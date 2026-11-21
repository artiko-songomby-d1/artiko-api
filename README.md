# Artiko API

FastAPI backend for the Artiko Android and web apps. The contract is [`openapi.yml`](openapi.yml); the plan is in [`docs/plans`](docs/plans).

## Setup

1. Create two Neon databases (dev and test) and copy `.env.example` to `.env` with their URLs in `DATABASE_URL` and `TEST_DATABASE_URL`.
2. Install, migrate, get the CLIP model, seed:

```
uv sync
uv run alembic upgrade head
uv run python -m scripts.download_model
uv run python -m scripts.seed_demo
uv run python -m scripts.index_known --met 200
uv run uvicorn app.main:app --reload
```

## Tests

```
uv run ruff check .
uv run pytest
```

Tests run on `TEST_DATABASE_URL` (every table is truncated between tests) and never call the network: the JWT key is generated locally and embeddings use `EMBED_PROVIDER=none`.

## Layout

- `app/core/`: config, database, errors, JWT check, storage, CLIP embeddings, similarity index, C2PA check.
- `app/modules/`: one router per feature under `/api/v1`.
- `app/models.py`: every table (schema `app`); Better Auth owns schema `better_auth`.
- `alembic/versions/`: one migration per table.
- `scripts/`: model download, demo seed, known-works index.
