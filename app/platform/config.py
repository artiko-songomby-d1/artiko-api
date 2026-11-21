from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # Neon connection string, from .env. No default: the app refuses to start without a database.
    database_url: str
    # TestClient runs each request on a fresh event loop, so pooled connections must be off there.
    db_pool: bool = True

    # Better Auth (in artiko-web) signs the JWTs; issuer and audience are its base URL.
    auth_jwks_url: str = "http://localhost:3000/api/auth/jwks"
    auth_issuer: str = "http://localhost:3000"
    auth_audience: str = "http://localhost:3000"
    cors_origins: str = "http://localhost:3000"

    media_dir: str = "media"
    # S3-compatible bucket for uploads (Neon Object Storage). Empty = files stay on local disk in MEDIA_DIR.
    media_bucket: str = ""
    aws_endpoint_url_s3: str = ""
    aws_region: str = ""
    aws_access_key_id: str = ""
    aws_secret_access_key: str = ""
    # Public base URL of this API, used to build absolute media links for the apps.
    public_api_url: str = "http://localhost:8000"
    max_upload_mb: int = 20
    # POST /scan/image is public and runs the CLIP model: smaller photos and a per-client rate cap.
    scan_max_upload_mb: int = 5
    scan_rate_limit_per_minute: int = 20
    # All clients together: the CLIP model is the bottleneck, so a client rotating IPs still cannot exceed this.
    scan_global_rate_limit_per_minute: int = 120
    # Header the hosting proxy overwrites with the client IP (e.g. x-real-ip). Empty = the socket peer.
    # Never name a header a client can send itself, or the rate limit can be dodged.
    client_ip_header: str = ""

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
