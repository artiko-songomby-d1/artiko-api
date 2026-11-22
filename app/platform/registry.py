"""Imports every feature's tables so `Base.metadata` is complete (Alembic and the test cleanup read it)."""

from app.features.artists import models as artists
from app.features.devices import models as devices
from app.features.practices import models as practices
from app.features.review import models as review
from app.features.scan import models as scan
from app.features.works import models as works

__all__ = ["artists", "devices", "practices", "review", "scan", "works"]
