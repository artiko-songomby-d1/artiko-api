"""Contest months, written `YYYY-MM` (UTC)."""

from datetime import UTC, datetime

MONTH = r"^\d{4}-(0[1-9]|1[0-2])$"


def current_month() -> str:
    return datetime.now(UTC).strftime("%Y-%m")
