#!/usr/bin/env bash
# Serve the built Web app in one foreground process; Ctrl+C stops this process.
set -euo pipefail
cd -- "$(dirname "${BASH_SOURCE[0]}")"

if ! command -v uv >/dev/null 2>&1; then
  echo "uv is required. Follow the installation steps in README.md." >&2
  exit 1
fi
if [[ ! -f web/dist/index.html ]]; then
  echo "Build the frontend first: cd web && npm ci && npm run build" >&2
  exit 1
fi
echo "Open http://127.0.0.1:8000 in your browser. Ctrl+C stops the server."
exec uv run --locked uvicorn server:app --host 127.0.0.1 --port 8000
