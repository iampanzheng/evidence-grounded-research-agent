# Evaluation — What was measured, what failed, and what is not proven

This is a **public summary of historical internal experiments**, not an executable benchmark dataset. The original third-party text, 409-chunk frozen corpus, Gold questions, raw model responses and private evaluation artifacts are not distributed. The separate [Orion demo](../README.md#try-it-locally) measures integration behavior with a deterministic fake LLM, **not real-provider answer quality**.

## Stage 7: first-exposure evaluation

- **Scope:** 16 AI-authored, owner-approved questions, frozen before first execution against the same 24-document source corpus (12 answerable, 4 unanswerable).
- **Provider:** real Groq requests in the internal project, using the fixed research workflow.
- **Outcome:** **14/16** Gold-aligned outcomes passed (10/12 answerable questions, 4/4 appropriately unanswerable questions).
- **Failure analysis:** two answerable questions (**S73-PROP-002** and **S73-PROP-003**) failed because the necessary supporting evidence was missing from BM25 Top-5 results. Refusing in those cases avoided invention, but still counted as **incorrect against the answerable Gold**.
- **Measurement distinction:** exact nominated Gold chunk hit was **8/12**, which is not interchangeable with 10/12 answerable correctness or all-adequate-evidence retrieval coverage.

**Threats to validity:** This was first exposure to those questions but **not an external-domain holdout**. The questions were AI-authored and approved by the project owner; the owner confirmed an AI-assisted Gold-aligned assessment. It is not a blinded independent-expert assessment. Sample size is small; later tuning would require a new frozen, unexposed evaluation set.

## Stage 5: Stage 3 vs bounded LangGraph

The earlier eight-question sequential paired evaluation included six answerable and two unanswerable tasks using the same source corpus and a real provider.

| Measured item | Stage 3 baseline | Stage 5 bounded LangGraph |
| --- | ---: | ---: |
| Gold-aligned outcome | 8/8 | 8/8 |
| Supported answered cases (claim and citation review) | 6/6 | 6/6 |
| Correct refusal | 2/2 | 2/2 |
| Model calls | 8 | 9 |
| Mean latency on answered cases | 1,897.21 ms | 3,029.755 ms |

**Interpretation:** There was **no demonstrated answer-quality gain** from LangGraph. The workflow was retained for explicit state, budgets, bounded repair and diagnostically clear termination. Latency/call trade-offs were visible; token cost and provider billing were not fully observed. The evaluation was small, sequential and not a controlled single-variable ablation.

## Stage 8: operations evidence

The owner ran a local Docker Desktop **Linux ARM64** acceptance of a single-API-worker + separate static Web deployment. During a controlled API outage, the Web remained available; API health and authenticated telemetry recovered after restart. Containers used read-only root filesystems with CPU/memory/PID limits. This is **local operational fault/recovery evidence**, not multi-replica production validation. The container image scan was not executed and base-image registry digests were not enforced.

## What is reproducible in this public repository

| Claim | Reproducible here? | Test path |
| --- | --- | --- |
| Synthetic source integrity and chunk provenance | **Yes** | `bash scripts/verify-public.sh` |
| Real BM25 → LangGraph → FastAPI HTTP answer and refusal | **Yes**, with deterministic fake model | `uv run python tools/public_demo_smoke.py` |
| React/TypeScript UI and static build | **Yes** | `cd web && npm ci && npm run check` |
| Real Dubbo/Groq outcome metrics above | **No** | Original rights-restricted evaluation is not distributed |
| Docker outage/restart result above | **No** | Owner-local environment and deployment evidence are not shipped |

A structurally authorized citation does not establish that every semantic claim is true. The public demo reports `claim_support=NOT_VERIFIED`; no semantic entailment oracle is claimed.

For the running components and trust boundaries, see [ARCHITECTURE.md](ARCHITECTURE.md). For the two interview-friendly questions, see [DEMO-GUIDE.md](DEMO-GUIDE.md).
