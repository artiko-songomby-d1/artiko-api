# Artiko MVP plan (API + web), 21 Nov 2026

Spec: `art-problems-research/SOLUTION-6.md` (sections 4, 12, 13.1). This plan covers the **API** (`artiko-api`) and the **web app** (`artiko-web`). The Android app (`artiko-mobile`) consumes the same API. **The contract is `artiko-api/openapi.yml`** (written first, so web and Android generate their clients and mock it before the code exists); `tests/test_contract.py` fails if the code serves a route the contract does not declare. The table below is a summary; the YAML wins.

## Goal

A visitor can check a photo (human-proven Artiko work / known work / AI-made / unknown), walk an artist's 3D museum and vote, see the month's top 5 per category in the Hall of Fame, and read the Living Heritage Index (kabary in red). An artist can sign up and publish a work with proof of process.

## Architecture

- **API:** FastAPI, async SQLAlchemy 2 (psycopg 3 async), Alembic, PostgreSQL on Neon (dev database + separate test database). Tables in schema `app`; Better Auth owns schema `better_auth`. Routes under `/api/v1`, one package per feature in `app/features/`, infrastructure in `app/platform/`.
- **Auth:** Better Auth in the Next.js app (email + password, `jwt` plugin, `bearer` plugin for Android). FastAPI verifies the JWT against `{web}/api/auth/jwks` (EdDSA). Android: `POST /api/auth/sign-in/email` → session token → `GET /api/auth/token` with `Authorization: Bearer <session>` → JWT for the API.
- **Scan:** CLIP ViT-B/32 image encoder (ONNX, quantized, `onnxruntime`) → 512-d normalized vector stored as `float4[]`; similarity by numpy dot product over an in-memory matrix loaded at startup (no pgvector on this machine; ~10k × 512 floats = 20 MB). C2PA manifest read with `c2pa-python`.
- **Files:** stored on local disk under `MEDIA_DIR`, served at `/media`. SHA-256 per file.

Decision: Better Auth instead of Keycloak (SOLUTION-6 names Keycloak) - same JWT/JWKS shape for the API, no extra container on a 7.4 GB machine; Keycloak can replace it by changing `AUTH_JWKS_URL`/issuer/audience only.
Decision: numpy similarity instead of pgvector - pgvector not installed; swap later by adding a `vector(512)` column.
Decision: local disk instead of MinIO - one less service for the demo.
Decision: no Museum table - the museum layout is computed from the artist's works (walls for 2D, plinths for 3D).
Decision: score = public votes only for the MVP; the 50 % jury share is a later task.

## API contract (shared with web and Android)

| Method | Path | Auth | Body / result |
|---|---|---|---|
| GET | `/api/v1/health` | - | `{status}` |
| GET | `/api/v1/me` | JWT | `{id, email, name, artist: Artist \| null}` |
| PUT | `/api/v1/me/artist` | JWT | `{display_name, bio, region}` → `Artist` (become/update artist) |
| GET | `/api/v1/artists` | - | `Artist[]` |
| GET | `/api/v1/artists/{id}` | - | `Artist` |
| GET | `/api/v1/artists/{id}/museum` | - | `{artist, slots: [{work, kind: "wall"\|"plinth", position:[x,y,z], rotation_y}]}` |
| GET | `/api/v1/works?category=&month=YYYY-MM&artist_id=` | - | `Work[]` |
| GET | `/api/v1/works/{id}` | - | `Work` |
| POST | `/api/v1/works` | JWT, artist | multipart: `title, story, category, ai_declared, file, proof[]` (0-5) → `Work` |
| POST | `/api/v1/votes` | JWT | `{work_id}` → `{work_id, category, month}`; 409 already voted this category this month |
| GET | `/api/v1/votes/mine?month=` | JWT | `Vote[]` |
| GET | `/api/v1/hall-of-fame/{category}/{month}` | - | `[{rank, work, votes}]` top 5 |
| GET | `/api/v1/categories` | - | `[{id, label}]` |
| GET | `/api/v1/practices` / `/{id}` | - | `Practice[]` / `Practice` with `status: "red"\|"orange"\|"green"` |
| POST | `/api/v1/scan/image` | - | multipart `file` → `{status, confidence, work?, known?, sources[]}` |

`Work = {id, artist_id, artist_name, title, story, category, media_url, media_kind: "image"|"audio"|"video"|"model", proof_urls[], sha256, human_proven, ai_declared, month, created_at, votes}`.
Scan `status` ∈ `human_proven | artiko_work | known_work | ai_made | unknown` (the two "found online / AI signals" answers of SOLUTION-6 §4.3 need web agents: later).
Categories: `painting, sculpture, photography, music, dance, oral_arts, crafts, digital`.
Errors: `{"error": {"code", "message"}}`.

## Tasks (API)

