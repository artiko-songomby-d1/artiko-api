from app.models import Work
from app.modules.works import current_month
from tests.conftest import auth, become_artist, publish


def test_vote_and_count(client):
    become_artist(client, "alice")
    work = publish(client, "alice", 1).json()
    r = client.post("/api/v1/votes", json={"work_id": work["id"]}, headers=auth("bob"))
    assert r.status_code == 201
    assert r.json() == {"work_id": work["id"], "category": "painting", "month": current_month()}
    assert client.get(f"/api/v1/works/{work['id']}").json()["votes"] == 1
    assert len(client.get("/api/v1/votes/mine", headers=auth("bob")).json()) == 1


def test_one_vote_per_category_per_month(client):
    become_artist(client, "alice")
    a = publish(client, "alice", 1).json()
    b = publish(client, "alice", 2).json()
    c = publish(client, "alice", 3, category="crafts").json()
    assert client.post("/api/v1/votes", json={"work_id": a["id"]}, headers=auth("bob")).status_code == 201
    r = client.post("/api/v1/votes", json={"work_id": b["id"]}, headers=auth("bob"))
    assert r.status_code == 409 and r.json()["error"]["code"] == "already_voted"
    assert client.post("/api/v1/votes", json={"work_id": c["id"]}, headers=auth("bob")).status_code == 201


def test_vote_rules(client):
    become_artist(client, "alice")
    unproven = publish(client, "alice", 1, proofs=0).json()
    mine = publish(client, "alice", 2).json()
    r = client.post("/api/v1/votes", json={"work_id": unproven["id"]}, headers=auth("bob"))
    assert r.json()["error"]["code"] == "not_eligible"
    r = client.post("/api/v1/votes", json={"work_id": mine["id"]}, headers=auth("alice"))
    assert r.json()["error"]["code"] == "own_work"
    assert client.post("/api/v1/votes", json={"work_id": mine["id"]}).status_code == 401


def test_vote_on_last_month_work_refused(client):
    import asyncio

    from sqlalchemy import update

    from app.platform.db import SessionLocal

    become_artist(client, "alice")
    work = publish(client, "alice", 1).json()

    async def age():
        async with SessionLocal() as db:
            await db.execute(update(Work).where(Work.id == work["id"]).values(month="2000-01"))
            await db.commit()

    asyncio.run(age())
    r = client.post("/api/v1/votes", json={"work_id": work["id"]}, headers=auth("bob"))
    assert r.status_code == 400 and r.json()["error"]["code"] == "not_in_contest"


def test_hall_of_fame_top_5(client):
    become_artist(client, "alice")
    works = [publish(client, "alice", i).json() for i in range(7)]
    # work i gets i votes, from distinct voters
    for i, w in enumerate(works):
        for v in range(i):
            client.post("/api/v1/votes", json={"work_id": w["id"]}, headers=auth(f"voter{i}-{v}"))
    top = client.get(f"/api/v1/hall-of-fame/painting/{current_month()}").json()
    assert [e["votes"] for e in top] == [6, 5, 4, 3, 2]
    assert [e["rank"] for e in top] == [1, 2, 3, 4, 5]
    assert top[0]["work"]["id"] == works[6]["id"]
    assert client.get(f"/api/v1/hall-of-fame/crafts/{current_month()}").json() == []
    assert client.get("/api/v1/hall-of-fame/painting/2026-1").status_code == 422
