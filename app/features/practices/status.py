"""Living Heritage Index: how healthy is the transmission of a practice."""

from typing import Literal

Status = Literal["red", "orange", "green"]
SEVERITY: dict[str, int] = {"red": 0, "orange": 1, "green": 2}


def status(practitioners: int, learners: int, median_age: int) -> Status:
    """Red when transmission is failing, orange when it is slowing."""
    if learners < practitioners / 2 or median_age >= 60:
        return "red"
    if learners < practitioners:
        return "orange"
    return "green"
