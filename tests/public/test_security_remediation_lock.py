"""Preserve the owner-tested security-remediation locks across public curation.

A version regression check is not a vulnerability scan. It proves that the
specific reviewed dependency versions did not silently revert in this tree.
"""

from __future__ import annotations

import json
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def _py_lock() -> dict[str, str]:
    locked = tomllib.loads((ROOT / "uv.lock").read_text(encoding="utf-8"))
    return {row["name"]: row["version"] for row in locked["package"]}


def _npm_lock() -> dict[str, object]:
    return json.loads((ROOT / "web/package-lock.json").read_text(encoding="utf-8"))


def test_fastapi_and_patched_transitive_dependencies_are_locked() -> None:
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    assert "fastapi==0.142.2" in project["project"]["dependencies"]
    locked = _py_lock()
    assert locked["fastapi"] == "0.142.2"
    assert locked["starlette"] == "1.10.0"
    assert locked["certifi"] == "2026.10.23"
    assert locked["opentelemetry-api"] == "1.45.1"


def test_npm_declared_pins_match_lock() -> None:
    package = json.loads((ROOT / "web/package.json").read_text(encoding="utf-8"))
    lock = _npm_lock()
    root = lock["packages"][""]
    for pkg in ("vite", "vitest"):
        expected = {"vite": "7.3.7", "vitest": "4.1.12"}[pkg]
        assert package["devDependencies"][pkg] == expected
        assert root["devDependencies"][pkg] == expected
        assert lock["packages"]["node_modules/" + pkg]["version"] == expected


def test_vitest_peer_is_resolved_from_same_patched_release() -> None:
    lock = _npm_lock()["packages"]
    assert lock["node_modules/@vitest/mocker"]["version"] == "4.1.12"
    assert lock["node_modules/@vitest/runner"]["version"] == "4.1.12"
