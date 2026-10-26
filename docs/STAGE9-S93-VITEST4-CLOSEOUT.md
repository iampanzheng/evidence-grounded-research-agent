# P2 S9.3 — Vitest 4 security remediation (2026-10-26)

**Decision:** `ENGINEERING_VERIFIED / OWNER_ONLINE_NPM_SCAN_ZERO / HOLD_PUBLICATION`.

## Owner-provided scan and provenance

The owner supplied `p2-s93-vitest4-retest.zip` containing `web/package.json`, `web/package-lock.json`, `audit/npm-all.json`, `audit/npm-production.json`, npm/Node version strings, and a macOS-created offline npm cache. SHA-256 identifiers for the exact lock and scan JSON files are in `docs/verification/STAGE9-S93-VITEST4-SECURITY-CLEARANCE.json`. The audit JSON reports **0 known vulnerabilities for all npm dependencies** and **0 for production-only** at scan time. Node was 24.21.0 and npm 11.19.0. The prior Python runtime audit reported zero findings on the unchanged Python lock. These time-bounded vendor advisories do not guarantee perpetual vulnerability absence.

## Locked dependency diff

- Direct: Vitest `3.2.7` -> `4.1.12`; `vite` stays `7.3.7`; no Python lock modifications.
- Transitive: `@vitest/mocker` and `@vitest/runner` resolve to `4.1.12`; `tinypool` no longer appears in the resolved lock.
- `web/package.json` and `web/package-lock.json` were imported from owner-supplied files rather than re-resolved offline. No `npm audit fix --force` was used.

## Clean Git staging fix

Release dry-run identified a previously missed `.gitignore` problem: unanchored `corpus/` was recursively ignoring the original application source folder `src/p2_research/application/corpus/`. The generated public `.gitignore` now ignores **only `/corpus/` at repository root** (and other private root paths), with an automated real-Git regression test. The candidate must pass a fresh `git init; git add -A` inventory comparison against the allowlist manifest. No private data was included in the previous ZIP; the risk was accidentally omitting eight essential source modules from GitHub.

## Engineering boundaries

- Same offline app source and tests; only npm lock/manifest, release inventory, curation governance and documentation updated. No retrieval, LangGraph, frozen Gold, synthetic fixture or provider behavior changed.
- Independent synthetic HTTP Demo must continue to use real BM25 -> LangGraph -> FastAPI, with an explicitly deterministic mock LLM and **zero Groq calls**.
- Current public ZIP is an allowlisted **unpublished source candidate**, not a deployment image or a licence opinion. Owner confirmed third-party LICENSE/NOTICE review and Apache-2.0 original attribution `Copyright 2026 Pan Zheng`.
- **Remaining conditions:** review staged contents of a *new clean* Git repository, specify the public destination, obtain explicit owner's push/publication approval. Run new vulnerability scans again if release is delayed or dependencies change. The original private Git history was *not* reviewed for public release and must never be transferred.
