"""Only accounts with a verified email may vote, when the server asks for it."""

import pytest

from app.platform.config import get_settings
from tests.conftest import become_artist, make_token, publish


def vote(client, work_id: str, voter: str = "bob", **claims):
    headers = {"Authorization": f"Bearer {make_token(voter, **claims)}"}
    return client.post("/api/v1/votes", json={"work_id": work_id}, headers=headers)


@pytest.fixture
def work_id(client) -> str:
    become_artist(client, "alice")
    return publish(client, "alice", 1).json()["id"]


@pytest.fixture
def verified_email_required(monkeypatch):
    monkeypatch.setattr(get_settings(), "require_verified_email_to_vote", True)


def test_by_default_an_unverified_account_can_still_vote(client, work_id):
    assert vote(client, work_id).status_code == 201


def test_an_unverified_account_cannot_vote_when_the_server_requires_it(client, work_id, verified_email_required):
    r = vote(client, work_id)
    assert r.status_code == 403 and r.json()["error"]["code"] == "email_not_verified"
    assert client.get(f"/api/v1/works/{work_id}").json()["votes"] == 0


def test_a_verified_account_votes_when_the_server_requires_it(client, work_id, verified_email_required):
    assert vote(client, work_id, emailVerified=True).status_code == 201
    assert client.get(f"/api/v1/works/{work_id}").json()["votes"] == 1


def test_only_the_boolean_true_counts_as_verified(client, work_id, verified_email_required):
    for claim in (False, "true", 1, None):
        r = vote(client, work_id, emailVerified=claim)
        assert r.status_code == 403 and r.json()["error"]["code"] == "email_not_verified", claim


def test_the_rule_does_not_touch_the_other_routes(client, work_id, verified_email_required):
    headers = {"Authorization": f"Bearer {make_token('bob')}"}
    assert client.get("/api/v1/me", headers=headers).status_code == 200
    assert client.get("/api/v1/votes/mine", headers=headers).status_code == 200
    assert client.put("/api/v1/me/artist", json={"display_name": "Bob"}, headers=headers).status_code == 200
