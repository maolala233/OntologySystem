#!/usr/bin/env bash
# ==============================================================================
# 启动 Celery parse worker（M3-2，docs/design/03 §7 / 01 §4.4）
#   bash run-worker.sh                # 默认 parse 队列，Windows 下 solo 池
#   QUEUE=parse POOL=threads bash run-worker.sh
# 必须在 src/backend 下运行（.env 按 CWD 读取，同 run-backend.sh）。
# ==============================================================================
set -uo pipefail
cd "$(dirname "$0")"

QUEUE="${QUEUE:-parse}"
POOL="${POOL:-solo}"       # Windows 开发默认 solo（稳定）；Linux/容器建议 threads -c 2
CONC="${CONC:-1}"
PY="${PY:-../../.venv/Scripts/python.exe}"

if [ ! -f .env ]; then
  echo "!! 缺少 .env —— 先在 src/backend 下配置"
  exit 1
fi

echo "== 启动 worker: queue=$QUEUE pool=$POOL conc=$CONC =="
exec "$PY" -m celery -A app.tasks.celery_app worker \
  -Q "$QUEUE" -n "worker-$QUEUE@%h" --pool="$POOL" --concurrency="$CONC" \
  --loglevel=INFO
