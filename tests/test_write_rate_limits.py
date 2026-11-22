import io
import uuid

import pytest
from PIL import Image
from sqlalchemy import create_engine, text

from app.platform.config import get_settings
from tests.conftest import auth, become_artist, publish, sketch

RETRY = "Retry-After"


def vote(client, user_id: str):
    """A vote for a work that does not exist: it costs a request, whatever the answer."""
    return client.post("/api/v1/votes", json={"work_id": str(uuid.uuid4())}, headers=auth(user_id))


def rename(client, user_id: str):
    return client.put("/api/v1/me/artist", json={"display_name": "Rova Arts"}, headers=auth(user_id))


def expose(client, user_id: str):
    return client.post("/api/v1/expositions", json={"title": "Highland light"}, headers=auth(user_id))


def work(client, user_id: str, seed: int = 1):
    return publish(client, user_id, seed, proofs=0)


def assert_rate_limited(response) -> None:
    assert response.status_code == 429, response.text
    assert response.json()["error"]["code"] == "rate_limited"
    assert int(response.headers[RETRY]) >= 1


def test_publishing_is_rate_limited_for_each_user(client, monkeypatch):
    monkeypatch.setattr(get_settings(), "work_rate_limit_per_minute", 2)
    become_artist(client, "alice")
    assert [work(client, "alice", seed).status_code for seed in (1, 2)] == [201, 201]
    assert_rate_limited(work(client, "alice", 3))
    # Same address, another account: the limit follows the user, so people behind one network do not share it.
    # Bob's request reaches the publication rules (he has no artist profile) instead of the limit.
    assert work(client, "bob", 4).json()["error"]["code"] == "not_an_artist"


def test_a_refused_publication_counts_towards_the_limit(client, monkeypatch):
    monkeypatch.setattr(get_settings(), "work_rate_limit_per_minute", 2)
    assert [work(client, "alice", seed).status_code for seed in (1, 2)] == [403, 403]  # not an artist
    assert_rate_limited(work(client, "alice", 3))


def test_publishing_has_a_global_cap_that_changing_account_cannot_get_around(client, monkeypatch):
    monkeypatch.setattr(get_settings(), "work_global_rate_limit_per_minute", 2)
    assert [work(client, name).status_code for name in ("alice", "bob")] == [403, 403]
    assert_rate_limited(work(client, "carol"))


@pytest.mark.parametrize(
    ("send", "setting", "allowed"),
    [
        (vote, "vote_rate_limit_per_minute", 404),
        (rename, "artist_rate_limit_per_minute", 200),
        (expose, "exposition_rate_limit_per_minute", 403),
    ],
)
def test_votes_profile_updates_and_expositions_are_rate_limited_for_each_user(
    client, monkeypatch, send, setting: str, allowed: int
):
    monkeypatch.setattr(get_settings(), setting, 2)
    assert [send(client, "alice").status_code for _ in range(2)] == [allowed, allowed]
    assert_rate_limited(send(client, "alice"))
    assert send(client, "bob").status_code == allowed


@pytest.mark.parametrize(
    ("send", "setting", "allowed"),
    [
        (vote, "vote_global_rate_limit_per_minute", 404),
        (rename, "artist_global_rate_limit_per_minute", 200),
        (expose, "exposition_global_rate_limit_per_minute", 403),
    ],
)
def test_votes_profile_updates_and_expositions_have_a_global_cap(client, monkeypatch, send, setting: str, allowed: int):
    monkeypatch.setattr(get_settings(), setting, 2)
    assert [send(client, name).status_code for name in ("alice", "bob")] == [allowed, allowed]
    assert_rate_limited(send(client, "carol"))


def test_requests_without_a_token_are_refused_and_use_nobodys_budget(client, monkeypatch):
    monkeypatch.setattr(get_settings(), "vote_rate_limit_per_minute", 1)
    monkeypatch.setattr(get_settings(), "vote_global_rate_limit_per_minute", 1)
    anonymous = [client.post("/api/v1/votes", json={"work_id": str(uuid.uuid4())}) for _ in range(3)]
    assert [r.status_code for r in anonymous] == [401, 401, 401]
    assert vote(client, "alice").status_code == 404


