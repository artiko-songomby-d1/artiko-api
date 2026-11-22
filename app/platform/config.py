from functools import lru_cache
from typing import Literal, Self

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    # Validation errors name the variable, never its value: a start-up log must not print a secret.
    model_config = SettingsConfigDict(env_file=".env", extra="ignore", hide_input_in_errors=True)

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
    max_upload_mb: int = 60
    # Secret that signs the short-lived links to reviewer-only proof files. Empty = no private link is issued.
    proof_url_secret: str = ""
    proof_url_ttl_seconds: int = 300
    # GET /proofs/{id}/file is public (the signature is the credential): rate limit it like the scan.
    proof_rate_limit_per_minute: int = 60
    proof_global_rate_limit_per_minute: int = 600
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
    # Publishing is refused when the image is this similar (CLIP cosine) to another artist's work or a known work.
    publish_duplicate_threshold: float = 0.92
    # ...or when its perceptual hash differs by at most this many of 64 bits (crops, resizes, recompression).
    publish_phash_max_distance: int = 8
    # Secret that signs the server nonces a phone puts into its key attestation. Empty = devices cannot register.
    nonce_secret: str = ""
    # "chain" verifies the Android key attestation chain against Google's roots; "none" accepts any chain (tests, demo).
    attestation_provider: str = "chain"
    # Extra PEM roots trusted besides Google's (private test CAs only).
    attestation_extra_roots_pem: str = ""
    # "none" accepts every integrity token except the literal "fail"; no other provider is implemented yet.
    integrity_provider: str = "none"
    # Transition switch, true until the Android app can capture signed proof: imported proof files still count for
    # the badge, as before. Set it to false to enforce the rule (only verified in-app captures count).
    allow_imported_proofs_for_badge: bool = True
    # A signed capture must reach the server within this many minutes of being taken.
    capture_max_age_minutes: int = 10
    # Device registration and nonce requests per client per minute.
    device_rate_limit_per_minute: int = 20
    device_global_rate_limit_per_minute: int = 200
    # "clip" runs the ONNX model; "none" gives a deterministic stand-in vector (tests, no model).
    embed_provider: str = "clip"
    # "sightengine" asks Sightengine's genai model whether an undeclared work's main file is AI-generated;
    # "none" checks nothing (tests, local development).
    ai_detection_provider: Literal["none", "sightengine"] = "none"
    sightengine_user: str = ""
    sightengine_secret: SecretStr = SecretStr("")
    # A main file whose AI score (the highest frame score of a video) reaches this is refused.
    ai_detection_threshold: float = Field(0.8, gt=0, le=1, allow_inf_nan=False)
    # Longest wait for a turn at Sightengine (1 request per second on the free plan) before the file is accepted.
    ai_detection_wait_seconds: float = Field(30, ge=0, allow_inf_nan=False)
    # Video checks running at once: each runs ffmpeg, the largest memory user of a check; the others wait.
    ai_detection_max_video_checks: int = Field(1, ge=1)
    # POST /works per user and for everyone, per minute, while Sightengine checks spend the free quota.
    works_rate_limit_per_minute: int = Field(10, ge=1)
    works_global_rate_limit_per_minute: int = Field(30, ge=1)
    # prlimit --data cap of each ffprobe and ffmpeg process, in MiB: the measured need of a noisy 4K MP4 (202 MiB with
    # Debian 13's ffmpeg 7.1) plus a margin.
    ai_detection_ffmpeg_memory_mb: int = Field(272, ge=1)

    @field_validator("sightengine_user", "sightengine_secret", mode="before")
    @classmethod
    def _trimmed(cls, value: object) -> object:
        return value.strip() if isinstance(value, str) else value

    @model_validator(mode="after")
    def _ai_detection_has_credentials(self) -> Self:
        if self.ai_detection_provider == "sightengine":
            if not self.sightengine_user:
                raise ValueError("SIGHTENGINE_USER is required when AI_DETECTION_PROVIDER=sightengine")
            if not self.sightengine_secret.get_secret_value():
                raise ValueError("SIGHTENGINE_SECRET is required when AI_DETECTION_PROVIDER=sightengine")
        return self

    @property
    def async_database_url(self) -> str:
        return self.database_url.replace("postgresql://", "postgresql+psycopg://", 1)

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
