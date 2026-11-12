#!/usr/bin/env bash
# =============================================================================
# 从 GitHub 拉取后端代码并重启服务（适用于离线包部署、服务器上没有源码的机器）
#
# 两种模式：
#   MODE=overlay（默认，快）：以现有 ontology-backend 镜像为底座，只覆盖
#                            src/backend 代码。依赖不变时用它，通常 1 分钟内完成。
#   MODE=full（慢）：从 Dockerfile 重建基础镜像 + 应用镜像。requirements.txt
#                    变了才需要，4C8G 大约 15–30 分钟。
#
# 用法：
#   sudo BRANCH=feat_simplify bash pull-and-update.sh
#   sudo BRANCH=main MODE=full UPDATE_FRONTEND=1 bash pull-and-update.sh
#
# 环境变量：
#   REPO=https://github.com/maolala233/OntologySystem.git
#   MIRROR_PREFIXES="https://ghfast.top/ ..."  GitHub 直连失败时的镜像前缀
#   BRANCH=feat_simplify        要部署的分支
#   SRC_DIR=/opt/ontology-src   源码目录（不存在会自动 clone）
#   APP_DIR=/opt/ontology-middleware   现有部署目录（.env / compose override）
#   MODE=overlay                overlay | full
#   UPDATE_FRONTEND=0           1 = 同时重建前端
#   HEALTH_RETRIES=60           健康检查重试次数（每次 5 秒）
# =============================================================================
set -Eeuo pipefail

REPO="${REPO:-https://github.com/maolala233/OntologySystem.git}"
MIRROR_PREFIXES="${MIRROR_PREFIXES:-https://ghfast.top/ https://gh-proxy.com/ https://ghproxy.net/}"
BRANCH="${BRANCH:-feat_simplify}"
SRC_DIR="${SRC_DIR:-/opt/ontology-src}"
APP_DIR="${APP_DIR:-/opt/ontology-middleware}"
MODE="${MODE:-overlay}"
UPDATE_FRONTEND="${UPDATE_FRONTEND:-0}"
HEALTH_RETRIES="${HEALTH_RETRIES:-60}"

log()  { printf '\n\033[1;36m[%s]\033[0m %s\n' "$(date +%H:%M:%S)" "$*"; }
warn() { printf '\n\033[1;33m[WARN]\033[0m %s\n' "$*" >&2; }
die()  { printf '\n\033[1;31m[ERROR]\033[0m %s\n' "$*" >&2; exit 1; }

if [ "$(id -u)" -ne 0 ]; then
  if command -v sudo >/dev/null 2>&1; then
    exec sudo -E bash "$0" "$@"
  fi
  die "请用 root 运行：sudo bash $0"
fi

command -v docker >/dev/null 2>&1 || die "缺少 docker"
docker info >/dev/null 2>&1 || die "docker 未运行"

if ! command -v git >/dev/null 2>&1; then
  log "安装 git"
  if command -v apt-get >/dev/null 2>&1; then
    apt-get update -y && apt-get install -y git
  elif command -v yum >/dev/null 2>&1; then
    yum install -y git
  else
    die "缺少 git，请先手动安装"
  fi
fi

[ -f "$APP_DIR/docker-compose-middleware.yml" ] \
  || die "缺少 $APP_DIR/docker-compose-middleware.yml，APP_DIR 是否正确？"
[ -f "$APP_DIR/docker-compose-app-cloud.override.yml" ] \
  || die "缺少 app override，请先用 deploy-middleware.sh 完成一次部署"

# ---------------------------------------------------------------- 1. 拉代码
log "拉取代码：$REPO ($BRANCH)"
export GIT_TERMINAL_PROMPT=0

# 国内服务器直连 GitHub 常被重置；直连失败后依次尝试镜像前缀。
FETCHED=0
if [ -d "$SRC_DIR/.git" ]; then
  if git -C "$SRC_DIR" fetch --depth 1 origin "$BRANCH"; then
    FETCHED=1
  else
    warn "增量拉取失败，删除源码目录后重新克隆"
    rm -rf "$SRC_DIR"
  fi
fi

if [ "$FETCHED" = "0" ]; then
  if [ -e "$SRC_DIR" ]; then
    die "$SRC_DIR 已存在但不是 git 仓库；请移走它或指定其他 SRC_DIR"
  fi
  if ! git clone --depth 1 --branch "$BRANCH" "$REPO" "$SRC_DIR"; then
    for prefix in $MIRROR_PREFIXES; do
      warn "直连 GitHub 失败，尝试镜像：$prefix"
      rm -rf "$SRC_DIR"
      if git clone --depth 1 --branch "$BRANCH" "${prefix}${REPO}" "$SRC_DIR"; then
        break
      fi
    done
  fi
  [ -d "$SRC_DIR/.git" ] || die "拉取代码失败（直连与镜像均不可用），可指定 REPO 或 MIRROR_PREFIXES"
else
  git -C "$SRC_DIR" checkout -f -B "$BRANCH" FETCH_HEAD
fi
COMMIT="$(git -C "$SRC_DIR" log -1 --oneline)"
log "当前代码：$COMMIT"

# ---------------------------------------------------------------- 2. 备份镜像
if docker image inspect ontology-backend:latest >/dev/null 2>&1; then
  docker tag ontology-backend:latest ontology-backend:previous
  log "已备份旧后端镜像为 ontology-backend:previous"
fi

# ---------------------------------------------------------------- 3. 构建后端
if [ "$MODE" = "overlay" ]; then
  log "构建后端（overlay：只覆盖 src/backend 代码，不重装依赖）"
  BUILD_DIR="$(mktemp -d)"
  cat > "$BUILD_DIR/Dockerfile" <<'DOCKER'
