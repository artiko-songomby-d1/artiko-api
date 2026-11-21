import uuid

from tests.conftest import auth

PROFILE = {"display_name": "Rova Arts", "bio": "Lamba weaver", "region": "Analamanga"}


def test_become_artist_then_update(client):
    r = client.put("/api/v1/me/artist", json=PROFILE, headers=auth("alice"))
    assert r.status_code == 200
    artist_id = r.json()["id"]

    r = client.put("/api/v1/me/artist", json={**PROFILE, "bio": "Silk"}, headers=auth("alice"))
    assert r.json()["id"] == artist_id
    assert r.json()["bio"] == "Silk"

    me = client.get("/api/v1/me", headers=auth("alice")).json()
    assert me["artist"]["id"] == artist_id


def test_me_without_artist(client):
    assert client.get("/api/v1/me", headers=auth("bob")).json()["artist"] is None


def test_list_and_read(client):
    artist_id = client.put("/api/v1/me/artist", json=PROFILE, headers=auth("alice")).json()["id"]
    assert [a["id"] for a in client.get("/api/v1/artists").json()] == [artist_id]
    assert client.get(f"/api/v1/artists/{artist_id}").json()["display_name"] == "Rova Arts"


def test_read_unknown_artist(client):
    r = client.get(f"/api/v1/artists/{uuid.uuid4()}")
    assert r.status_code == 404
    assert r.json()["error"]["code"] == "artist_not_found"


def test_display_name_required(client):
    r = client.put("/api/v1/me/artist", json={**PROFILE, "display_name": ""}, headers=auth("alice"))
    assert r.status_code == 422