def test_the_default_limits_leave_room_for_an_honest_session(client):
    """Nobody publishes, votes or edits a profile faster than this by hand; the limits must not be felt."""
    become_artist(client, "alice")
    assert {work(client, "alice", seed).status_code for seed in range(8)} == {201}
    assert {vote(client, "alice").status_code for _ in range(16)} == {404}  # two tries in each of the 8 categories
    assert {rename(client, "alice").status_code for _ in range(5)} == {200}
    assert {expose(client, "alice").status_code for _ in range(5)} == {201}


# --- review cases opened by refused copies ---


def encode(img: Image.Image, fmt: str = "PNG", **options) -> bytes:
    buf = io.BytesIO()
    img.save(buf, fmt, **options)
    return buf.getvalue()


ORIGINAL = Image.open(io.BytesIO(sketch(7, size=256)))
# The same picture in other sizes: other bytes (another SHA-256), still a copy for the perceptual hash.
COPIES = [encode(ORIGINAL.resize((side, side))) for side in (128, 160, 192, 224)]


def post_work(client, user_id: str, data: bytes):
    files = [("file", ("work.png", data, "image/png"))]
    return client.post(
        "/api/v1/works", data={"title": "Study", "category": "painting"}, files=files, headers=auth(user_id)
    )


def refused_case(client, user_id: str, data: bytes) -> str:
    response = post_work(client, user_id, data)
    assert response.status_code == 409, response.text
    error = response.json()["error"]
    assert error["code"] == "possible_copy"
    return error["case_id"]


def cases(where: str = "TRUE") -> int:
    engine = create_engine(get_settings().async_database_url)
    with engine.begin() as conn:
        value = conn.execute(text(f"SELECT count(*) FROM app.review_case WHERE {where}")).scalar_one()  # noqa: S608
    engine.dispose()
    return value


def close_cases() -> None:
    engine = create_engine(get_settings().async_database_url)
    with engine.begin() as conn:
        conn.execute(text("UPDATE app.review_case SET status = 'closed'"))
    engine.dispose()


@pytest.fixture
def original(client):
    become_artist(client, "alice")
    become_artist(client, "bob", "Bob")
    become_artist(client, "carol", "Carol")
    assert post_work(client, "alice", encode(ORIGINAL)).status_code == 201


def test_sending_the_same_refused_file_again_reuses_its_open_review_case(client, original):
    first = refused_case(client, "bob", COPIES[0])
    assert [refused_case(client, "bob", COPIES[0]) for _ in range(3)] == [first] * 3
    assert cases() == 1


def test_another_refused_file_and_another_artist_get_their_own_review_case(client, original):
    first = refused_case(client, "bob", COPIES[0])
    assert refused_case(client, "bob", COPIES[1]) != first
    assert refused_case(client, "carol", COPIES[0]) != first
    assert cases() == 3


def test_a_refused_file_gets_a_new_review_case_once_the_first_one_is_closed(client, original):
    first = refused_case(client, "bob", COPIES[0])
    close_cases()
    assert refused_case(client, "bob", COPIES[0]) != first
    assert cases("status = 'open'") == 1


def test_an_artist_cannot_pile_up_open_review_cases_by_altering_the_file(client, original, monkeypatch):
    monkeypatch.setattr(get_settings(), "review_max_open_cases_per_artist", 2)
    opened = [refused_case(client, "bob", data) for data in COPIES[:2]]
    assert len(set(opened)) == 2
    # Past the cap no row is added: the answer names the artist's latest open case.
    assert [refused_case(client, "bob", data) for data in COPIES[2:]] == [opened[1]] * 2
    assert cases() == 2
    # The cap is per artist: somebody else's refused copy still opens a case.
    assert refused_case(client, "carol", COPIES[2]) not in opened
    assert cases() == 3
