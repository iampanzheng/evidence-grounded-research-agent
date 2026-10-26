"""Private-only offline model response shape inspection; never prints answer text."""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any

_CITE = re.compile(r"\[(E[1-9][0-9]*)\]")


def inspect_response(content: str) -> dict[str, Any]:
    """Report structural facts without leaking any model-generated prose."""
    try:
        obj = json.loads(content)
    except (ValueError, TypeError):
        return {"valid_json": False, "error": "invalid_json"}
    if not isinstance(obj, dict):
        return {"valid_json": True, "object": False, "error": "not_an_object"}
    answer = obj.get("answer")
    citations = obj.get("citations")
    inline = _CITE.findall(answer) if isinstance(answer, str) else []
    return {
        "valid_json": True,
        "object": True,
        "expected_keys_present": sorted(k for k in ("status", "answer", "citations") if k in obj),
        "unexpected_key_count": sum(k not in ("status", "answer", "citations") for k in obj),
        "status_type": type(obj.get("status")).__name__,
        "status_known": obj.get("status") in ("ANSWERED", "INSUFFICIENT_EVIDENCE"),
        "answer_type": type(answer).__name__,
        "answer_nonempty": isinstance(answer, str) and bool(answer.strip()),
        "answer_characters": len(answer) if isinstance(answer, str) else None,
        "citations_type": type(citations).__name__,
        "citations_count": len(citations) if isinstance(citations, list) else None,
        "inline_citation_count": len(inline),
        "inline_array_same_set": (
            set(inline) == set(citations)
            if isinstance(citations, list) and all(isinstance(x, str) for x in citations)
            else None
        ),
    }


def inspect_private_dir(path: Path) -> dict[str, Any]:
    """Analyze explicitly captured response files without displaying raw contents."""
    if path.is_symlink() or not path.is_dir():
        raise ValueError("private input must be an existing non-symlink directory")
    cases: list[dict[str, Any]] = []
    for file in sorted(path.glob("*.raw-response.json")):
        if file.is_symlink():
            raise ValueError("raw response symlink refused")
        record = json.loads(file.read_text(encoding="utf-8"))
        if not isinstance(record, dict) or not isinstance(record.get("content"), str):
            raise ValueError("malformed raw response record")
        case_id = file.name.removesuffix(".raw-response.json")
        if record.get("case_id") != case_id:
            raise ValueError("case ID does not match filename")
        cases.append({"case_id": case_id, **inspect_response(record["content"])})
    return {
        "schema_version": 1,
        "captured_cases": len(cases),
        "cases": cases,
        "contains_source_text": False,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Metadata-only local raw response audit")
    parser.add_argument("--private-dir", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    report = inspect_private_dir(args.private_dir)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"captured_cases": report["captured_cases"]}))


if __name__ == "__main__":
    main()
