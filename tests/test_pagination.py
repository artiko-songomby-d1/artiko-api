from tests.conftest import become_artist, publish


def test_works_are_paged_newest_first_without_overlap(client):
    become_artist(client, "alice")
    for seed in range(1, 6):
        publish(client, "alice", seed)
    everything = [w["id"] for w in client.get("/api/v1/works").json()]
    first = [w["id"] for w in client.get("/api/v1/works?limit=2").json()]
    second = [w["id"] for w in client.get("/api/v1/works?limit=2&offset=2").json()]
    assert len(everything) == 5
    assert first + second == everything[:4]


def test_artists_are_paged(client):
    for name in ("alice", "bob", "carol"):
        become_artist(client, name, name.title())
    names = [a["display_name"] for a in client.get("/api/v1/artists?limit=2&offset=1").json()]
    assert names == ["Bob", "Carol"]


def test_paging_bounds_are_validated(client):
    for query in ("limit=0", "limit=201", "offset=-1"):
        r = client.get(f"/api/v1/works?{query}")
        assert r.status_code == 422 and r.json()["error"]["code"] == "invalid_request"
