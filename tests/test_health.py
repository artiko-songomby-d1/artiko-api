def test_ready_checks_the_database(client):
    assert client.get("/api/v1/ready").json() == {"status": "ok"}


def test_ready_answers_503_without_leaking_the_cause(client, monkeypatch):
    from sqlalchemy.ext.asyncio import AsyncSession

    async def down(*_, **__):
        raise ConnectionError("password authentication failed for user artiko")

    monkeypatch.setattr(AsyncSession, "execute", down)
    r = client.get("/api/v1/ready")
    assert r.status_code == 503
    assert r.json() == {"error": {"code": "not_ready", "message": "The database is not reachable"}}


def test_health_does_not_need_the_database(client):
    assert client.get("/api/v1/health").json() == {"status": "ok"}
