import asyncio

from app.core.db import SessionLocal
from app.models import Practice
from app.modules.practices import status


def test_status_rule():
    assert status(practitioners=100, learners=40, median_age=40) == "red"  # too few learners
    assert status(practitioners=100, learners=200, median_age=65) == "red"  # ageing masters
    assert status(practitioners=100, learners=70, median_age=40) == "orange"
    assert status(practitioners=100, learners=100, median_age=40) == "green"


def test_list_most_endangered_first(client):
    async def seed():
        async with SessionLocal() as db:
            db.add_all([
                Practice(id="antemoro-paper", name="Antemoro paper", region="Madagascar", category="crafts",
                         practitioners=60, learners=70, median_age=40, description=""),
                Practice(id="kabary", name="Kabary", region="Madagascar", category="oral_arts",
                         practitioners=120, learners=30, median_age=63, description="Malagasy oratory"),
            ])
            await db.commit()

    asyncio.run(seed())
    rows = client.get("/api/v1/practices").json()
    assert [(p["id"], p["status"]) for p in rows] == [("kabary", "red"), ("antemoro-paper", "green")]
    assert client.get("/api/v1/practices/kabary").json()["category"] == "oral_arts"
    assert client.get("/api/v1/practices/nope").status_code == 404