FROM ontology-backend:latest
COPY src/backend/ /app/
DOCKER
  docker build -f "$BUILD_DIR/Dockerfile" -t ontology-backend:latest "$SRC_DIR"
  rm -rf "$BUILD_DIR"
elif [ "$MODE" = "full" ]; then
  log "构建后端（full：重建基础镜像，较慢）"

  # 基础镜像走国内源：先按标准名准备好，Dockerfile 里就不用改 FROM
  ensure_image() {
    local mirror="$1" name="$2"
    if ! docker image inspect "$name" >/dev/null 2>&1; then
      docker pull "$mirror" || die "拉取失败：$mirror"
      docker tag "$mirror" "$name"
    fi
  }
  ensure_image "docker.m.daocloud.io/library/python:3.11-slim" "python:3.11-slim"
  ensure_image "docker.m.daocloud.io/library/node:22-alpine" "node:22-alpine"
  ensure_image "docker.m.daocloud.io/library/nginx:alpine" "nginx:alpine"

  docker build --progress=plain \
    -f "$SRC_DIR/dockerfile/envbackend-slim.Dockerfile" \
    -t ontology-backend-env:0.0.4-slim "$SRC_DIR"
  docker build --progress=plain \
    --build-arg "BASE_IMAGE=ontology-backend-env:0.0.4-slim" \
    --build-arg "DEBIAN_MIRROR=${DEBIAN_MIRROR:-mirrors.aliyun.com}" \
    -f "$SRC_DIR/dockerfile/backend.Dockerfile" \
    -t ontology-backend:latest "$SRC_DIR"
else
  die "MODE 只支持 overlay / full，当前：$MODE"
fi

# ---------------------------------------------------------------- 4. 前端（可选）
if [ "$UPDATE_FRONTEND" = "1" ]; then
  log "重建前端镜像"
  ensure_image() {
    local mirror="$1" name="$2"
    if ! docker image inspect "$name" >/dev/null 2>&1; then
      docker pull "$mirror" || die "拉取失败：$mirror"
      docker tag "$mirror" "$name"
    fi
  }
  ensure_image "docker.m.daocloud.io/library/node:22-alpine" "node:22-alpine"
  ensure_image "docker.m.daocloud.io/library/nginx:alpine" "nginx:alpine"
  if docker image inspect ontology-frontend:latest >/dev/null 2>&1; then
    docker tag ontology-frontend:latest ontology-frontend:previous
  fi
  docker build --progress=plain \
    --build-arg "NODE_IMAGE=node:22-alpine" \
    --build-arg "NGINX_IMAGE=nginx:alpine" \
    --build-arg "NPM_REGISTRY=${NPM_REGISTRY:-https://registry.npmmirror.com}" \
    -f "$SRC_DIR/dockerfile/frontend-deploy.Dockerfile" \
    -t ontology-frontend:latest "$SRC_DIR"
fi

# ---------------------------------------------------------------- 5. 重启服务
log "重启后端与 worker"
cd "$APP_DIR"
COMPOSE=(docker compose
  -f docker-compose-middleware.yml
  -f docker-compose-middleware-cloud.override.yml
  -f docker-compose-app-cloud.override.yml)

"${COMPOSE[@]}" up -d --no-deps --force-recreate --pull never \
  backend worker-parse worker-extract worker-graph worker-rdf beat

if [ "$UPDATE_FRONTEND" = "1" ]; then
  log "重启前端"
  "${COMPOSE[@]}" up -d --no-deps --force-recreate --pull never frontend
else
  # nginx 在启动时解析 proxy_pass http://backend:3001 并缓存容器 IP；
  # backend 重建后 IP 会变，不重启 nginx 会一直 502。
  log "重启前端以刷新后端 DNS（不重建镜像）"
  docker restart ontology-frontend >/dev/null
fi

# ---------------------------------------------------------------- 6. 健康检查
API_PORT="$(awk -F= '$1 == "API_PORT" {print $2; exit}' "$APP_DIR/.env" 2>/dev/null || true)"
API_PORT="${API_PORT:-3001}"
log "等待后端健康（最长 $((HEALTH_RETRIES * 5)) 秒）"
OK=0
for _ in $(seq 1 "$HEALTH_RETRIES"); do
  if curl -fsS --max-time 3 "http://127.0.0.1:${API_PORT}/api/system/health" >/dev/null 2>&1; then
    OK=1
    break
  fi
  sleep 5
done

if [ "$OK" != "1" ]; then
  warn "健康检查失败，最近日志："
  "${COMPOSE[@]}" logs --tail=120 backend || true
  cat <<EOF

回滚命令：
  docker tag ontology-backend:previous ontology-backend:latest
  cd $APP_DIR && ${COMPOSE[*]} up -d --no-deps --force-recreate backend worker-parse worker-extract worker-graph worker-rdf beat
  docker restart ontology-frontend   # 刷新 nginx 缓存的后端 IP
EOF
  exit 1
fi

curl -sS "http://127.0.0.1:${API_PORT}/api/system/health"; echo

cat <<EOF

============================================================
 更新完成
------------------------------------------------------------
 分支：  $BRANCH
 提交：  $COMMIT
 模式：  $MODE
 前端：  $([ "$UPDATE_FRONTEND" = "1" ] && echo 已更新 || echo 未更新)

 回滚：
   docker tag ontology-backend:previous ontology-backend:latest
   cd $APP_DIR
   ${COMPOSE[*]} up -d --no-deps --force-recreate backend worker-parse worker-extract worker-graph worker-rdf beat
   docker restart ontology-frontend   # 刷新 nginx 缓存的后端 IP
============================================================
EOF
