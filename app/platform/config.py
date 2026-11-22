from functools import lru_cache

from pydantic import NonNegativeInt, PositiveInt
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
    # Megapixels of a picture that may be decoded at full size, checked on its header before a pixel is decoded: a
    # few hundred kilobytes can announce gigabytes of pixels. Decoding takes 6 MB a megapixel and one picture is
    # decoded at a time, so 17 cost about 100 MB, on top of the 330 MB the API takes with the CLIP model loaded
    # (512 MB instance). A larger JPEG is decoded at 1/2, 1/4 or 1/8 of its size, so phone photos of 24 to 64
    # megapixels pass; a PNG or WebP cannot be, and is refused. Pictures that take more memory per pixel get
    # fewer: transparent PNG 3/4 of this, WebP 1/3 (see storage.open_image).
    max_image_megapixels: int = 17
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
    # POST /works reads every file into memory (up to 8 files of MAX_UPLOAD_MB) and runs the CLIP model. It requires
    # a token, so the limit follows the user, not the address. Nobody publishes 10 works a minute by hand.
    work_rate_limit_per_minute: int = 10
    # All users together: what the instance can take, so many accounts still cannot exceed it.
    work_global_rate_limit_per_minute: int = 60
    # POST /votes, per user: a voter has 8 votes a month (one per category), so 30 tries a minute is far above use.
    vote_rate_limit_per_minute: int = 30
    vote_global_rate_limit_per_minute: int = 600
    # PUT /me/artist, per user: a profile is edited a few times in a row at most.
    artist_rate_limit_per_minute: int = 10
    artist_global_rate_limit_per_minute: int = 120
    # POST /expositions, per user: every call adds a row, and an artist creates a handful of them in all.
    exposition_rate_limit_per_minute: int = 10
    exposition_global_rate_limit_per_minute: int = 120
    # Header the hosting proxy overwrites with the client IP. Empty = the socket peer. On Render it is
    # CF-Connecting-IP (see render.yaml): Cloudflare refuses a request that already carries it.
    # Never name a header a client can send itself (X-Forwarded-For), or the rate limit can be dodged.
    client_ip_header: str = ""
    # All API instances share rate limits when they use the same Redis URL.
    rate_limit_redis_url: str = ""

    models_dir: str = "models"
    # Cosine similarity above which a scanned photo is the same work (CLIP ViT-B/32).
    scan_match_threshold: float = 0.85
    # Seconds before a scan checks the database for index updates again. Zero refreshes every scan.
    scan_index_refresh_seconds: NonNegativeInt = 15
    # Where the web app lives (links in scan sources).
    public_web_url: str = "http://localhost:3000"
    # Publishing is refused when the image is this similar (CLIP cosine) to another artist's work or a known work.
    publish_duplicate_threshold: float = 0.92
    # ...or when its perceptual hash differs by at most this many of 64 bits (crops, resizes, recompression).
    publish_phash_max_distance: int = 8
    # Open possible-copy review cases one artist can have. Past it a refused publication names the latest one
    # instead of adding a row, so changing a byte of the file each time cannot fill the review queue.
    review_max_open_cases_per_artist: PositiveInt = 20
    # Secret that signs the server nonces a phone puts into its key attestation and into each capture it signs.
    # Empty = devices cannot register and no capture is accepted.
    nonce_secret: str = ""
    # "chain" verifies the Android key attestation chain against Google's roots; "none" accepts any chain (tests, demo).
    attestation_provider: str = "chain"
    # Extra PEM roots trusted besides Google's (private test CAs only). Like every signer of the chain, a root
    # must be a CA certificate (BasicConstraints).
    attestation_extra_roots_pem: str = ""
    # Google's list of revoked attestation certificates (leaked keys), checked by the "chain" provider. It is kept
    # in memory and refetched hourly; a failed refetch keeps the last list. Until a first list is fetched, devices
    # cannot register (503 attestation_unavailable). Empty = no revocation check.
    attestation_revocation_url: str = "https://android.googleapis.com/attestation/status"
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

    @property
    def async_database_url(self) -> str:
        return self.database_url.replace("postgresql://", "postgresql+psycopg://", 1)

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
