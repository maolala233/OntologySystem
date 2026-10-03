#!/usr/bin/env bash
# ==============================================================================
# dev-check.sh —— 本地一键质量门（CI 的等价物，docs/design/08 §2 M0 任务 7）
# 用法：bash build-scripts/dev-check.sh
# ==============================================================================
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"

PY="$ROOT/.venv/Scripts/python.exe"   # Windows Git Bash
[ -f "$PY" ] || PY="$ROOT/.venv/bin/python"
[ -x "$PY" ] || PY=python

echo "== 1/3 backend: ruff =="
(cd "$ROOT/src/backend" && "$PY" -m ruff check app)

echo "== 2/3 backend: pytest =="
(cd "$ROOT/src/backend" && "$PY" -m pytest)

echo "== 3/3 frontend: tsc + vite build =="
(cd "$ROOT/src/frontend" && npx tsc && npx vite build)

echo "== ALL GREEN =="
