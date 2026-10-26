"""Portfolio documentation must foreground measured results without blurring demo boundaries."""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_public_readme_exposes_results_and_caveats() -> None:
    template = ROOT / "public_demo/PUBLIC-README.md"
    readme = template if template.is_file() else ROOT / "README.md"
    body = readme.read_text(encoding="utf-8")
    assert "# Evidence-grounded Research Agent" in body
    assert "## Results at a glance" in body
    assert "14 / 16" in body and "4 / 4" in body
    assert "two answerable questions fail" in body.lower()
    assert "deterministic fake" in body.lower()
    assert "claim_support=NOT_VERIFIED" in body
    assert "Copyright 2026 Pan Zheng" in body
    assert "docs/EVALUATION.md" in body
    assert "PUBLICATION STATUS: CURATED RELEASE CANDIDATE" not in body.upper()


def test_evaluation_ledger_is_explicitly_non_reproducible() -> None:
    ledger = (ROOT / "docs/EVALUATION.md").read_text(encoding="utf-8")
    assert "14/16" in ledger
    assert "4/4" in ledger
    assert "S73-PROP-002" in ledger and "S73-PROP-003" in ledger
    assert "3,029.755 ms" in ledger
    assert "not an external-domain holdout" in ledger
    assert "not distributed" in ledger
    assert "NOT_VERIFIED" in ledger


def test_public_app_does_not_advertise_private_source_as_demo_source() -> None:
    template = ROOT / "public_demo/PUBLIC-App.tsx"
    ui = template if template.is_file() else ROOT / "web/src/App.tsx"
    content = ui.read_text(encoding="utf-8")
    assert "当前公开演示使用原创的虚构 Orion 文档" in content
    assert "基于已冻结的真实文档进行检索、生成与引用验证" not in content


def test_real_demo_images_are_allowlisted_pngs_and_documented() -> None:
    template = ROOT / "public_demo/PUBLIC-README.md"
    readme = (template if template.is_file() else ROOT / "README.md").read_text(encoding="utf-8")
    guide = (ROOT / "docs/DEMO-GUIDE.md").read_text(encoding="utf-8")
    for name in ("demo-workspace.png", "demo-cited-answer.png", "demo-safe-refusal.png"):
        image = ROOT / "docs/assets" / name
        data = image.read_bytes()
        assert data.startswith(b"\x89PNG\r\n\x1a\n") and data[12:16] == b"IHDR"
        width = int.from_bytes(data[16:20], "big")
        height = int.from_bytes(data[20:24], "big")
        assert width >= 600 and height >= 600
        assert name in readme and name in guide
    assert "actual local demo screenshots" in readme.lower()
    assert "Claim support: NOT_VERIFIED" in readme
    assert "deterministic fake LLM" in readme


def test_post_publication_record_separates_baseline_from_unpushed_update() -> None:
    import json

    manifest_path = ROOT / "PUBLIC-CURATION-MANIFEST.json"
    # The internal engineering source does not carry the generated public manifest.
    if not manifest_path.is_file():
        curator = (ROOT / "tools/s92_curate_public.py").read_text(encoding="utf-8")
        assert '"release_type": "CURATED_PUBLIC_SOURCE_SNAPSHOT"' in curator
        return
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert manifest["release_type"] == "CURATED_PUBLIC_SOURCE_SNAPSHOT"
    baseline = manifest["published_baseline"]
    assert baseline["repository"] == "iampanzheng/evidence-grounded-research-agent"
    assert baseline["commit_prefix"] == "cc67d0f"
    assert baseline["ci_run_id"] == 40291128850
    assert baseline["ci_conclusion"] == "success"
    assert baseline["live_repository_independently_verified"] is False
    assert manifest["third_party_original_corpus_included"] is False
    assert manifest["original_private_git_history_included"] is False
    note = (ROOT / "docs/RELEASE-CHECKLIST.md").read_text(encoding="utf-8")
    assert "public baseline published" in note.lower()
    assert "future updates" in note.lower()
    assert "GitHub Actions" in note
