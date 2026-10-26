# Third-party dependency license inventory — review, not legal clearance

The machine-readable `THIRD-PARTY-LICENSE-INVENTORY.json` is derived from pinned `uv.lock`, pinned npm lock declarations, and the exact wheel METADATA available during the S9.3 audit. **A package metadata license string is not an independent review of every bundled license file, NOTICE, vendored asset or indirect obligation.**

Inventory scope:

- Python runtime packages were determined with `uv export --locked --no-dev` and checked against version-identical wheel metadata; Python dev-only and optional dependencies are listed separately.
- npm package license labels come from `web/package-lock.json`, and include dev/build dependencies; no claim has been made that all their full licenses have been examined.
- Some Python runtime distributions declare **MPL-2.0**; package compliance and notice obligations should be checked before redistribution of built binary artifacts. The public release candidate only distributes project code and lock files, not third-party wheels or node_modules.
- The npm package `caniuse-lite` declares **CC-BY-4.0**. Attribution/notice obligations must be considered separately from MIT/ISC/Apache/BSD dependencies.
- Optional and platform-specific packages may be present in the lock without being installed in the public demo. They are not silently classified as published runtime dependencies.

No bundled dependency distributions are being redistributed in the source archive. Dependencies are obtained by the end-user's package manager; this does **not** eliminate a publisher's responsibility to respect dependency licenses, especially when shipping containers or compiled artifacts later.

**Open gates:** independent license/notice review for flagged distributions, third-party security/vulnerability scan, owner review of this clean public tree, and explicit authorization before any push. No release has been published.

## Dependency remediation inventory refresh (2026-10-26)

The attached machine-readable inventory now derives from the refreshed owner-supplied `uv.lock` and `web/package-lock.json`: Python runtime 45 version-matched wheel METADATA records, 109 Python lock entries (including the local project/optional packages), and 211 npm lock entries with license declarations. Versioned runtime entries include FastAPI 0.142.2, Starlette 1.10.0, certifi 2026.10.23, and opentelemetry-api 1.45.1; Vite/Vitest development pins are 7.3.7/3.2.7. The count of all lock entries must not be confused with the number of redistributed or installed production packages.

This update **does not** assert that new dependencies have been freshly scanned against online vulnerability databases. `certifi` and `orjson` retain MPL-related declarations; `caniuse-lite` retains CC-BY-4.0. The final license/NOTICE review is still outstanding; refer to `docs/RELEASE-CHECKLIST.md`.


## Owner review (2026-10-26)

Owner confirmed review of the third-party LICENSE/NOTICE material. This is a record of the owner’s review, **not** an automatic blanket relicensing of dependencies or source corpus and **not** a release approval. The latest npm development vulnerabilities still block public release; see `docs/STAGE9-S93-OWNER-SECURITY-COPYRIGHT.md`.

## Vitest 4.1.12 refreshed inventory (2026-10-26)

Rebuilt from the exact new npm lock: Vitest/@vitest/mocker are 4.1.12, Vite remains 7.3.7. Owner reports **0 npm full and 0 production vulnerabilities** at scan time; this is not continuous scanning or a legal opinion. The owner has confirmed review of third-party LICENSE/NOTICE; no third-party code or original Dubbo corpus has been relicensed. Latest package counts are in `docs/THIRD-PARTY-LICENSE-INVENTORY.json`.
