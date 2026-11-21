FROM python:3.14-slim

COPY --from=ghcr.io/astral-sh/uv:0.11.28 /uv /bin/uv
WORKDIR /app
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_NO_DEV=1 PATH="/app/.venv/bin:$PATH"

# Dependencies first, so code changes do not reinstall them.
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-install-project

COPY . .
RUN uv sync --frozen
# The CLIP model (~90 MB) is baked into the image: no download at startup.
RUN python -m scripts.download_model

# Render sets PORT. Migrations run before every start; they are no-ops when the schema is current.
CMD ["sh", "-c", "alembic upgrade head && uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000}"]
