#!/usr/bin/env bash
# Every check a change must pass before it is called done. Stops at the first failure and
# exits non-zero, so a failure can't be hidden by piping the output somewhere.
set -euo pipefail
cd "$(dirname "$0")/.."

ruff check .
ruff format --check .
mypy
pytest -m "not network" -q
cd frontend
npm test
npm run build
echo "All checks passed."
