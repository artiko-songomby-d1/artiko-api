import pytest
from pydantic import ValidationError

from app.platform.config import Settings

DATABASE_URL = "postgresql://artiko:artiko@127.0.0.1:5432/artiko_test"


def settings(**values) -> Settings:
    return Settings(_env_file=None, database_url=DATABASE_URL, **values)


@pytest.mark.parametrize(
    ("values", "named"),
    [
        ({"sightengine_secret": "s3cr3t-value"}, "SIGHTENGINE_USER"),
        ({"sightengine_user": "u-12345"}, "SIGHTENGINE_SECRET"),
    ],
)
def test_startup_refuses_missing_credentials_or_unknown_provider(values, named):
    with pytest.raises(ValidationError) as missing:
        settings(ai_detection_provider="sightengine", **values)
    assert named in str(missing.value)
    assert all(v not in str(missing.value) for v in values.values())

    with pytest.raises(ValidationError) as unknown:
        settings(ai_detection_provider="openai")
    assert "ai_detection_provider" in str(unknown.value)

    assert settings().ai_detection_provider == "none"
    configured = settings(ai_detection_provider="sightengine", sightengine_user="u", sightengine_secret="s")
    assert configured.ai_detection_threshold == 0.8 and configured.ai_detection_wait_seconds == 30
