#!/usr/bin/env bash
# ==============================================================================
# 通过国内镜像源拉取 OntologySystem 所需的全部镜像
# ==============================================================================
# 背景：Docker Hub（registry-1.docker.io / auth.docker.io）在国内多数网络下不可达，
#       直接 `docker pull minio/minio:xxx` 会卡住或超时。本脚本从可用的镜像源拉取，
#       再 `docker tag` 回**原始镜像名** —— 这样 docker-compose.yml 一个字都不用改。
#
# 用法：
#   bash build-scripts/pull-images.sh                        # 用默认镜像源
#   bash build-scripts/pull-images.sh docker.m.daocloud.io    # 换源重试
#
# 实测（2026-09）：
#   ✅ 可用   docker.1panel.live（已验证 manifest + layer blob 全通）
#             docker.m.daocloud.io / docker.1ms.run / hub.rat.dev
#   ❌ 不可达 dockerpull.org、mirror.ccs.tencentyun.com（仅腾讯云内网可用）
#   ❌ 被墙   registry-1.docker.io / auth.docker.io / hub.docker.com
#
# 注意：quay.io / ghcr.io / docker.elastic.co 实测可直连，不受影响。
# ==============================================================================
set -uo pipefail

MIRROR="${1:-docker.1panel.live}"
MIRROR="${MIRROR#https://}"
MIRROR="${MIRROR%/}"

# ---- 需要走 Docker Hub 的镜像 ----
HUB_IMAGES=(
  "minio/minio:RELEASE.2023-03-20T20-16-18Z"    # Milvus 内部对象存储
  "minio/minio:RELEASE.2024-06-13T22-53-53Z"    # 业务对象存储
  "minio/mc:RELEASE.2024-06-12T14-34-03Z"       # 建 bucket 的一次性任务
  "milvusdb/milvus:v2.3.5"                      # 向量库
  "zilliz/attu:v2.3.5"                          # Milvus 可视化
  "neo4j:5.26.17"                               # 图库
  "mysql:8.0"                                   # 关系库
  "redis:7-alpine"                              # 队列
  "adminer:4-standalone"                        # MySQL 可视化（profile: tools）
  "ollama/ollama:latest"                        # 模型容器化（profile: models）
)

# ---- 直连即可（已实测可达，不走 Docker Hub）----
DIRECT_IMAGES=(
  "quay.io/coreos/etcd:v3.5.5"
  "ghcr.io/oxigraph/oxigraph:latest"                        # profile: sparql
  "docker.elastic.co/elasticsearch/elasticsearch:8.15.0"    # profile: es
  "docker.elastic.co/kibana/kibana:8.15.0"                  # profile: es
)

# 单段名（官方镜像，如 mysql:8.0）在镜像源里需要补 library/ 前缀
mirror_ref() {
  local repo tag
  repo="${1%%:*}"; tag="${1##*:}"
  [[ "$repo" != */* ]] && repo="library/$repo"
  printf '%s/%s:%s' "$MIRROR" "$repo" "$tag"
}

ok=0; bad=0
echo "镜像源: $MIRROR"
echo

for img in "${HUB_IMAGES[@]}"; do
  src="$(mirror_ref "$img")"
  printf '==> %s\n' "$img"
  if docker pull "$src" >/dev/null 2>&1 && docker tag "$src" "$img"; then
    printf '    OK\n'; ok=$((ok + 1))
  else
    printf '    FAIL  ← 换源重试：bash %s docker.m.daocloud.io\n' "$0"; bad=$((bad + 1))
  fi
done

for img in "${DIRECT_IMAGES[@]}"; do
  printf '==> %s （直连）\n' "$img"
  if docker pull "$img" >/dev/null 2>&1; then
    printf '    OK\n'; ok=$((ok + 1))
  else
    printf '    FAIL\n'; bad=$((bad + 1))
  fi
done

echo
echo "成功 $ok 个，失败 $bad 个"
if [[ $bad -eq 0 ]]; then
  echo "可以启动了：docker-compose up -d   （或 docker compose up -d）"
else
  exit 1
fi
