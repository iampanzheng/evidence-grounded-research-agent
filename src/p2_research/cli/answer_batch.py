"""Opt-in Groq batch collection; raw answers/evidence stay in private local checkpoints."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import stat
import time
from dataclasses import asdict
from pathlib import Path
from typing import Any

from p2_research.application.research.answer_evaluation import (
    build_report,
    load_frozen_cases,
    write_safe_report,
)
from p2_research.application.research.grounded import Evidence, ResearchAnswer
from p2_research.application.research.llm import (
    CITATION_POLICIES,
    CITATION_POLICY_COMPAT,
    GenerationRun,
    generate_answer,
)
from p2_research.application.retrieval.bm25 import BM25Index, load_locked_chunks
from p2_research.domain.models import ResearchStatus
from p2_research.infrastructure.llm.openai_compatible import OpenAICompatibleChatClient


class BatchError(ValueError):
    """Unsafe or inconsistent private batch state."""


def _private_directory(path: Path) -> None:
    if path.is_symlink() or (path.exists() and not path.is_dir()):
        raise BatchError("private directory must be a non-symlink directory")
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(path, 0o700)


def _write_private(path: Path, record: dict[str, Any]) -> None:
    if path.is_symlink():
        raise BatchError("private checkpoint symlink refused")
    temporary = path.with_name(path.name + ".tmp")
    if temporary.is_symlink():
        raise BatchError("private checkpoint temporary symlink refused")
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(record, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _serialize_run(run: GenerationRun) -> dict[str, Any]:
    return {
        "question": run.result.question,
        "status": run.result.status.value,
        "answer": run.result.answer,
        "citations": list(run.result.citations),
        "evidence": [asdict(e) for e in run.result.evidence],
        "generator": run.result.generator,
        "model": run.model,
        "input_tokens": run.input_tokens,
        "output_tokens": run.output_tokens,
        "latency_ms": run.latency_ms,
        "error_category": run.error_category,
        "citation_provenance": run.citation_provenance,
        "llm_attempts": run.llm_attempts,
    }


def _deserialize_run(data: dict[str, Any]) -> GenerationRun:
    result = ResearchAnswer(
        data["question"],
        ResearchStatus(data["status"]),
        data["answer"],
        tuple(data["citations"]),
        tuple(Evidence(**e) for e in data["evidence"]),
        data["generator"],
    )
    return GenerationRun(
        result,
        data["model"],
        data["input_tokens"],
        data["output_tokens"],
        data["latency_ms"],
        data["error_category"],
        data.get("citation_provenance", "NOT_OBSERVED"),
        data.get("llm_attempts", 0),
    )


def collect(
    gold: bytes,
    chunks: list[dict[str, Any]],
    client: OpenAICompatibleChatClient,
    private_dir: Path,
    *,
    interval_seconds: float = 2.0,
    max_cases: int = 10,
    top_k: int = 5,
    budget_chars: int = 9000,
    retry_failed: bool = False,
    case_ids: tuple[str, ...] | None = None,
    capture_raw_responses: bool = False,
    citation_policy: str = CITATION_POLICY_COMPAT,
    max_citation_retries: int = 0,
    sleep: Any = time.sleep,
) -> dict[str, Any]:
    """Serial, resumable collection; system errors are checkpoints, never silent success."""
    if (
        not 0 <= interval_seconds <= 120
        or not 1 <= max_cases <= 10
        or not 1 <= top_k <= 15
        or not 256 <= budget_chars <= 24000
    ):
        raise BatchError("invalid rate or case limit")
    if citation_policy not in CITATION_POLICIES:
        raise BatchError("unknown citation policy")
    if max_citation_retries not in (0, 1):
        raise BatchError("citation retries must be 0 or 1")
    cases = load_frozen_cases(gold)
    if case_ids is not None:
        valid_ids = {case["case_id"] for case in cases}
        if not case_ids or len(case_ids) != len(set(case_ids)) or not set(case_ids) <= valid_ids:
            raise BatchError("invalid or duplicate selected case IDs")
        if len(case_ids) > max_cases:
            raise BatchError("selected case count exceeds max_cases")
        cases = tuple(case for case in cases if case["case_id"] in set(case_ids))
    else:
        cases = cases[:max_cases]
    _private_directory(private_dir)
    fingerprint = hashlib.sha256(
        (
            hashlib.sha256(gold).hexdigest()
            + "|"
            + client.base_url
            + "|"
            + client.model
            + f"|top_k={top_k}|budget={budget_chars}"
            + (f"|citation_retries={max_citation_retries}" if max_citation_retries else "")
            + (
                ""
                if citation_policy == CITATION_POLICY_COMPAT
                else f"|citation_policy={citation_policy}"
            )
        ).encode()
    ).hexdigest()
    index = BM25Index(chunks)
    runs: dict[str, GenerationRun] = {}
    executed = 0
    resumed = 0
    retried_failed = 0
    for case in cases:
        cid = case["case_id"]
        file = private_dir / f"{cid}.json"
        if file.is_symlink():
            raise BatchError("private checkpoint symlink refused")
        saved_failure = False
        if file.exists():
            if not stat.S_ISREG(file.stat().st_mode):
                raise BatchError("non-file checkpoint refused")
            saved = json.loads(file.read_text(encoding="utf-8"))
            if saved.get("fingerprint") != fingerprint or saved.get("case_id") != cid:
                raise BatchError("checkpoint configuration drift")
            run = _deserialize_run(saved["run"])
            if run.result.question != case["question"]:
                raise BatchError("checkpoint question drift")
            saved_failure = bool(run.error_category and run.error_category.startswith("provider_"))
            if not (saved_failure and retry_failed):
                resumed += 1
        if not file.exists() or (saved_failure and retry_failed):
            if executed:
                sleep(interval_seconds)
            observer = None
            if capture_raw_responses:
                attempt_number = 0

                def capture(content: str, *, case_id: str = cid) -> None:
                    nonlocal attempt_number
                    attempt_number += 1
                    _write_private(
                        private_dir / f"{case_id}.raw-response-attempt-{attempt_number:02d}.json",
                        {"case_id": case_id, "attempt": attempt_number, "content": content},
                    )

                observer = capture
            run = generate_answer(
                case["question"],
                index,
                chunks,
                client,
                top_k=top_k,
                budget_chars=budget_chars,
                response_observer=observer,
                citation_policy=citation_policy,
                max_citation_retries=max_citation_retries,
            )
            if capture_raw_responses and attempt_number:
                latest = json.loads(
                    (
                        private_dir / f"{cid}.raw-response-attempt-{attempt_number:02d}.json"
                    ).read_text(encoding="utf-8")
                )
                _write_private(
                    private_dir / f"{cid}.raw-response.json",
                    {"case_id": cid, "content": latest["content"]},
                )
            # Only replace a failed checkpoint on an explicit operator retry.
            # Preserve its previous sanitized failure metadata for audit.
            record: dict[str, Any] = {
                "case_id": cid,
                "fingerprint": fingerprint,
                "run": _serialize_run(run),
            }
            if saved_failure:
                record["previous_error_category"] = saved["run"].get("error_category")
                retried_failed += 1
            _write_private(file, record)
            executed += 1
        runs[cid] = run
        # Stop on provider failure; preserve checkpoint for diagnosis, do not keep burning quota.
        if run.error_category and run.error_category.startswith("provider_"):
            break
    report = build_report(gold, runs)
    report["experiment"] = (
        "s35-final-top5-compat"
        if any(case["case_id"].startswith("S35-CAND-") for case in cases)
        else "s33-v02-private-answer-collection"
    )
    report["batch"] = {
        "citation_policy": citation_policy,
        "max_citation_retries": max_citation_retries,
        "total_llm_attempts": sum(r.llm_attempts for r in runs.values()),
        "top_k": top_k,
        "budget_chars": budget_chars,
        "selection_mode": "explicit" if case_ids is not None else "first_n",
        "selected_case_ids": [case["case_id"] for case in cases],
        "new_calls": executed,
        "resumed_cases": resumed,
        "retried_failed_cases": retried_failed,
        "halted_on_provider_error": any(
            r.error_category and r.error_category.startswith("provider_") for r in runs.values()
        ),
        "total_input_tokens_reported": sum(r.input_tokens or 0 for r in runs.values()),
        "total_output_tokens_reported": sum(r.output_tokens or 0 for r in runs.values()),
    }
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description="Opt-in serial, checkpointed answer collection")
    parser.add_argument(
        "--chunks", type=Path, default=Path("reports/generated/corpus/bootstrap/chunks.json")
    )
    parser.add_argument("--gold", type=Path, default=Path("evaluation/datasets/pilot-gold-v1.json"))
    parser.add_argument(
        "--private-dir", type=Path, default=Path("reports/generated/private/answer-eval")
    )
    parser.add_argument(
        "--output", type=Path, default=Path("reports/generated/research/s33-v02-batch-report.json")
    )
    parser.add_argument("--interval-seconds", type=float, default=2.0)
    parser.add_argument("--max-cases", type=int, default=10)
    parser.add_argument(
        "--case-ids",
        type=str,
        default=None,
        help="Comma-separated frozen case IDs; use a NEW --private-dir for rerun experiments",
    )
    parser.add_argument(
        "--top-k",
        type=int,
        default=5,
        help="BM25 evidence count (1–15); use new checkpoint directory if changed",
    )
    parser.add_argument(
        "--budget-chars", type=int, default=9000, help="LLM evidence character budget (256–24000)"
    )
    parser.add_argument("--max-retries", type=int, default=3)
    parser.add_argument(
        "--retry-failed",
        action="store_true",
        help="Explicitly retry a saved provider error once; successful checkpoints are always reused",
    )
    parser.add_argument(
        "--timeout-seconds",
        type=float,
        default=60.0,
        help="Per-request timeout (1–120s); an ambiguous timeout is never auto-retried",
    )
    parser.add_argument(
        "--capture-raw-responses",
        action="store_true",
        help="Opt in: save unredacted model responses in private-dir for local audit ONLY",
    )
    parser.add_argument(
        "--citation-policy",
        choices=CITATION_POLICIES,
        default=CITATION_POLICY_COMPAT,
        help="compat preserves legacy single-sentence append; strict requires model inline citations",
    )
    parser.add_argument(
        "--max-citation-retries",
        type=int,
        choices=(0, 1),
        default=0,
        help="Opt-in: at most one citation-only repair request; new private directory required",
    )
    args = parser.parse_args()
    selected_ids = None
    if args.case_ids is not None:
        selected_ids = tuple(part.strip() for part in args.case_ids.split(","))
    chunks = load_locked_chunks(args.chunks)
    base_client = OpenAICompatibleChatClient.from_env()
    client = OpenAICompatibleChatClient(
        base_client.base_url,
        base_client.model,
        base_client.api_key,
        max_retries=args.max_retries,
        timeout_seconds=args.timeout_seconds,
    )
    report = collect(
        args.gold.read_bytes(),
        chunks,
        client,
        args.private_dir,
        interval_seconds=args.interval_seconds,
        max_cases=args.max_cases,
        top_k=args.top_k,
        budget_chars=args.budget_chars,
        retry_failed=args.retry_failed,
        case_ids=selected_ids,
        capture_raw_responses=args.capture_raw_responses,
        citation_policy=args.citation_policy,
        max_citation_retries=args.max_citation_retries,
    )
    write_safe_report(args.output, report)
    print(
        json.dumps(
            {
                "executed_cases": report["executed_cases"],
                "new_calls": report["batch"]["new_calls"],
                "resumed_cases": report["batch"]["resumed_cases"],
                "retried_failed_cases": report["batch"]["retried_failed_cases"],
                "halted_on_provider_error": report["batch"]["halted_on_provider_error"],
            }
        )
    )


if __name__ == "__main__":
    main()
