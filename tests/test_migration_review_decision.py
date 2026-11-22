import subprocess
import sys
from pathlib import Path

import pytest
from sqlalchemy import create_engine, text

from app.platform.config import get_settings
from tests.conftest import become_artist

SHA = "a" * 64
PROJECT = Path(__file__).parents[1]


def downgrade_to_0016() -> None:
    subprocess.run([sys.executable, "-m", "alembic", "downgrade", "0016"], check=True, cwd=PROJECT)


def upgrade_to_head() -> None:
    subprocess.run([sys.executable, "-m", "alembic", "upgrade", "head"], check=True, cwd=PROJECT)


def query(sql: str, **params) -> list:
    engine = create_engine(get_settings().async_database_url)
    with engine.begin() as conn:
        rows = conn.execute(text(sql), params).all()
    engine.dispose()
    return rows


@pytest.fixture
def before_review_decision():
    downgrade_to_0016()
    try:
        yield
    finally:
        upgrade_to_head()


def insert_open_case(artist_id: str, created_at: str) -> str:
    return query(
        "INSERT INTO app.review_case (id, reason, status, artist_id, candidate_sha256, created_at)"
        " VALUES (gen_random_uuid(), 'possible_copy', 'open', :artist_id, :sha, :created_at) RETURNING id",
        artist_id=artist_id,
        sha=SHA,
        created_at=created_at,
    )[0][0]


def test_upgrade_keeps_the_oldest_of_duplicate_open_cases_and_can_be_replayed(client, before_review_decision):
    artist_id = become_artist(client, "bob", "Bob")
    oldest = insert_open_case(artist_id, "2026-01-01T00:00:00Z")
    insert_open_case(artist_id, "2026-01-02T00:00:00Z")

    upgrade_to_head()
    assert query("SELECT id FROM app.review_case") == [(oldest,)]

    downgrade_to_0016()
    upgrade_to_head()
    assert query("SELECT id FROM app.review_case") == [(oldest,)]
