"""Import rules of the modular monolith: infrastructure below features, routers never shared."""

import ast
from pathlib import Path

APP = Path(__file__).parents[1] / "app"


def imports_of(path: Path) -> set[str]:
    found = set()
    for node in ast.walk(ast.parse(path.read_text())):
        if isinstance(node, ast.Import):
            found |= {a.name for a in node.names}
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            found.add(node.module)
            found |= {f"{node.module}.{a.name}" for a in node.names}
    return {i for i in found if i.startswith("app.")}


def test_platform_does_not_depend_on_features():
    # registry.py is the one place that lists every feature's tables.
    offenders = {
        str(p.relative_to(APP)): sorted(i for i in imports_of(p) if i.startswith("app.features"))
        for p in (APP / "platform").glob("*.py")
        if p.name != "registry.py" and any(i.startswith("app.features") for i in imports_of(p))
    }
    assert not offenders, offenders


def test_features_never_import_another_features_router():
    offenders = []
    for path in (APP / "features").rglob("*.py"):
        own = path.relative_to(APP / "features").parts[0]
        for name in imports_of(path):
            parts = name.split(".")
            if len(parts) >= 4 and parts[1] == "features" and parts[2] != own and parts[3] == "router":
                offenders.append(f"{path.relative_to(APP)} imports {name}")
    assert not offenders, offenders


# Feature -> features it may import. Anything else is a new dependency to discuss, not to slip in.
ALLOWED = {
    "artists": set(),
    "practices": {"works"},
    "works": {"artists"},
    "review": set(),
    "devices": {"works"},
    "integrity": {"works", "scan", "review"},
    "expositions": {"works", "artists"},
    "votes": {"works", "artists"},
    "museum": {"works", "artists"},
    "scan": {"works"},
    "me": {"artists"},
    "health": set(),
    "media": set(),
}


def test_feature_dependencies_follow_the_declared_direction():
    offenders = []
    for path in (APP / "features").rglob("*.py"):
        own = path.relative_to(APP / "features").parts[0]
        for name in imports_of(path):
            parts = name.split(".")
            if len(parts) >= 3 and parts[1] == "features" and parts[2] != own and parts[2] not in ALLOWED[own]:
                offenders.append(f"{path.relative_to(APP)} imports {name}")
    assert not offenders, offenders
