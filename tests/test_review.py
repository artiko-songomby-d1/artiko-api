from tests.conftest import become_artist
from tests.test_copy_detection import art, count, encode, post_work


def refuse_copy(client, user_id: str = "bob", size: int = 128) -> tuple[bytes, str]:
    """Alice publishes a painting, then `user_id` tries a resized copy: the file and the case it opened."""
    if not count("SELECT count(*) FROM app.work"):
        become_artist(client, "alice")
        assert post_work(client, "alice", encode(art(1))).status_code == 201
    become_artist(client, user_id, user_id.title())
    copy = encode(art(1).resize((size, size)))
    refused = post_work(client, user_id, copy)
    assert refused.status_code == 409, refused.text
    return copy, refused.json()["error"]["case_id"]


def test_retrying_a_refused_file_reuses_the_open_case(client):
    copy, case_id = refuse_copy(client)
    again = post_work(client, "bob", copy)
    assert again.status_code == 409 and again.json()["error"]["case_id"] == case_id
    assert count("SELECT count(*) FROM app.review_case") == 1
