#!/usr/bin/env bash
# Run on the test machine. Model/search dependencies are replaced inside Python tests.
set -euo pipefail
cd -- "$(dirname "${BASH_SOURCE[0]}")"
uv run --locked python _test_hardening.py
FORCE_STUB_FASTAPI=1 uv run --locked python _test_hardening.py
uv run --locked python _test_hardening_fc.py
uv run --locked python _test_eval.py
uv run --locked python _test_web.py
uv run --locked python _test_reasoning.py
(
  cd web
  npm run lint
  npm run build
)
echo "Regression checks and frontend build completed."
