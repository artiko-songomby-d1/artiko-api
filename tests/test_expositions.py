from tests.conftest import auth, become_artist


def create(client, user_id: str, title: str = "Highland light", **body):
    return client.post("/api/v1/expositions", json={"title": title, **body}, headers=auth(user_id))


def test_artist_creates_an_exposition(client):
    artist_id = become_artist(client, "alice")
    r = create(client, "alice", description="Red earth at dusk")
    assert r.status_code == 201, r.text
    expo = r.json()
    assert expo["artist_id"] == artist_id and expo["artist_name"] == "Rova Arts"
    assert expo["title"] == "Highland light" and expo["description"] == "Red earth at dusk"
    assert expo["cover_url"] is None and expo["works_count"] == 0
    assert client.get(f"/api/v1/expositions/{expo['id']}").json() == expo


def test_only_artists_create(client):
    r = create(client, "bob")
    assert r.status_code == 403
    assert r.json()["error"]["code"] == "not_an_artist"


def test_needs_a_title(client):
    become_artist(client, "alice")
    assert create(client, "alice", title="").status_code == 422
    assert client.post("/api/v1/expositions", json={"title": "X"}).status_code == 401


def test_list_newest_first_and_filtered_by_artist(client):
    alice = become_artist(client, "alice")
    become_artist(client, "bob", "Bob")
    create(client, "alice", "First")
    create(client, "bob", "Bob's")
    create(client, "alice", "Second")
    assert [e["title"] for e in client.get("/api/v1/expositions").json()] == ["Second", "Bob's", "First"]
    assert [e["title"] for e in client.get(f"/api/v1/expositions?artist_id={alice}").json()] == ["Second", "First"]


def test_unknown_exposition_is_404(client):
    r = client.get("/api/v1/expositions/00000000-0000-0000-0000-000000000000")
    assert r.status_code == 404
    assert r.json()["error"]["code"] == "exposition_not_found"
