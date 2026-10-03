#!/usr/bin/env bash
# ==============================================================================
# 本地启动后端（uvicorn）—— 中间件跑在 Docker，后端在宿主直接跑
# ==============================================================================
# 用法：
#   bash run-backend.sh                                  # 默认 0.0.0.0:3001 --reload
#   HOST_IP=127.0.0.1 bash run-backend.sh                # 后端就在这台 VM 上
#   PY=../.venv/bin/python bash run-backend.sh           # 指定虚拟环境
#   PORT=3002 bash run-backend.sh                        # 换端口
#
# 为什么必须先 cd 到脚本所在目录：
#   app/core/config.py 里是 env_file=".env" —— 相对【当前工作目录】解析；
#   UPLOAD_DIR / TEMP_DIR / UPLOAD_PROJECTS_DIR 也都是相对路径。
#   在别的地方启动会读不到 .env，并把 uploads/temp 建到错误的目录。
# ==============================================================================
set -uo pipefail
cd "$(dirname "$0")"

HOST_IP="${HOST_IP:-192.168.209.128}"
PORT="${PORT:-3001}"
# M0：默认使用项目根 .venv（Python 3.10，semantica 要求 >=3.10；docs/design/01 §4.2）
PY="${PY:-../../.venv/Scripts/python.exe}"
if ! command -v "$PY" >/dev/null 2>&1 && [ ! -f "$PY" ]; then PY=python; fi

echo "== 1. 配置文件 =="
if [ ! -f .env ]; then
  echo "  !! 缺少 .env —— 先执行：cp .env.example .env 再填写"
  exit 1
fi
echo "  OK   .env 存在"
if grep -q "CHANGE_ME" .env; then
  echo "  !! .env 里还有 CHANGE_ME —— 模型地址还没填"
  echo "     纯数据库/图联调可以先忽略；抽取、嵌入、RAG 一定会失败"
fi

echo "== 2. 中间件端口连通性（$HOST_IP）=="
fail=0
check() {
  if timeout 2 bash -c "exec 3<>/dev/tcp/$2/$3" 2>/dev/null; then
    echo "  OK   $1  $2:$3"
  else
    echo "  FAIL $1  $2:$3   <- 容器没起？端口没映射？防火墙？"
    fail=1
  fi
}
check MySQL  "$HOST_IP" 3309
check Neo4j  "$HOST_IP" 7687
check Milvus "$HOST_IP" 19530
[ "$fail" -eq 0 ] || echo "  （有端口不通；也可以先起来看具体报错）"

echo "== 3. 启动 uvicorn =="
echo "   接口 http://$HOST_IP:$PORT    文档 http://$HOST_IP:$PORT/docs"
echo "   注意 --reload：每次改代码都会重新 import main.py，"
echo "   而 main.py 在 import 时就执行 init_db()（建表 + 自动迁移 + 密码哈希迁移），"
echo "   所以热重载会多花几秒，属正常现象。"
echo
exec "$PY" -m uvicorn main:app --host 0.0.0.0 --port "$PORT" --reload
