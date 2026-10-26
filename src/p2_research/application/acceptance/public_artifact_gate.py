"""Fail-closed public artifact inventory and explicit rights exclusions.

An allowlist of repository-owned engineering paths is necessary but not a legal grant.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import zipfile
from pathlib import Path
from typing import Any

from .license_preflight import LicensePreflightError, build_preflight

EXCLUDED_REST_PATHS = frozenset(
    {
        "content/zh-cn/docs/references/lifecycle/rest.md",
        "content/zh-cn/docs/references/protocols/rest.md",
        "content/zh-cn/docsv2.7/user/rest.md",
    }
)
SAFE_PREFIXES = ("src/", "tests/", "scripts/", "docs/", "evaluation/", ".github/")
SAFE_FILES = frozenset(
    {
        "README.md",
        "PROJECT-CONTEXT.md",
        "pyproject.toml",
        "uv.lock",
        ".gitignore",
        ".python-version",
        ".env.example",
    }
)
SAFE_METADATA_PREFIXES = ("data_sources/", "reports/")
FORBIDDEN_SEGMENTS = frozenset(
    {"raw", "cache", "processed", "generated", "indexes", "__pycache__", ".venv", ".git"}
)
FORBIDDEN_SUFFIXES = (
    ".png",
    ".jpg",
    ".jpeg",
    ".gif",
    ".webp",
    ".svg",
    ".pdf",
    ".pyc",
    ".db",
    ".sqlite",
    ".parquet",
)


def build_policy(root: Path, adjudication: Path) -> dict[str, Any]:
    """Bind the user's narrow exclusion instruction to reviewed, locked identities."""
    pre = build_preflight(root)
    report = json.loads(adjudication.read_text(encoding="utf-8"))
    if (
        report.get("snapshot_id") != pre["snapshot_id"]
        or report.get("commit_sha") != pre["commit_sha"]
        or report.get("document_count") != 24
        or report.get("chunks_sha256")
        != "a312e11036c2a7d6b723c9fbc5dfd8d984ec9865be7e41b00fdb9feaaa4d7fa1"
    ):
        raise LicensePreflightError("ADJUDICATION_BASELINE_DRIFT")
    indexed = {entry["path"]: entry for entry in report["files"]}
    expected = {entry["path"] for entry in pre["files"]}
    if set(indexed) != expected or len(report["files"]) != len(expected):
        raise LicensePreflightError("ADJUDICATION_DOCUMENT_DRIFT")
    locked = {entry["path"]: entry["document_sha256"] for entry in pre["files"]}
    if {
        path for path, info in indexed.items() if info.get("ambiguous_no_derivatives_wording")
    } != EXCLUDED_REST_PATHS:
        raise LicensePreflightError("AMBIGUOUS_RIGHTS_SET_DRIFT")
    if any(indexed[p].get("document_sha256") != locked[p] for p in expected):
        raise LicensePreflightError("DOCUMENT_HASH_DRIFT")
    records = []
    for path in sorted(expected):
        excluded = path in EXCLUDED_REST_PATHS
        records.append(
            {
                "path": path,
                "document_sha256": locked[path],
                "text_public_distribution": "excluded" if excluded else "pending",
                "media_public_distribution": "excluded" if excluded else "pending",
                "reason": "user_directed_ambiguous_rights_exclusion"
                if excluded
                else "file_level_clearance_pending",
            }
        )
    return {
        "schema_version": 1,
        "snapshot_id": pre["snapshot_id"],
        "commit_sha": pre["commit_sha"],
        "adjudication_sha256": hashlib.sha256(adjudication.read_bytes()).hexdigest(),
        "explicit_exclusions": len(EXCLUDED_REST_PATHS),
        "pending_documents": len(expected) - len(EXCLUDED_REST_PATHS),
        "public_corpus_distribution_approved": False,
        "source_corpus_unmodified": True,
        "records": records,
    }


def _safe_member(path: str) -> bool:
    if path.startswith(("/", "\\")) or "\\" in path or "//" in path:
        return False
    parts = path.split("/")
    if not parts or any(part in ("", ".", "..") or part in FORBIDDEN_SEGMENTS for part in parts):
        return False
    if path.lower().endswith(FORBIDDEN_SUFFIXES):
        return False
    if path in SAFE_FILES:
        return True
    if path.startswith(SAFE_PREFIXES):
        if path.startswith("evaluation/") and not path.endswith(".json"):
            return False
        return True
    if path.startswith("data_sources/"):
        return path in {
            "data_sources/sources.json",
            "data_sources/snapshots.lock.json",
            "data_sources/license-reviews.json",
        }
    if path.startswith("reports/"):
        return path.endswith((".json", ".md")) and not path.startswith("reports/generated/")
    return False


def scan_zip(archive: Path) -> dict[str, Any]:
    """Reject corpus payloads, unknown paths, traversal and symlinks in a public ZIP."""
    violations: list[str] = []
    checked = 0
    with zipfile.ZipFile(archive) as handle:
        members = [item for item in handle.infolist() if not item.is_dir()]
        if not members:
            raise LicensePreflightError("EMPTY_ARTIFACT")
        names = [item.filename for item in members]
        if len(set(names)) != len(names):
            raise LicensePreflightError("DUPLICATE_ARCHIVE_MEMBER")
        for item in members:
            name = item.filename
            parts = name.split("/")
            relative = (
                "/".join(parts[1:])
                if len(parts) > 1 and parts[0] == "evidence-grounded-research-agent"
                else name
            )
            unix_mode = (item.external_attr >> 16) & 0o170000
            if unix_mode == 0o120000 or not _safe_member(relative):
                violations.append(name)
            checked += 1
    return {
        "decision": "PASS" if not violations else "BLOCKED",
        "members_checked": checked,
        "violations": violations,
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Evaluate public artifact against conservative policy"
    )
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[4])
    parser.add_argument(
        "--adjudication", type=Path, default=Path("reports/s17-v04-license-adjudication.json")
    )
    parser.add_argument("--archive", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    result = build_policy(args.root, args.root / args.adjudication)
    if args.archive:
        result["artifact_scan"] = scan_zip(args.archive)
    payload = json.dumps(result, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(payload, encoding="utf-8")
    else:
        print(payload, end="")
    if args.archive and result["artifact_scan"]["decision"] != "PASS":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
