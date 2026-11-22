# Artiko API

FastAPI backend for the Artiko Android and web apps. The contract is [`openapi.yml`](openapi.yml); the plan is in [`docs/plans`](docs/plans).

## Setup

Install Go Task and Docker with Compose v2.24 or newer, then run `task up`. Docker builds the API image with the locked dependencies and CLIP model, starts a local PostgreSQL database, applies migrations, and waits for the API and its database connection to be ready at `http://127.0.0.1:8000/api/v1/ready`.

If port 8000 is in use, run `ARTIKO_PORT=8001 task up` to serve the API on port 8001.

The stack works without a `.env` file. To use Neon, set `DATABASE_URL` to your development database in `.env`. Run `task seed` after startup to recreate the demo artists and index known works in the configured database. `task down` stops the stack and keeps the local database and uploads in Docker volumes.

For auto-reload outside Docker, install `uv`, configure `DATABASE_URL` for a database reachable from your host, then run `uv sync`, `uv run alembic upgrade head`, `uv run python -m scripts.download_model`, and `task run`.

## Tests

With `uv` installed, run lint and the full test suite with `task` (or `task check`).

```sh
task lint                          # Ruff only
task test                          # Full pytest suite
task test -- tests/test_health.py  # Run a single test file
```

Set `TEST_DATABASE_URL` in `.env` or in your shell to a dedicated PostgreSQL test database. The suite applies migrations and truncates tables between tests. It uses a local JWT key and sets `EMBED_PROVIDER=none`.

Set `TEST_RATE_LIMIT_REDIS_URL` to a dedicated Redis test instance to run the shared rate limiter integration test.

## Deploy (Render)

`render.yaml` describes the service: Docker runtime, free plan, Frankfurt, health check on `/api/v1/health`. The image bakes in the CLIP model, and each start runs `alembic upgrade head` before uvicorn. In Render: **New > Blueprint**, pick this repository, then fill in the variables it asks for:

| Variable | Value |
|---|---|
| `DATABASE_URL` | Neon pooled connection string (database `artiko`) |
| `AUTH_JWKS_URL` | `https://<web app>/api/auth/jwks` |
| `AUTH_ISSUER`, `AUTH_AUDIENCE`, `PUBLIC_WEB_URL`, `CORS_ORIGINS` | `https://<web app>` |
| `PUBLIC_API_URL` | `https://<this service>.onrender.com` |
| `MEDIA_BUCKET`, `AWS_ENDPOINT_URL_S3`, `AWS_REGION`, `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY` | Neon Object Storage bucket and its storage credential |

Proof files are private by default (`proof_visibility=reviewers` when publishing; `public` shows them to everyone). Private proofs are stored under the `private/` prefix of the bucket or of `MEDIA_DIR`, which `/media` never serves, and are opened only through signed links that expire after `PROOF_URL_TTL_SECONDS` (300). `WorkOut.proof_urls` lists the links the caller may use: the owner and users with the `reviewer` role get the private ones, everybody else only the public ones. Set `PROOF_URL_SECRET` (a long random string); without it no private link is issued. `GET /proofs/{id}/file` is public, since the signature is the credential, and is rate limited by `PROOF_RATE_LIMIT_PER_MINUTE` (60) and `PROOF_GLOBAL_RATE_LIMIT_PER_MINUTE` (600).

Publishing checks every image for copies. Its CLIP vector must stay under `PUBLISH_DUPLICATE_THRESHOLD` (0.92) of other artists' works and of known works, and its perceptual hash (pHash, 64 bits) within `PUBLISH_PHASH_MAX_DISTANCE` (8) bits is also a copy: the answer is 409 `possible_copy` with the id of a new review case. Similar to the artist's own earlier work: accepted, and both works get the same `series_id`. The CLIP signal catches crops and photos taken at an angle; with `EMBED_PROVIDER=none` (tests) only the exact and pHash signals work.

