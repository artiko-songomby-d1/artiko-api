from tests.conftest import REVIEWER, auth, become_artist, make_token
from tests.test_copy_detection import art, count, encode, post_work

CASES = "/api/v1/review/cases"
UNKNOWN = "00000000-0000-0000-0000-000000000000"


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


def decide(client, case_id: str, decision: str, headers: dict[str, str]):
    return client.post(f"{CASES}/{case_id}/decision", json={"decision": decision}, headers=headers)


def test_the_review_queue_is_for_reviewers_only(client, reviewer):
    assert client.get(CASES).status_code == 401
    assert client.get(CASES, headers=auth("alice")).status_code == 403
    claimed = {"Authorization": f"Bearer {make_token('mallory', role='reviewer')}"}
    forbidden = client.get(CASES, headers=claimed)
    assert forbidden.status_code == 403 and forbidden.json()["error"]["code"] == "not_a_reviewer"
    assert decide(client, UNKNOWN, "cleared", auth("alice")).status_code == 403
    assert decide(client, UNKNOWN, "cleared", {}).status_code == 401


def test_a_refused_publication_is_listed_as_an_open_case(client, reviewer):
    _, case_id = refuse_copy(client)
    cases = client.get(CASES, headers=reviewer).json()
    assert [c["id"] for c in cases] == [case_id]
    case = cases[0]
    assert case["reason"] == "possible_copy" and case["status"] == "open"
    assert case["work_id"] is None and case["other_work_id"] and case["artist_id"]
    assert case["decided_by"] is None and case["decided_at"] is None
    assert client.get(CASES, params={"status": "cleared"}, headers=reviewer).json() == []
    assert client.get(CASES, params={"status": "closed"}, headers=reviewer).status_code == 422


def test_cases_are_paged_oldest_first(client, reviewer):
    _, first = refuse_copy(client, "bob")
    _, second = refuse_copy(client, "carol", size=100)
    assert [c["id"] for c in client.get(CASES, headers=reviewer).json()] == [first, second]
    page = client.get(CASES, params={"limit": 1, "offset": 1}, headers=reviewer).json()
    assert [c["id"] for c in page] == [second]


def test_retrying_a_refused_file_reuses_the_open_case(client):
    copy, case_id = refuse_copy(client)
    again = post_work(client, "bob", copy)
    assert again.status_code == 409 and again.json()["error"]["case_id"] == case_id
    assert count("SELECT count(*) FROM app.review_case") == 1


def test_a_cleared_file_can_be_published_by_its_artist(client, reviewer):
    copy, case_id = refuse_copy(client)
    decided = decide(client, case_id, "cleared", reviewer)
    assert decided.status_code == 200, decided.text
    body = decided.json()
    assert body["status"] == "cleared" and body["decided_by"] == REVIEWER and body["decided_at"]
    assert client.get(CASES, headers=reviewer).json() == []
    assert [c["id"] for c in client.get(CASES, params={"status": "cleared"}, headers=reviewer).json()] == [case_id]

    become_artist(client, "carol", "Carol")
    assert post_work(client, "carol", copy).status_code == 409  # cleared for Bob only
    assert post_work(client, "bob", copy).status_code == 201
    assert post_work(client, "bob", encode(art(1).resize((100, 100)))).status_code == 409  # another file


def test_a_confirmed_copy_stays_refused(client, reviewer):
    copy, case_id = refuse_copy(client)
    decided = decide(client, case_id, "confirmed", reviewer)
    assert decided.status_code == 200 and decided.json()["status"] == "confirmed"
    assert post_work(client, "bob", copy).status_code == 409


def test_only_an_open_case_can_be_decided(client, reviewer):
    _, case_id = refuse_copy(client)
    assert decide(client, case_id, "confirmed", reviewer).status_code == 200
    again = decide(client, case_id, "cleared", reviewer)
    assert again.status_code == 409 and again.json()["error"]["code"] == "case_already_decided"
    assert client.get(CASES, params={"status": "confirmed"}, headers=reviewer).json()[0]["status"] == "confirmed"
    missing = decide(client, UNKNOWN, "cleared", reviewer)
    assert missing.status_code == 404 and missing.json()["error"]["code"] == "case_not_found"
    assert decide(client, case_id, "maybe", reviewer).status_code == 422