1. **Core + DB + auth** - `app/core/{config,db,errors,security}.py`, `app/models.py` (Base only), `alembic/` (initial migration creates schemas `app`, `better_auth`), `app/modules/health.py`, `app/main.py` (CORS, `/api/v1`, `/media`), `tests/conftest.py` (test DB `artiko_test`, Ed25519 key + JWKS stub, `auth_header(user_id)` fixture), `.env.example`. **verify:** `uv run pytest` green incl. test that `/api/v1/me` 401s without token and 200s with a signed token.
2. **Artists + me** - models `Artist(id uuid, user_id unique, display_name, bio, region, created_at)`; `app/modules/{me,artists}.py`. **verify:** tests: become artist, update, list, get 404.
3. **Works + files** - model `Work` (fields above + `embedding float4[] null`), `app/core/storage.py` (save upload → url, sha256, 20 MB cap, allowed mime), `app/modules/works.py`. `human_proven = len(proof) >= 2 and not ai_declared`. **verify:** tests: publish with 2 proofs → `human_proven`; with 0 proofs → not proven; non-artist → 403; bad mime → 400.
4. **Votes + Hall of Fame + categories** - model `Vote(user_id, work_id, category, month, unique(user_id, category, month))`; `app/modules/{votes,hall_of_fame}.py`. Rules: work must be `human_proven`, `work.month == current month`, not own work. **verify:** tests: vote ok; 2nd vote same category → 409; unproven → 400; own work → 400; hall-of-fame order and limit 5.
5. **Museum layout** - `GET /artists/{id}/museum`; pure function `layout(works) -> slots` in `app/modules/artists.py`. **verify:** unit test: images go on walls without overlapping, models on plinths.
6. **Practices (Index)** - model `Practice(id, name, region, category, practitioners, learners, median_age, description)`, `status()` rule: red if learners < practitioners/2 or median_age ≥ 60, orange if learners < practitioners, else green. **verify:** unit test on the rule + list endpoint.
7. **Embeddings + scan** - `app/core/embed.py` (`embed(image_bytes) -> np.ndarray(512)`; ONNX at `MODELS_DIR/clip-vision.onnx`; `EMBED_PROVIDER=none` in tests returns a deterministic hash-based vector), `app/core/index.py` (in-memory matrix of works + known works, `search(vec) -> (kind, id, score)`), `app/core/c2pa_check.py`, model `KnownWork(id, title, artist_name, date, museum, source_url, image_url, embedding)`, `app/modules/scan.py`. Thresholds: ≥ 0.90 match. Works embed on publish. **verify:** tests: published work's own image scans as `human_proven`; unrelated image → `unknown`; AI manifest stub → `ai_made`.
8. **Seed scripts** - `scripts/download_model.py` (CLIP vision ONNX), `scripts/seed_demo.py` (3 artists, ~15 works across 3 categories with proofs, votes, practices incl. kabary red), `scripts/index_met.py --limit N` (Met open-access public-domain paintings → KnownWork with embeddings). **verify:** run against dev DB; scan of a Mona Lisa image → `known_work`.

## Tasks (web)

9. **Auth + API client** - swap `next-auth` → `better-auth` + `pg`; `src/lib/{auth,auth-client,api}.ts`, `src/app/api/auth/[...all]/route.ts`, sign-in/up pages; generated client from OpenAPI (`@hey-api/openapi-ts`) in `src/lib/api-client/`. **verify:** sign up in browser, `GET /api/v1/me` from the page returns the user.
10. **Layout + Home + Explore** - nav (Home, Explore, Hall of Fame, Index, Check a photo, Me), category filter, work cards with the human-made badge. **verify:** `pnpm build` + browser check against seeded API.
11. **Artist 3D museum + vote** - `src/app/artists/[id]/page.tsx` + client `Museum.tsx` (R3F room from `slots`, click a work → panel with story, proof, vote button), 2D gallery fallback toggle. **verify:** browser: walk, open a work, vote → count updates; 2nd vote → message.
12. **Hall of Fame** - `src/app/hall-of-fame/page.tsx` (category × month, top 5 podium, rendered in the same 3D room with 5 slots). **verify:** browser against seeded votes.
13. **Index** - `src/app/index/page.tsx` practices with red / orange / green status. **verify:** kabary shows red.
14. **Check a photo** - `src/app/scan/page.tsx` upload → status card with sources and link to the artist's museum. **verify:** browser: seeded work image → human-proven; Mona Lisa → known work.
15. **Me / Create** - become artist, publish work with file + proofs. **verify:** browser publish → appears in own museum with the human-made badge.

## Edge cases

- Vote on a work from a previous month → 400 `not_in_contest` (task 4, test).
- Same file published twice by two artists → second gets 409 `duplicate_work` by sha256 (task 3, test).
- Scan upload that is not an image / > 20 MB → 400 / 413 (task 7, test).
- Token from an unknown key or expired → 401 (task 1, test).
- Artist with zero works → museum returns empty `slots`, web shows an empty-room message (task 5 test, task 11).

## Team split suggestion (18 h)

- Android: consumes the contract above; sign-in flow per "Auth" bullet.
- Web+API devs: take tasks 1-8 (API) in order, 9-15 (web) in parallel once task 1 + the contract exist; seed data (task 8) unblocks every UI check.
