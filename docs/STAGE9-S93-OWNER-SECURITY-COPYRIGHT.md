# S9.3 owner security audit and copyright update — 2026-10-26

**Historical snapshot:** the three npm dev findings described below were subsequently cleared by Vitest 4.1.12; current status: `docs/STAGE9-S93-VITEST4-CLOSEOUT.md`. Historical 3.2.7 evidence is retained unchanged.

The project owner confirmed the third-party LICENSE/NOTICE review and requested the original application copyright notice: **Copyright 2026 Pan Zheng**. The root Apache-2.0 LICENSE appendix and the original-project NOTICE now use the specified attribution; third-party dependencies retain their own licenses. This change does **not** authorize publication of the original Dubbo corpus.

## Supplied fresh live scan findings

- Python runtime `pip-audit`: **zero known vulnerabilities reported** on the submitted locks.
- npm production (`npm audit --omit=dev`): **zero known vulnerabilities reported**.
- npm full tree: **three affected development packages**: `vitest@3.2.7`, `@vitest/mocker@3.2.7` and `tinypool@1.1.1`; npm severity summary **two critical, one moderate**.
- The affected graph is only used in test/build tooling, but untrusted inputs in exposed development/test servers remain a meaningful risk; production-only zero findings do not clear this release policy.

### Required next step

Upgrade the dev/test graph with a pinned **Vitest 4.1.12** candidate and rerun `npm ci`, `npm run check`, and `npm audit` in a **temporary copy** of the new public candidate. Do not use `npm audit fix --force`; do not alter the hash manifest in place. Return the updated `web/package.json`, `web/package-lock.json` and latest full+production npm audit results for deterministic internal curation. Vitest 4.1.12 is a **major-version candidate** until tests and audit have both passed. The owner can run the included separate helper script from the candidate root to build the required offline cache as well.

### Integrity behavior

The earlier `FILE_HASH_MISMATCH:LICENSE` was a **correct** failure after editing the curated ZIP in place. For this delivery the copyright was changed in the INTERNAL source first, then the candidate was **regenerated using the original allowlist and SHA-256 builder**; no integrity bypass was added.

**Decision: HOLD_PUBLICATION** until the npm development dependencies are remediated and retested, the new clean Git commit is reviewed, and the owner authorizes a named target repository. The scan JSON was used to prepare a sanitized evidence summary; raw online audit files remain local-only.
