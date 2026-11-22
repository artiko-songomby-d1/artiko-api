# Base images are pinned by digest, so a rebuild cannot silently pull different content. The tag stays in front
# of the digest for readers and for Dependabot (docker ecosystem), which bumps tag and digest together.
FROM ghcr.io/astral-sh/uv:0.11.28@sha256:0f36cb9361a3346885ca3677e3767016687b5a170c1a6b88465ec14aefec90aa AS uv
FROM python:3.15-slim@sha256:65a65fc7b30b06cb6df4930fd4650697cb23f4c2698ab81fc87e4b031bc36928

COPY --from=uv /uv /bin/uv
WORKDIR /app
# ffmpeg and ffprobe cut the frames of MP4 videos for AI-generated detection; prlimit comes with util-linux.
RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg \
    && rm -rf /var/lib/apt/lists/*
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_NO_DEV=1 PATH="/app/.venv/bin:$PATH"

# Dependencies first, so code changes do not reinstall them.
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-install-project

COPY . .
RUN uv sync --frozen
# The CLIP model (~90 MB) is baked into the image: no download at startup.
RUN python -m scripts.download_model

# Run as an unprivileged user, so a flaw in the app does not give root in the container. The code, the virtualenv
# and the model stay owned by root: the app only reads them. MEDIA_DIR is the one place it writes (uploads, when no
# bucket is configured), so it is created here and handed over; a new Compose volume mounted on it inherits that.
RUN useradd --create-home --uid 10001 app && mkdir -p media && chown app:app media
USER app

# Render sets PORT. Migrations run before every start; they are no-ops when the schema is current, and
# alembic/env.py takes a database lock so instances starting together run them one at a time.
CMD ["sh", "-c", "alembic upgrade head && uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000}"]
