"""Public release archive integrity regression tests, independent of private data."""

from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path

from tools.s93_release_gate import audit


def _fixture(root: Path) -> None:
    documents = {
        "LICENSE": b"license",
        "NOTICE": b"notice",
        "README.md": b"readme",
        "docs/ARCHITECTURE.md": b"architecture",
        "docs/DEMO-GUIDE.md": b"demo",
        "docs/RELEASE-CHECKLIST.md": b"release",
        "docs/THIRD-PARTY-LICENSE-INVENTORY.json": json.dumps(
            {"publication_license_clearance": "NOT_AUTOMATICALLY_GRANTED"}
        ).encode(),
    }
    for name, data in documents.items():
        p = root / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(data)
    manifest = {
        "third_party_original_corpus_included": False,
        "sha256": {name: hashlib.sha256(data).hexdigest() for name, data in documents.items()},
    }
    (root / "PUBLIC-CURATION-MANIFEST.json").write_text(json.dumps(manifest))


def test_content_verified_but_approval_still_pending(tmp_path: Path) -> None:
    _fixture(tmp_path)
    result = audit(tmp_path)
    assert result["status"] == "CONTENT_VERIFIED_ONLY"
    assert "explicit_owner_push_approval_required" in result["limits"]


def test_reject_added_private_corpus_and_untracked_file(tmp_path: Path) -> None:
    _fixture(tmp_path)
    (tmp_path / "corpus").mkdir()
    (tmp_path / "corpus" / "chunks.json").write_text("secret")
    (tmp_path / "unexpected.txt").write_text("random")
    result = audit(tmp_path)
    assert result["status"] == "BLOCK_RELEASE"
    assert any(x.startswith("PRIVATE_OR_GENERATED_PATH:") for x in result["issues"])
    assert "UNDECLARED_FILE:unexpected.txt" in result["issues"]


def test_reject_file_hash_drift(tmp_path: Path) -> None:
    _fixture(tmp_path)
    (tmp_path / "README.md").write_text("modified")
    assert "FILE_HASH_MISMATCH:README.md" in audit(tmp_path)["issues"]


def test_reject_secret_in_manifest_declared_file(tmp_path: Path) -> None:
    _fixture(tmp_path)
    key = b"gsk_" + b"Z" * 40
    (tmp_path / "README.md").write_bytes(key)
    assert any(x.startswith("SECRET_PATTERN:README.md") for x in audit(tmp_path)["issues"])


def test_reject_symlink(tmp_path: Path) -> None:
    _fixture(tmp_path)
    (tmp_path / "link").symlink_to(tmp_path / "README.md")
    assert "SYMLINK_FORBIDDEN:link" in audit(tmp_path)["issues"]


def test_reject_misleading_license_attestation(tmp_path: Path) -> None:
    _fixture(tmp_path)
    p = tmp_path / "docs/THIRD-PARTY-LICENSE-INVENTORY.json"
    p.write_text('{"publication_license_clearance":"ALL_CLEAR"}')
    result = audit(tmp_path)
    assert "MISLEADING_LICENSE_CLEARANCE" in result["issues"]


def test_allows_clean_git_checkout_metadata_and_local_build_cache(tmp_path: Path) -> None:
    _fixture(tmp_path)
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    (tmp_path / ".venv").mkdir()
    (tmp_path / ".venv" / "cache.txt").write_text("local-only")
    assert audit(tmp_path)["status"] == "CONTENT_VERIFIED_ONLY"


def test_copyright_exact_and_release_hold_in_project_tree() -> None:
    project_root = Path(__file__).resolve().parents[2]
    license_text = (project_root / "LICENSE").read_text(encoding="utf-8")
    notice_text = (project_root / "NOTICE").read_text(encoding="utf-8")
    assert "Copyright 2026 Pan Zheng" in license_text
    assert "Copyright 2026 Pan Zheng" in notice_text
    evidence_file = project_root / "docs/verification/STAGE9-S93-OWNER-SECURITY-SCAN-20261026.json"
    evidence = json.loads(evidence_file.read_text(encoding="utf-8"))
    assert evidence["npm_all"]["affected_package_count"] == 3
    assert evidence["decision"].startswith("HOLD_PUBLICATION")


def test_public_gitignore_does_not_hide_application_corpus_python_code(tmp_path: Path) -> None:
    """Git staging MUST preserve corpus-processing source while excluding root private corpus."""
    project_root = Path(__file__).resolve().parents[2]
    source_ignore = project_root / "public_demo/PUBLIC-gitignore.txt"
    ignore = source_ignore if source_ignore.is_file() else project_root / ".gitignore"
    (tmp_path / ".gitignore").write_bytes(ignore.read_bytes())
    source_path = tmp_path / "src/p2_research/application/corpus/source_contracts.py"
    source_path.parent.mkdir(parents=True, exist_ok=True)
    source_path.write_text("# public application source\n")
    private_path = tmp_path / "corpus/private.txt"
    private_path.parent.mkdir(parents=True, exist_ok=True)
    private_path.write_text("do not publish")
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    subprocess.run(["git", "add", "-A"], cwd=tmp_path, check=True)
    tracked = subprocess.check_output(
        ["git", "diff", "--cached", "--name-only"], cwd=tmp_path, text=True
    ).splitlines()
    assert "src/p2_research/application/corpus/source_contracts.py" in tracked
    assert "corpus/private.txt" not in tracked


def test_detects_tampered_screenshot_even_if_declared(tmp_path: Path) -> None:
    _fixture(tmp_path)
    screenshot = tmp_path / "docs/assets/demo-cited-answer.png"
    screenshot.parent.mkdir(parents=True, exist_ok=True)
    original = b"\x89PNG\r\n\x1a\nsynthetic fixture"
    screenshot.write_bytes(original)
    manifest = tmp_path / "PUBLIC-CURATION-MANIFEST.json"
    payload = json.loads(manifest.read_text())
    payload["sha256"]["docs/assets/demo-cited-answer.png"] = hashlib.sha256(original).hexdigest()
    manifest.write_text(json.dumps(payload))
    assert audit(tmp_path)["status"] == "CONTENT_VERIFIED_ONLY"
    screenshot.write_bytes(original + b"tampered")
    assert "FILE_HASH_MISMATCH:docs/assets/demo-cited-answer.png" in audit(tmp_path)["issues"]
