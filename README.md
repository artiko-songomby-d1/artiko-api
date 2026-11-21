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

## Deploy (Render)

`render.yaml` describes the service: Docker runtime, free plan, Frankfurt, health check on `/api/v1/health`. The image bakes in the CLIP model, and each start runs `alembic upgrade head` before uvicorn. In Render: **New > Blueprint**, pick this repository, then fill in the variables it asks for:

| Variable | Value |
|---|---|
| `DATABASE_URL` | Neon pooled connection string (database `artiko`) |
| `AUTH_JWKS_URL` | `https://<web app>/api/auth/jwks` |
| `AUTH_ISSUER`, `AUTH_AUDIENCE`, `PUBLIC_WEB_URL`, `CORS_ORIGINS` | `https://<web app>` |
| `PUBLIC_API_URL` | `https://<this service>.onrender.com` |
| `MEDIA_BUCKET`, `AWS_ENDPOINT_URL_S3`, `AWS_REGION`, `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY` | Neon Object Storage bucket and its storage credential |

`POST /scan/image` is public and runs the CLIP model, so it takes photos up to 5 MB (`SCAN_MAX_UPLOAD_MB`) and is rate limited: 20 a minute per client (`SCAN_RATE_LIMIT_PER_MINUTE`) and 120 a minute for everyone (`SCAN_GLOBAL_RATE_LIMIT_PER_MINUTE`). Counts are kept in memory per instance. A client is its IPv4 address or IPv6 /64 network. Behind a proxy the socket peer is the proxy itself, so set `CLIENT_IP_HEADER` to the header your host overwrites with the real client IP (never one a client can send); until then every client shares one counter and the per-client limit behaves like the global one.

Uploads go to the bucket when `MEDIA_BUCKET` is set (local `MEDIA_DIR` otherwise) and are served by the API at `/media/<sha256>.<ext>`. A free instance sleeps after 15 idle minutes: open `/api/v1/health` a minute before a demo.

## Layout

One deployable FastAPI app, organised as a modular monolith.

- `app/platform/`: infrastructure with no feature knowledge: config, database, errors, JWT auth, file storage, CLIP embeddings, contest months. `registry.py` imports every feature's tables for Alembic.
- `app/features/<feature>/`: one package per feature (`artists`, `works`, `votes`, `practices`, `scan`, `museum`, `me`, `health`). Each holds the files it needs: `models.py` (tables), `schemas.py` (API shapes), `service.py` (rules), `queries.py` (reads), `router.py` (thin HTTP layer under `/api/v1`).
- `app/main.py`: `create_app()` wires the routers.
- `alembic/versions/`: one migration per table.
- `scripts/`: model download, demo seed, known-works index.

Dependency rule, checked by `tests/test_architecture.py`: platform never imports a feature (except `registry.py`), and a feature never imports another feature's `router`. Features share code through `schemas`, `queries` and `service` modules only. The allowed direction is listed in that test: `artists` and `works` (which owns the `Vote` table) are the base, and `votes`, `museum`, `scan`, `practices` and `me` build on them. Services raise typed errors (`NotFound`, `Conflict`...) from `app/platform/errors.py`; the status code is mapped in one handler. The database engine, JWKS client and similarity index are created on first use (`get_engine`, `get_jwks`, `get_index`), never at import.
