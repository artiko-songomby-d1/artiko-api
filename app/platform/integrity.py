"""Play Integrity verdicts behind a provider setting."""

from app.platform.config import get_settings
from app.platform.errors import ServiceUnavailable

FAILING_TOKEN = "fail"  # noqa: S105 - the stand-in provider's marker, not a credential


def verify(token: str | None) -> str:
    """"pass" or "fail". Only the "none" provider exists: it passes everything except the token "fail"."""
    provider = get_settings().integrity_provider
    if provider != "none":
        raise ServiceUnavailable("integrity_unavailable", f"The integrity provider {provider!r} is not implemented")
    return "fail" if token == FAILING_TOKEN else "pass"
