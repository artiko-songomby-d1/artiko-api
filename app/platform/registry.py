"""Imports every feature's tables so `Base.metadata` is complete (Alembic and the test cleanup read it)."""

from app.features.artists import models as artists
from app.features.practices import models as practices
from app.features.scan import models as scan
from app.features.works import models as works

__all__ = ["artists", "practices", "scan", "works"]
