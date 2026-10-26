"""Thin command line entrypoint for P2 research infrastructure."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from p2_research.application.corpus.acquisition import AcquisitionError, acquire_document
from p2_research.application.corpus.bootstrap import (
    BootstrapError,
    build_corpus,
    verify_artifacts,
    write_artifact,
)
from p2_research.application.corpus.chunking import (
    ChunkingError,
    chunk_cached_snapshot,
    chunks_manifest,
)
from p2_research.application.corpus.license_evidence import (
    collect_license_evidence,
    evidence_digest,
    license_evidence_report,
)
from p2_research.application.corpus.provenance import (
    DocumentProvenance,
    ProvenanceError,
    audit_snapshot,
    manifest_sha256,
    provenance_manifest,
    require_public_distribution_allowed,
)
from p2_research.application.corpus.source_contracts import (
    RegistryValidationError,
    load_source_contracts,
)
from p2_research.application.corpus.source_review import (
    SourceReviewError,
    load_file_reviews,
    verify_upstream_commit,
)
from p2_research.application.evidence.annotations import AnnotationError, validate_pilot_files
from p2_research.application.evidence.candidate_freeze import (
    CandidateGateError,
    validate_candidate_files,
)
from p2_research.application.evidence.gold_freeze import GoldFreezeError, validate_gold_files
from p2_research.application.evidence.review_gate import ReviewGateError, validate_preaudit_files
from p2_research.application.retrieval.benchmark import benchmark, write_report
from p2_research.application.retrieval.bm25 import BM25Index, RetrievalError, load_locked_chunks


def main() -> int:
    parser = argparse.ArgumentParser(
        prog="p2", description="P2 Research Agent (Stage 0 foundation / Stage 1 corpus)"
    )
    parser.add_argument("--version", action="version", version="p2 0.0.1")
    sub = parser.add_subparsers(dest="domain")
    evaluation = sub.add_parser("eval", help="Frozen annotation pilot validation")
    eval_actions = evaluation.add_subparsers(dest="eval_action")
    pilot_check = eval_actions.add_parser(
        "validate-pilot", help="Validate evidence spans against frozen chunks"
    )
    pilot_check.add_argument("--pilot", type=Path, default=Path("evaluation/pilot-v0.1.json"))
    pilot_check.add_argument(
        "--chunks", type=Path, default=Path("reports/generated/corpus/bootstrap/chunks.json")
    )
    review_check = eval_actions.add_parser(
        "validate-review", help="Validate human review gate without auto-approval"
    )
    review_check.add_argument(
        "--review", type=Path, default=Path("evaluation/review/pilot-v0.2-preaudit.json")
    )
    review_check.add_argument("--pilot", type=Path, default=Path("evaluation/pilot-v0.1.json"))
    review_check.add_argument(
        "--chunks", type=Path, default=Path("reports/generated/corpus/bootstrap/chunks.json")
    )
    candidate_check = eval_actions.add_parser(
        "validate-candidate", help="Validate revised pilot without Gold promotion"
    )
    candidate_check.add_argument(
        "--candidate", type=Path, default=Path("evaluation/pilot-v0.3-candidate.json")
    )
    candidate_check.add_argument("--pilot", type=Path, default=Path("evaluation/pilot-v0.1.json"))
    candidate_check.add_argument(
        "--chunks", type=Path, default=Path("reports/generated/corpus/bootstrap/chunks.json")
    )
    gold_check = eval_actions.add_parser("validate-gold", help="Verify final signed Gold dataset")
    gold_check.add_argument(
        "--candidate", type=Path, default=Path("evaluation/pilot-v0.4-candidate.json")
    )
    gold_check.add_argument(
        "--signoff", type=Path, default=Path("evaluation/review/pilot-v0.5-signed-decisions.json")
    )
    gold_check.add_argument(
        "--gold", type=Path, default=Path("evaluation/datasets/pilot-gold-v1.json")
    )
    gold_check.add_argument(
        "--chunks", type=Path, default=Path("reports/generated/corpus/bootstrap/chunks.json")
    )
    corpus = sub.add_parser("corpus", help="Pinned-source corpus commands")
    actions = corpus.add_subparsers(dest="action")
    fetch = actions.add_parser("fetch", help="Fetch locked documents and verify local cache")
    fetch.add_argument("--source-dir", type=Path, default=Path("data_sources"))
    fetch.add_argument("--cache-dir", type=Path, default=Path("data/cache/corpus"))
    fetch.add_argument("--profile", choices=["demo", "research"], default="demo")
    fetch.add_argument("--offline", action="store_true")
    audit = actions.add_parser("audit", help="Verify cached provenance and license gates offline")
    audit.add_argument("--source-dir", type=Path, default=Path("data_sources"))
    audit.add_argument("--cache-dir", type=Path, default=Path("data/cache/corpus"))
    audit.add_argument("--profile", choices=["demo", "research"], default="demo")
    audit.add_argument("--output", type=Path, help="Write deterministic provenance manifest")
    audit.add_argument("--public-release-check", action="store_true")
    verify = actions.add_parser(
        "verify-upstream", help="Check pinned GitHub commit identity online"
    )
    verify.add_argument("--source-dir", type=Path, default=Path("data_sources"))
    verify.add_argument("--snapshot-id", default="dubbo-resilience-legacy-candidate-01")
    reviews = actions.add_parser("review-files", help="Validate tracked license review records")
    reviews.add_argument("--source-dir", type=Path, default=Path("data_sources"))
    reviews.add_argument("--snapshot-id", default="dubbo-resilience-legacy-candidate-01")
    reviews.add_argument(
        "--review-file", type=Path, default=Path("data_sources/license-reviews.json")
    )
    evidence = actions.add_parser(
        "license-evidence", help="Inventory file-level license signals offline"
    )
    evidence.add_argument("--source-dir", type=Path, default=Path("data_sources"))
    evidence.add_argument("--cache-dir", type=Path, default=Path("data/cache/corpus"))
    evidence.add_argument("--snapshot-id", default="dubbo-resilience-legacy-candidate-01")
    evidence.add_argument(
        "--review-file", type=Path, default=Path("data_sources/license-reviews.json")
    )
    evidence.add_argument(
        "--output", type=Path, default=Path("reports/generated/corpus/license-evidence.json")
    )
    chunks = actions.add_parser("chunk", help="Build byte-exact chunks from verified offline cache")
    chunks.add_argument("--source-dir", type=Path, default=Path("data_sources"))
    chunks.add_argument("--cache-dir", type=Path, default=Path("data/cache/corpus"))
    chunks.add_argument("--snapshot-id", default="dubbo-resilience-legacy-candidate-01")
    chunks.add_argument("--max-chunk-bytes", type=int, default=2048)
    chunks.add_argument("--output", type=Path, default=Path("reports/generated/corpus/chunks.json"))
    bootstrap = actions.add_parser("bootstrap", help="Build a reproducible pinned corpus")
    check = actions.add_parser("verify", help="Rebuild and compare pinned corpus digests")
    for command in (bootstrap, check):
        command.add_argument("--source-dir", type=Path, default=Path("data_sources"))
        command.add_argument("--cache-dir", type=Path, default=Path("data/cache/corpus"))
        command.add_argument("--profile", choices=["demo", "research"], default="research")
        command.add_argument("--max-chunk-bytes", type=int, default=2048)
        command.add_argument(
            "--output-dir", type=Path, default=Path("reports/generated/corpus/bootstrap")
        )
        command.add_argument("--expected-chunks-sha256")
    bootstrap.add_argument("--locked", action="store_true", required=True)
    bootstrap.add_argument("--offline", action="store_true")
    retrieval = sub.add_parser("retrieval", help="Stage 2 frozen BM25 baseline")
    retrieval_actions = retrieval.add_subparsers(dest="retrieval_action")
    for action in ("search", "benchmark"):
        r = retrieval_actions.add_parser(action)
        r.add_argument(
            "--chunks", type=Path, default=Path("reports/generated/corpus/bootstrap/chunks.json")
        )
        if action == "search":
            r.add_argument("--question", required=True)
            r.add_argument("--top-k", type=int, default=5)
        else:
            r.add_argument(
                "--gold", type=Path, default=Path("evaluation/datasets/pilot-gold-v1.json")
            )
            r.add_argument(
                "--output", type=Path, default=Path("reports/generated/retrieval/bm25-v01.json")
            )
    args = parser.parse_args()
    if args.domain == "retrieval":
        try:
            index = BM25Index(load_locked_chunks(args.chunks))
            if args.retrieval_action == "search":
                print(
                    json.dumps(
                        [hit.as_dict() for hit in index.search(args.question, args.top_k)],
                        ensure_ascii=False,
                        indent=2,
                    )
                )
                return 0
            if args.retrieval_action == "benchmark":
                bm25_report = benchmark(index, args.gold.read_bytes())
                write_report(args.output, bm25_report)
                print(
                    json.dumps(
                        {
                            k: bm25_report[k]
                            for k in ("answerable_cases", "recall_at_k", "mrr_at_max_k")
                        },
                        sort_keys=True,
                    )
                )
                return 0
        except (RetrievalError, OSError) as exc:
            parser.exit(2, f"retrieval failed: {exc}\n")
        retrieval.print_help()
        return 0
    if args.domain == "eval":
        if args.eval_action == "validate-gold":
            try:
                gold_summary = validate_gold_files(
                    args.candidate, args.signoff, args.gold, args.chunks
                )
            except GoldFreezeError as exc:
                parser.exit(2, f"Gold validation failed: {exc}\n")
            print(f"Gold dataset validated: {json.dumps(gold_summary, sort_keys=True)}")
            return 0
        if args.eval_action == "validate-candidate":
            try:
                candidate_summary = validate_candidate_files(
                    args.candidate, args.pilot, args.chunks
                )
            except CandidateGateError as exc:
                parser.exit(2, f"candidate validation failed: {exc}\n")
            print(
                f"Candidate evidence validated: {json.dumps(candidate_summary, sort_keys=True)}; "
                "all editorial reviews pending, Gold release blocked"
            )
            return 0
        if args.eval_action == "validate-review":
            try:
                review_result = validate_preaudit_files(args.review, args.pilot, args.chunks)
            except ReviewGateError as exc:
                parser.exit(2, f"review validation failed: {exc}\n")
            print(
                f"Review gate checked: {json.dumps(review_result, sort_keys=True)}; no automatic approval"
            )
            return 0
        if args.eval_action != "validate-pilot":
            evaluation.print_help()
            return 0
        try:
            pilot_summary = validate_pilot_files(args.pilot, args.chunks)
        except AnnotationError as exc:
            parser.exit(2, f"pilot validation failed: {exc}\n")
        print(
            f"Pilot evidence validated: {json.dumps(pilot_summary, sort_keys=True)}; editorial review remains required"
        )
        return 0
    if args.domain != "corpus" or args.action not in {
        "fetch",
        "audit",
        "verify-upstream",
        "review-files",
        "license-evidence",
        "chunk",
        "bootstrap",
        "verify",
    }:
        if args.domain is None:
            parser.print_help()
        else:
            corpus.print_help()
        return 0
    try:
        if args.action in {"bootstrap", "verify"}:
            if args.action == "bootstrap" and not args.locked:
                raise BootstrapError("immutable snapshot lock required")
            artifacts = build_corpus(
                args.source_dir,
                args.cache_dir,
                profile=args.profile,
                offline=(args.action == "verify" or args.offline),
                max_chunk_bytes=args.max_chunk_bytes,
            )
            saved = args.output_dir / "run.json"
            if args.action == "verify" and (not saved.is_file() or saved.is_symlink()):
                raise BootstrapError("RUN_MANIFEST_MISSING: bootstrap must precede verification")
            verify_artifacts(
                artifacts,
                expected_chunks_sha256=args.expected_chunks_sha256,
                saved_run=saved.read_bytes()
                if args.action == "verify" and saved.exists()
                else None,
            )
            if args.action == "bootstrap":
                write_artifact(args.output_dir / "provenance.json", artifacts.provenance)
                write_artifact(args.output_dir / "chunks.json", artifacts.chunks)
                write_artifact(saved, artifacts.run)
            print(
                f"{args.action}: {artifacts.document_count} locked documents, "
                f"{artifacts.chunk_count} chunks; run metadata: {artifacts.run.decode().strip()}"
            )
            return 0
        _, snapshots = load_source_contracts(args.source_dir)
        if args.action == "chunk":
            snapshot = snapshots[args.snapshot_id]
            chunk_items = chunk_cached_snapshot(
                snapshot, args.cache_dir, max_chunk_bytes=args.max_chunk_bytes
            )
            report = chunks_manifest(chunk_items)
            if args.output.is_symlink():
                raise ChunkingError("refusing chunk output symlink")
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_bytes(report)
            print(
                f"Chunked {len(snapshot.documents)} locked documents into {len(chunk_items)} byte-exact chunks"
            )
            return 0
        if args.action == "verify-upstream":
            snapshot = snapshots[args.snapshot_id]
            tree = verify_upstream_commit(snapshot)
            print(f"Verified pinned upstream commit identity; tree SHA: {tree}")
            return 0
        if args.action == "license-evidence":
            snapshot = snapshots[args.snapshot_id]
            entries = load_file_reviews(args.review_file, snapshot)
            items = collect_license_evidence(snapshot, entries, args.cache_dir)
            report = license_evidence_report(items, snapshot)
            if args.output.is_symlink():
                raise SourceReviewError("refusing license evidence output symlink")
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_bytes(report)
            available = sum(item.cache_status == "verified_cache" for item in items)
            print(
                f"License triage: {available}/{len(items)} cached, review required; SHA-256: {evidence_digest(report)}"
            )
            return 0
        if args.action == "review-files":
            snapshot = snapshots[args.snapshot_id]
            entries = load_file_reviews(args.review_file, snapshot)
            pending = sum(item.decision == "pending" for item in entries)
            print(f"Validated {len(entries)} file review records; pending: {pending}")
            return 0
        config = json.loads((args.source_dir / "snapshots.lock.json").read_text(encoding="utf-8"))
        selection = config["profiles"][args.profile]
        permitted = set(selection["languages"])
        count = 0
        audited: list[DocumentProvenance] = []
        for snapshot_id in selection["snapshot_ids"]:
            snapshot = snapshots[snapshot_id]
            if args.action == "audit":
                # Audit exactly the selected locked files. No networking occurs.
                documents = tuple(doc for doc in snapshot.documents if doc.language in permitted)
                selected = type(snapshot)(
                    snapshot.snapshot_id,
                    snapshot.source_id,
                    snapshot.repository,
                    snapshot.commit_sha,
                    snapshot.upstream_verification,
                    documents,
                )
                audited.extend(audit_snapshot(selected, args.cache_dir))
            else:
                for document in snapshot.documents:
                    if document.language not in permitted:
                        continue
                    result = acquire_document(
                        snapshot, document, args.cache_dir, offline=args.offline
                    )
                    count += 1
                    print(f"{result.source_path}: {'cache' if result.from_cache else 'fetched'}")
        if args.action == "audit":
            if args.public_release_check:
                for item in audited:
                    require_public_distribution_allowed(item)
            manifest = provenance_manifest(tuple(audited))
            print(
                f"Audited {len(audited)} documents; manifest SHA-256: {manifest_sha256(tuple(audited))}"
            )
            if args.output:
                if args.output.is_symlink():
                    raise ProvenanceError("refusing manifest output symlink")
                args.output.parent.mkdir(parents=True, exist_ok=True)
                args.output.write_bytes(manifest)
        else:
            print(f"Verified {count} locked documents (no publishing permission inferred).")
    except (
        AcquisitionError,
        BootstrapError,
        SourceReviewError,
        ProvenanceError,
        RegistryValidationError,
        OSError,
        ValueError,
        KeyError,
    ) as exc:
        parser.exit(2, f"corpus acquisition failed: {exc}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
