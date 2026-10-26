# Final public-source release checklist (approval recorded; no push performed)

**Current (2026-10-26):** The owner has approved **Apache-2.0**, the exclusion of real Dubbo data and private Git history, third-party LICENSE/NOTICE review, and the intended destination **`iampanzheng/evidence-grounded-research-agent`**. The owner requested a clearer Portfolio presentation **before** the first push. This refreshed source candidate must have its final staged file list and new README reviewed; specifying the repository is not itself a completed GitHub publish. Latest provided owner scans (2026-10-26) reported **0 findings** for the unchanged Python runtime lock and refreshed full + production npm lock. Later vulnerability advisories are possible.

S9.3 ships a curated **source candidate**, not a GitHub release. The following steps apply to a clean, separately initialized repository, **never push the private project's Git history**.

1. Extract the public ZIP into an empty directory. Verify its checksums and no unexpected paths before installing dependencies:

   ```bash
   python tools/s93_release_gate.py --root . --enforce
   ```

   Expected: `CONTENT_VERIFIED_OWNER_CLEARANCE_PENDING`. This is an integrity result, **not** permission to publish.

2. Run `uv sync --locked --dev`, `bash scripts/verify-public.sh`, `uv run python tools/public_demo_smoke.py`, and `cd web && npm ci && npm run check`.
3. Compare `docs/THIRD-PARTY-LICENSE-INVENTORY.json` against current upstream dependency notices and licenses, especially MPL-2.0 and `caniuse-lite` CC-BY-4.0; confirm license and attribution treatment of redistributed artifacts.
4. Run a fresh vulnerability scan (for example `uv pip audit` where supported, `pip-audit` / OSV and `npm audit`), and document unresolved advisories and dates. **This offline artifact does not contain a current vulnerability scan.**
5. Inspect LICENSE / NOTICE and ensure synthetic Orion fixture is original. Review README claims and demo reproducibility.
   Verify the three genuine UI screenshots under `docs/assets/` are included in the generated manifest, display only synthetic Orion responses and a masked local API key, and are linked from README/DEMO-GUIDE. No manual image injection after curation.
6. Initialize a **new** Git repo in this curated directory (do not add private Git remotes or import private commits); run `git status`, `git ls-files`, and inspect `git log --all` after the first commit.
7. Confirm the already-approved destination and scope still match the **final refreshed source tree**, have the owner review the clean staged commit, and obtain go-ahead for the exact refreshed release before pushing. No step in this package invokes `git push` or creates a remote repository.

Never commit `.env`, API credentials, local artifacts, `node_modules`, `.venv`, private corpus/Gold, raw responses, test caches or Docker build outputs.

## After S9.3 security-remediation lock update

Before requesting final publish authorization, run the following **online** commands in a clean checkout of the updated public ZIP (results are local audit artifacts, not Git-committed reports):

```bash
mkdir -p artifacts/local/release-security
uv export --locked --no-dev --no-hashes --no-emit-project -o artifacts/local/release-security/python-runtime.txt
uvx --from pip-audit pip-audit -r artifacts/local/release-security/python-runtime.txt --no-deps --disable-pip -f json -o artifacts/local/release-security/python-audit.json
(cd web && npm audit --json > ../artifacts/local/release-security/npm-all.json)
(cd web && npm audit --omit=dev --json > ../artifacts/local/release-security/npm-production.json)
```

Nonzero exit codes due to reported advisories are expected; preserve the JSONs. **Do not run automatic `npm audit fix` or rewrite lock files** without a new compatibility run. Obtain owner final approval after reviewing these updated scan results and third-party license obligations.

## Owner scan 2026-10-26

Owner confirmed third-party LICENSE/NOTICE review and specified `Copyright 2026 Pan Zheng`. Latest submitted scans report 0 Python runtime and 0 npm production findings, but **3 npm development dependency findings** (Vitest, `@vitest/mocker`, tinypool). Treat as `HOLD_PUBLICATION`; upgrade and re-run both npm audits and complete tests before reconsidering clearance. Never edit `PUBLIC-CURATION-MANIFEST.json` by hand.

## Latest Vitest 4 scan decision (2026-10-26)

The earlier three npm development findings have been remediated by the owner-provided pinned Vitest 4.1.12 lock and confirmed with owner online full/production npm audit (both 0 reported vulnerabilities). The Python runtime lock did not change from its last 0-finding scan. See `docs/STAGE9-S93-VITEST4-CLOSEOUT.md`. **Do not use historical 3.2.7 audit results as the current state.** Original private Git history is not part of the clean candidate; explicit owner push authorization for a named new repository, last review of staged commit, and future rescan if release is delayed are still mandatory.
