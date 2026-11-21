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

One deployable FastAPI app, organised as a modular monolith.

- `app/platform/`: infrastructure with no feature knowledge: config, database, errors, JWT auth, file storage, CLIP embeddings, contest months. `registry.py` imports every feature's tables for Alembic.
- `app/features/<feature>/`: one package per feature (`artists`, `works`, `votes`, `practices`, `scan`, `museum`, `me`, `health`). Each holds the files it needs: `models.py` (tables), `schemas.py` (API shapes), `service.py` (rules), `queries.py` (reads), `router.py` (thin HTTP layer under `/api/v1`).
- `app/main.py`: `create_app()` wires the routers.
- `alembic/versions/`: one migration per table.
- `scripts/`: model download, demo seed, known-works index.

Dependency rule, checked by `tests/test_architecture.py`: platform never imports a feature (except `registry.py`), and a feature never imports another feature's `router`. Features share code through `schemas`, `queries` and `service` modules only. Order: `artists` and `works` first, then `votes`, `museum`, `scan` which build on them.