Captures. Only proof captured in the app counts for the human-made badge. A phone first registers the P-256 key it made in the Android Keystore: `GET /devices/challenge` gives a nonce (signed with `NONCE_SECRET`, valid 10 minutes), the key is created with that nonce as attestation challenge, and `POST /devices` sends the public key and the attestation chain. With `ATTESTATION_PROVIDER=chain` the server checks the chain up to Google's roots (`app/features/devices/google_attestation_roots.pem`, from `android.googleapis.com/attestation/root`), that the leaf is the registered key, that its challenge is the nonce and that the key is hardware-backed. Each proof file is then sent with a claim (`proof_claims`) that holds the signature over `<sha256>|<captured_at_ms>|<draft_id>|<nonce>`. The server refuses a bad signature, an unknown device, a capture older than `CAPTURE_MAX_AGE_MINUTES` (10), a capture dated in the future and a failed integrity verdict. Proof picked from the gallery is stored as `imported` and never counts. `INTEGRITY_PROVIDER` only knows `none` for now: the Play Integrity provider needs a Google Cloud project and is not implemented, so no real integrity verdict is checked yet. Works published before this rule keep their badge. `ALLOW_IMPORTED_PROOFS_FOR_BADGE` is a transition switch and is `true` by default, because the Android app cannot capture signed proof yet: imported proof files still count, as before. Set it to `false` to enforce the rule above.

`POST /scan/image` is public and runs the CLIP model, so it takes photos up to 5 MB (`SCAN_MAX_UPLOAD_MB`) and is rate limited: 20 a minute per client (`SCAN_RATE_LIMIT_PER_MINUTE`) and 120 a minute for everyone (`SCAN_GLOBAL_RATE_LIMIT_PER_MINUTE`). A client is its IPv4 address or IPv6 /64 network. Behind a proxy the socket peer is the proxy itself, so set `CLIENT_IP_HEADER` to the header your host overwrites with the real client IP (never one a client can send); until then every client shares one counter and the per-client limit behaves like the global one.

With `RATE_LIMIT_REDIS_URL` unset, the scan, signed proof download, and device endpoints keep process-local counters. For multiple API instances, set the same Redis URL on each instance (in Render, add it in the service's Environment settings), for example `rediss://<host>:6379/0` for a TLS-enabled Redis service. Use a separate Redis database or instance per API environment so its counters stay independent. The limited endpoints return `503 rate_limit_unavailable` until a configured Redis service is reachable.

Uploads go to the bucket when `MEDIA_BUCKET` is set (local `MEDIA_DIR` otherwise) and are served by the API at `/media/<sha256>.<ext>`. A free instance sleeps after 15 idle minutes: open `/api/v1/health` a minute before a demo.

## Layout

One deployable FastAPI app, organised as a modular monolith.

- `app/platform/`: infrastructure with no feature knowledge: config, database, errors, JWT auth, file storage, CLIP embeddings, contest months. `registry.py` imports every feature's tables for Alembic.
- `app/features/<feature>/`: one package per feature (`artists`, `works`, `votes`, `practices`, `scan`, `museum`, `me`, `health`). Each holds the files it needs: `models.py` (tables), `schemas.py` (API shapes), `service.py` (rules), `queries.py` (reads), `router.py` (thin HTTP layer under `/api/v1`).
- `app/main.py`: `create_app()` wires the routers.
- `alembic/versions/`: one migration per table.
- `scripts/`: model download, demo seed, known-works index.

Dependency rule, checked by `tests/test_architecture.py`: platform never imports a feature (except `registry.py`), and a feature never imports another feature's `router`. Features share code through `schemas`, `queries` and `service` modules only. The allowed direction is listed in that test: `artists` and `works` (which owns the `Vote` table) are the base, and `votes`, `museum`, `scan`, `practices` and `me` build on them. Services raise typed errors (`NotFound`, `Conflict`...) from `app/platform/errors.py`; the status code is mapped in one handler. The database engine, JWKS client and similarity index are created on first use (`get_engine`, `get_jwks`, `get_index`), never at import.
