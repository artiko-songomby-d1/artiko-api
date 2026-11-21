"""`limit` / `offset` query parameters shared by list endpoints."""

from dataclasses import dataclass
from typing import Annotated

from fastapi import Depends, Query

DEFAULT_LIMIT = 100
MAX_LIMIT = 200


@dataclass(frozen=True)
class Page:
    limit: int
    offset: int


def _page(
    limit: Annotated[int, Query(ge=1, le=MAX_LIMIT)] = DEFAULT_LIMIT,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> Page:
    return Page(limit, offset)


Paged = Annotated[Page, Depends(_page)]
