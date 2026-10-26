"""Stage 1 engineering closeout based on explicitly scoped, pinned evidence.

A human-reported online run is recorded as an attestation, not as a CI execution.
Publication clearance remains a separate, restrictive decision.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any

from .stage1 import CHUNKS_SHA256, GOLD_SHA256, assess_stage1


class EngineeringCloseoutError(ValueError):
    """Evidence is missing or inconsistent with the frozen project baseline."""


def _load(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise EngineeringCloseoutError(f"Cannot read {path}") from exc
    if not isinstance(value, dict):
        raise EngineeringCloseoutError(f"Expected JSON object: {path}")
    return value


def assess_engineering(root: Path, evidence_path: Path) -> dict[str, Any]:
    """Close local engineering only when all explicitly scoped evidence agrees."""
    base = assess_stage1(root)
    evidence = _load(evidence_path)
    if evidence.get("schema_version") != 1:
        raise EngineeringCloseoutError("Unknown evidence schema")
    if evidence.get("chunks_sha256") != CHUNKS_SHA256:
        raise EngineeringCloseoutError("CORPUS_DRIFT")
    if evidence.get("gold_sha256") != GOLD_SHA256:
        raise EngineeringCloseoutError("GOLD_DRIFT")
    if evidence.get("commit_sha") != base["commit_sha"]:
        raise EngineeringCloseoutError("UPSTREAM_COMMIT_DRIFT")
    local = evidence.get("local_verification")
    online = evidence.get("fresh_online")
    if not isinstance(local, dict) or not isinstance(online, dict):
        raise EngineeringCloseoutError("Missing independently scoped verification")
    if local.get("source") != "assistant_reported_v06_quality_run":
        raise EngineeringCloseoutError("Unknown local verification evidence source")
    if local.get("offline_tests_passed") != 121 or local.get("online_deselected") != 1:
        raise EngineeringCloseoutError("Local suite evidence needs update")
    if online.get("source") != "user_supplied_terminal_output":
        raise EngineeringCloseoutError("Missing online execution attestation")
    if online.get("result") != "PASS" or online.get("fresh_cache") is not True:
        raise EngineeringCloseoutError("Fresh online smoke not confirmed")
    if online.get("bootstrap_chunks_sha256") != CHUNKS_SHA256:
        raise EngineeringCloseoutError("ONLINE_BOOTSTRAP_DRIFT")
    if online.get("offline_replay_chunks_sha256") != CHUNKS_SHA256:
        raise EngineeringCloseoutError("ONLINE_REPLAY_DRIFT")
    trace_path = root / "reports/s17-v06-provenance-trace.json"
    trace = _load(trace_path)
    if (
        trace.get("chunks_sha256") != CHUNKS_SHA256
        or trace.get("gold_sha256") != GOLD_SHA256
        or trace.get("forward_reverse_trace_verified") is not True
        or trace.get("document_count") != 24
        or trace.get("chunk_count") != 409
        or trace.get("gold_case_count") != 10
        or trace.get("evidence_links") != 10
        or trace.get("includes_source_text") is not False
    ):
        raise EngineeringCloseoutError("Provenance report is not valid closeout evidence")
    digest = hashlib.sha256(trace_path.read_bytes()).hexdigest()
    checks = [{"id": f"CORPUS-ACC-{i:03d}", "engineering_status": "PASS"} for i in range(1, 7)]
    checks.append(
        {
            "id": "CORPUS-ACC-007",
            "engineering_status": "SEPARATE_PUBLICATION_GATE",
            "public_distribution_status": "RESTRICTED",
        }
    )
    checks.append(
        {
            "id": "CORPUS-ACC-008",
            "engineering_status": "LOCAL_OFFLINE_AND_ONLINE_SMOKE_PASS",
            "remote_ci_status": "NOT_RUN",
        }
    )
    return {
        "schema_version": 1,
        "decision": "ENGINEERING_CLOSEOUT_CANDIDATE",
        "stage": "1",
        "chunks_sha256": CHUNKS_SHA256,
        "gold_sha256": GOLD_SHA256,
        "provenance_report_sha256": digest,
        "source_commit_sha": base["commit_sha"],
        "verification_source": "user_online_attestation_and_assistant_quality_run",
        "checks": checks,
        "remote_ci": "NOT_RUN",
        "public_distribution": "RESTRICTED",
        "stage1_full_acceptance": "NOT_CLOSED",
        "stage2_internal_development_ready": True,
        "local_v07_replay": "PENDING_USER_VERIFICATION",
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Verify local engineering closeout evidence")
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[4])
    parser.add_argument(
        "--evidence",
        type=Path,
        default=Path("reports/s17-v07-user-verification.json"),
    )
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    report = assess_engineering(args.root, args.evidence)
    rendered = json.dumps(report, indent=2, ensure_ascii=False, sort_keys=True) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered, encoding="utf-8")
    print(rendered, end="")


if __name__ == "__main__":
    main()
