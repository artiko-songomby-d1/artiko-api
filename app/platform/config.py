from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = ""  # Neon connection string, from .env
    # TestClient runs each request on a fresh event loop, so pooled connections must be off there.
    db_pool: bool = True

    # Keycloak signs the access tokens (SOLUTION-6, 12.6). Defaults match the local dev realm `lova`;
    # `auth_audience` is the audience claim Keycloak adds for this API (an audience mapper on the client).
    auth_jwks_url: str = "http://localhost:8080/realms/lova/protocol/openid-connect/certs"
    auth_issuer: str = "http://localhost:8080/realms/lova"
    auth_audience: str = "lova-api"
    cors_origins: str = "http://localhost:3000"

    media_dir: str = "media"
    # Public base URL of this API, used to build absolute media links for the apps.
    public_api_url: str = "http://localhost:8000"
    max_upload_mb: int = 20

    models_dir: str = "models"
    # Cosine similarity above which a scanned photo is the same work (CLIP ViT-B/32).
    scan_match_threshold: float = 0.85
    # Where the web app lives (links in scan sources).
    public_web_url: str = "http://localhost:3000"
    # "clip" runs the ONNX model; "none" gives a deterministic stand-in vector (tests, no model).
    embed_provider: str = "clip"

    @property
    def async_database_url(self) -> str:
        return self.database_url.replace("postgresql://", "postgresql+psycopg://", 1)

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
