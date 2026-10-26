#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
if [[ -n "${P2_OFFLINE_WHEELHOUSE_DIR:-}" ]]; then
  uv lock --check --offline
  requirements="$(mktemp)"
  trap 'rm -f "$requirements"' EXIT
  uv export --locked --group dev --no-emit-project --no-hashes \
    --output-file "$requirements"
  if [[ ! -e .venv/bin/python ]]; then
    uv venv --python 3.13 .venv
  fi
  uv pip install --python .venv/bin/python --no-index \
    --find-links "$P2_OFFLINE_WHEELHOUSE_DIR" -r "$requirements"
  export PYTHONPATH="$PWD/src:$PWD${PYTHONPATH:+:$PYTHONPATH}"
else
  uv sync --locked --dev
fi
uv run --no-sync ruff check .
uv run --no-sync ruff format --check .
uv run --no-sync mypy
uv run --no-sync pytest -q tests/public
