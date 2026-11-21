"""openapi.yml is the contract web and Android build against: the code may not drift from it."""

from pathlib import Path

import yaml

from app.main import app

CONTRACT = yaml.safe_load((Path(__file__).parents[1] / "openapi.yml").read_text())
METHODS = {"get", "post", "put", "patch", "delete"}


def operations(spec: dict) -> set[tuple[str, str]]:
    return {(path, m) for path, item in spec["paths"].items() for m in item if m in METHODS}


def test_every_served_route_is_in_the_contract():
    undeclared = operations(app.openapi()) - operations(CONTRACT)
    assert not undeclared, f"Add these to openapi.yml or remove them: {sorted(undeclared)}"


def test_operation_ids_are_unique():
    ids = [op["operationId"] for item in CONTRACT["paths"].values() for m, op in item.items() if m in METHODS]
    assert len(ids) == len(set(ids))
