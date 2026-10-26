#!/usr/bin/env bash
# P2 S9.3 — online, OWNER-LOCAL remediation bundle preparation.
# Run ONLY from an unmodified *copy* of the curated public candidate root.
# It intentionally changes web/package.json and web/package-lock.json, which
# invalidates the SHA-256 release manifest until the INTERNAL curator regenerates it.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
if [[ ! -f PUBLIC-CURATION-MANIFEST.json || ! -f web/package-lock.json ]]; then
  echo 'ERROR: run only in a clean copy of the published public candidate' >&2
  exit 3
fi
command -v npm >/dev/null || { echo 'ERROR: npm is required' >&2; exit 3; }
command -v node >/dev/null || { echo 'ERROR: Node.js is required' >&2; exit 3; }
command -v python3 >/dev/null || { echo 'ERROR: python3 is required' >&2; exit 3; }
TMP="$(mktemp -d "$ROOT/.s93-npm-upgrade.XXXXXXXX")"
trap 'rm -rf "$TMP"' EXIT
mkdir -p "$TMP/cache" "$TMP/transport/web" "$TMP/transport/audit"
export npm_config_cache="$TMP/cache"
printf '[1/5] Upgrade ONLY Vitest to patched 4.1.12 in this working copy...\n'
(
  cd web
  npm install --save-dev --save-exact vitest@4.1.12
  npm ci
)
printf '[2/5] Execute frontend compatibility checks...\n'
(cd web && npm run check)
printf '[3/5] Execute both live npm audits (they may exit nonzero on advisories)...\n'
(cd web && npm audit --json > "$TMP/transport/audit/npm-all.json") || true
(cd web && npm audit --omit=dev --json > "$TMP/transport/audit/npm-production.json") || true
cp web/package.json "$TMP/transport/web/package.json"
cp web/package-lock.json "$TMP/transport/web/package-lock.json"
node -v > "$TMP/transport/audit/node-version.txt"
npm -v > "$TMP/transport/audit/npm-version.txt"
printf '[4/5] Pack npm offline cache for replay...\n'
tar -czf "$TMP/transport/npm-cache.tar.gz" -C "$TMP" cache/_cacache
OUT="${S93_TRANSPORT_ZIP:-$HOME/Desktop/p2-s93-vitest4-retest.zip}"
mkdir -p "$(dirname "$OUT")"
if [[ -e "$OUT" ]]; then echo "ERROR: output already exists: $OUT" >&2; exit 3; fi
(cd "$TMP/transport" && zip -q -r "$OUT" web audit npm-cache.tar.gz)
unzip -tq "$OUT" >/dev/null
printf '[5/5] Check advisories; do not declare clearance if vulnerabilities remain...\n'
if node - "$TMP/transport/audit/npm-all.json" "$TMP/transport/audit/npm-production.json" <<'NODE'
const fs=require('node:fs');
let unresolved=0;
for (const path of process.argv.slice(2)) {
  const report=JSON.parse(fs.readFileSync(path,'utf8'));
  if (!report.metadata?.vulnerabilities) { throw new Error('Invalid npm audit JSON: '+path); }
  const counts=report.metadata.vulnerabilities;
  console.log(path.split('/').at(-1)+': total='+counts.total+', critical='+counts.critical+', high='+counts.high);
  unresolved+=counts.total;
}
if (unresolved) {
  console.error('SECURITY_HOLD: vulnerabilities remain. Upload ZIP for review; do not publish.');
  process.exitCode=2;
} else {
  console.log('NO_KNOWN_NPM_FINDINGS at scan time; upload ZIP for independent replay.');
}
NODE
then
  echo 'Audit advisory check completed with no reported findings.'
else
  echo 'IMPORTANT: Audit findings or audit parsing errors remain; upload archive for review.' >&2
fi
printf 'Transport archive: %s\n' "$OUT"
echo 'NOTE: LOCAL public manifest now mismatches changed lockfiles. Do not edit manifest; regenerate from internal curator.'
