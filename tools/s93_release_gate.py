"""Check a *fresh curated public tree* against its immutable file inventory.

This command NEVER publishes to GitHub. Release intent and unreviewed third-party
licenses/security findings remain explicit owner gates.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

PRIVATE_PARTS = {
    ".git",
    ".venv",
    "node_modules",
    "dist",
    "__pycache__",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
}
PRIVATE_ROOTS = {"corpus", "data_sources", "evaluation", "reports", "artifacts", "experiments"}
SECRET_PATTERNS = {
    "provider_key": re.compile(rb"\b(?:gsk_[A-Za-z0-9]{25,}|sk-proj-[A-Za-z0-9_-]{25,})\b"),
    "github_token": re.compile(rb"\bgithub_pat_[A-Za-z0-9_]{30,}\b"),
    "private_key": re.compile(rb"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
    "aws_access_key": re.compile(rb"\b(?:AKIA|ASIA)[A-Z0-9]{16}\b"),
}


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def audit(root: Path) -> dict[str, Any]:
    issues: list[str] = []
    manifest_path = root / "PUBLIC-CURATION-MANIFEST.json"
    if not manifest_path.is_file() or manifest_path.is_symlink():
        return {"status": "BLOCK_RELEASE", "issues": ["MISSING_OR_UNSAFE_PUBLIC_MANIFEST"]}
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        declared: dict[str, str] = manifest["sha256"]
    except (ValueError, KeyError, TypeError):
        return {"status": "BLOCK_RELEASE", "issues": ["INVALID_PUBLIC_MANIFEST"]}
    actual: dict[str, str] = {}
    local_generated = {
        ".git",
        ".venv",
        "node_modules",
        "dist",
        "__pycache__",
        ".pytest_cache",
        ".mypy_cache",
        ".ruff_cache",
    }
    for path in sorted(root.rglob("*")):
        rel = path.relative_to(root)
        name = rel.as_posix()
        parts = rel.parts
        if any(p in local_generated for p in parts):
            continue  # Git checkouts/build outputs are never members of this release manifest.
        if parts and parts[0] in PRIVATE_ROOTS:
            # Excluded generated directories are not copied into release ZIP, but
            # this audit MUST still reject them in the proposed release tree.
            issues.append("PRIVATE_OR_GENERATED_PATH:" + name)
            continue
        if path.is_symlink():
            issues.append("SYMLINK_FORBIDDEN:" + name)
            continue
        if not path.is_file():
            continue
        if path.stat().st_size > 2_000_000:
            issues.append("OVERSIZED_FILE:" + name)
            continue
        if name == "PUBLIC-CURATION-MANIFEST.json":
            continue
        data = path.read_bytes()
        actual[name] = sha(data)
        for rule, pattern in SECRET_PATTERNS.items():
            if pattern.search(data):
                issues.append("SECRET_PATTERN:" + name + ":" + rule)
    if (root / ".git").is_dir():
        tracked = subprocess.run(
            ["git", "ls-files", "--cached", "-z"], cwd=root, capture_output=True, check=False
        )
        if tracked.returncode != 0:
            issues.append("GIT_TRACKED_FILE_AUDIT_FAILED")
        else:
            tracked_files = {
                x for x in tracked.stdout.decode("utf-8", errors="replace").split("\0") if x
            }
            for name in sorted(tracked_files - set(declared) - {"PUBLIC-CURATION-MANIFEST.json"}):
                issues.append("GIT_TRACKS_UNDECLARED_FILE:" + name)
    for name in sorted(set(actual) - set(declared)):
        issues.append("UNDECLARED_FILE:" + name)
    for name in sorted(set(declared) - set(actual)):
        issues.append("DECLARED_FILE_MISSING:" + name)
    for name in sorted(set(actual) & set(declared)):
        if actual[name] != declared[name]:
            issues.append("FILE_HASH_MISMATCH:" + name)
    if manifest.get("third_party_original_corpus_included") is not False:
        issues.append("CORPUS_EXCLUSION_NOT_CONFIRMED")
    required = {
        "LICENSE",
        "NOTICE",
        "README.md",
        "docs/ARCHITECTURE.md",
        "docs/DEMO-GUIDE.md",
        "docs/RELEASE-CHECKLIST.md",
        "docs/THIRD-PARTY-LICENSE-INVENTORY.json",
    }
    for name in sorted(required - set(actual)):
        issues.append("RELEASE_DOCUMENT_MISSING:" + name)
    try:
        inventory = json.loads((root / "docs/THIRD-PARTY-LICENSE-INVENTORY.json").read_text())
        if inventory["publication_license_clearance"] != "NOT_AUTOMATICALLY_GRANTED":
            issues.append("MISLEADING_LICENSE_CLEARANCE")
    except (OSError, KeyError, ValueError):
        issues.append("DEPENDENCY_INVENTORY_INVALID")
    return {
        "schema_version": 1,
        "status": "BLOCK_RELEASE" if issues else "CONTENT_VERIFIED_ONLY",
        "issues": issues,
        "file_count": len(actual) + 1,
        "manifest_sha256": sha(manifest_path.read_bytes()),
        "limits": [
            "local_git_history_not_examined",
            "owner_online_npm_security_scan_time_bounded_recheck_before_future_release",
            "third_party_notice_review_owner_confirmed_not_independent_legal_opinion",
            "explicit_owner_push_approval_required",
        ],
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--output", type=Path)
    parser.add_argument("--enforce", action="store_true", help="Exit 2 unless content is verified")
    args = parser.parse_args()
    result = audit(args.root)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print(
        json.dumps({k: result[k] for k in ("status", "file_count", "issues")}, ensure_ascii=False)
    )
    if args.enforce and result["status"] != "CONTENT_VERIFIED_ONLY":
        sys.exit(2)


if __name__ == "__main__":
    main()
