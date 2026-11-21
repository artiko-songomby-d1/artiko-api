import time
from urllib.parse import parse_qs, urlparse

from app.platform import signed_links, storage
from tests.conftest import auth, become_artist, make_token, png, publish


def reviewer(user_id: str = "rev") -> dict[str, str]:
    return {"Authorization": f"Bearer {make_token(user_id, role='reviewer')}"}


def proof_name(seed: int, index: int) -> str:
    """The stored name of the index-th proof that `publish` sends for this seed."""
    return f"{storage.sha256(png(1000 + seed * 10 + index))}.png"


def path_of(url: str) -> str:
    parts = urlparse(url)
    return f"{parts.path}?{parts.query}"


def test_proofs_are_private_by_default_but_still_count_for_the_badge(client):
    become_artist(client, "alice")
    work = publish(client, "alice", 1).json()
    assert work["human_proven"] is True
    assert len(work["proof_urls"]) == 2  # the owner sees their own links
    anonymous = client.get(f"/api/v1/works/{work['id']}").json()
    assert anonymous["proof_urls"] == [] and anonymous["human_proven"] is True


def test_the_owner_opens_a_private_proof_through_a_signed_link(client):
    become_artist(client, "alice")
    work = publish(client, "alice", 1).json()
    seen = client.get(f"/api/v1/works/{work['id']}", headers=auth("alice")).json()["proof_urls"]
    assert len(seen) == 2
    r = client.get(path_of(seen[0]))
    assert r.status_code == 200 and r.content == png(1010)
    assert r.headers["cache-control"] == "private, no-store"


def test_a_private_proof_is_never_served_at_media(client):
    become_artist(client, "alice")
    publish(client, "alice", 1)
    assert client.get(f"/media/{proof_name(1, 0)}").status_code == 404
    assert client.get(f"/media/private/{proof_name(1, 0)}").status_code == 404


def test_other_users_and_visitors_get_no_private_link(client):
    become_artist(client, "alice")
    work = publish(client, "alice", 1).json()
    assert client.get(f"/api/v1/works/{work['id']}", headers=auth("bob")).json()["proof_urls"] == []
    assert client.get("/api/v1/works", headers=auth("bob")).json()[0]["proof_urls"] == []
    assert client.get(f"/api/v1/works/{work['id']}/proofs", headers=auth("bob")).json() == []


def test_reviewers_see_private_proofs(client):
    become_artist(client, "alice")
    work = publish(client, "alice", 1).json()
    assert len(client.get(f"/api/v1/works/{work['id']}", headers=reviewer()).json()["proof_urls"]) == 2


def test_the_proofs_endpoint_needs_a_sign_in_and_renews_links(client):
    become_artist(client, "alice")
    work = publish(client, "alice", 1).json()
    assert client.get(f"/api/v1/works/{work['id']}/proofs").status_code == 401
    proofs = client.get(f"/api/v1/works/{work['id']}/proofs", headers=auth("alice")).json()
    assert [p["visibility"] for p in proofs] == ["reviewers", "reviewers"]
    assert client.get(path_of(proofs[0]["url"])).status_code == 200
    unknown = client.get("/api/v1/works/00000000-0000-0000-0000-000000000000/proofs", headers=auth("alice"))
    assert unknown.status_code == 404


def test_a_tampered_or_expired_link_is_refused(client):
    become_artist(client, "alice")
    work = publish(client, "alice", 1).json()
    url = client.get(f"/api/v1/works/{work['id']}", headers=auth("alice")).json()["proof_urls"][0]
    proof_id = urlparse(url).path.split("/")[-2]
    query = parse_qs(urlparse(url).query)
    bad = client.get(f"/api/v1/proofs/{proof_id}/file?exp={query['exp'][0]}&sig={'0' * 64}")
    assert bad.status_code == 403 and bad.json()["error"]["code"] == "invalid_link"
    past = int(time.time()) - 10
    expired = client.get(f"/api/v1/proofs/{proof_id}/file?exp={past}&sig={signed_links._signature(proof_id, past)}")
    assert expired.status_code == 403


def test_public_proofs_are_shown_to_everyone_and_served_at_media(client):
    become_artist(client, "alice")
    work = publish(client, "alice", 1, proof_visibility="public").json()
    urls = client.get(f"/api/v1/works/{work['id']}").json()["proof_urls"]
    assert len(urls) == 2 and all("/media/" in u for u in urls)
    assert client.get(f"/media/{proof_name(1, 0)}").status_code == 200


def test_the_visibility_must_be_a_known_one(client):
    become_artist(client, "alice")
    assert publish(client, "alice", 1, proof_visibility="everyone").status_code == 422
