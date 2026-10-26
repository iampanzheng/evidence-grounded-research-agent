"""Validate owner-evidenced npm zero-vulnerability remediation against pinned lock files."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_vitest4_dependency_graph_has_no_known_prior_affected_package() -> None:
    packages = json.loads((ROOT / "web/package-lock.json").read_text())["packages"]
    pins = json.loads((ROOT / "web/package.json").read_text())["devDependencies"]
    assert pins["vitest"] == "4.1.12"
    assert packages[""]["devDependencies"]["vitest"] == "4.1.12"
    assert packages["node_modules/vitest"]["version"] == "4.1.12"
    assert packages["node_modules/@vitest/mocker"]["version"] == "4.1.12"
    assert "node_modules/tinypool" not in packages


def test_owner_audit_attestation_matches_exact_lock_bytes() -> None:
    evidence = json.loads(
        (ROOT / "docs/verification/STAGE9-S93-VITEST4-SECURITY-CLEARANCE.json").read_text()
    )
    for file, key in [
        ("web/package.json", "package_json_sha256"),
        ("web/package-lock.json", "package_lock_sha256"),
    ]:
        assert hashlib.sha256((ROOT / file).read_bytes()).hexdigest() == evidence[key]
    assert evidence["npm_all_summary"]["total"] == 0
    assert evidence["npm_production_summary"]["total"] == 0
    assert evidence["release_decision"].startswith("HOLD_PUBLICATION")
