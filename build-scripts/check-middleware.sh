#!/usr/bin/env bash
# ==============================================================================
# OntologySystem 中间件健康自检
# ==============================================================================
# 用法：bash build-scripts/check-middleware.sh
# 只读操作，不会改动任何数据。
# ==============================================================================
set -uo pipefail

pass=0; fail=0; warn=0
ok()  { printf '  \033[32mOK\033[0m   %s\n' "$1"; pass=$((pass+1)); }
ng()  { printf '  \033[31mFAIL\033[0m %s\n' "$1"; fail=$((fail+1)); }
wn()  { printf '  \033[33mWARN\033[0m %s\n' "$1"; warn=$((warn+1)); }
hdr() { printf '\n\033[1m%s\033[0m\n' "$1"; }

# 期望运行的容器（minio-init 是一次性任务，跑完即退出，属正常）
RUNNING=(milvus-etcd milvus-minio milvus-standalone attu-onto neo4j-onto mysql-onto redis-onto minio-onto)
ONESHOT=(minio-init)

hdr "1. 容器状态"
for c in "${RUNNING[@]}"; do
  st=$(docker inspect -f '{{.State.Status}}' "$c" 2>/dev/null || echo "missing")
  hl=$(docker inspect -f '{{if .State.Health}}{{.State.Health.Status}}{{else}}none{{end}}' "$c" 2>/dev/null || echo "-")
  case "$st/$hl" in
    running/healthy)   ok  "$(printf '%-20s %s (%s)' "$c" "$st" "$hl")" ;;
    running/none)      ok  "$(printf '%-20s %s (无健康检查)' "$c" "$st")" ;;
    running/starting)  wn  "$(printf '%-20s %s (%s) ← 还在启动窗口内，稍后再看' "$c" "$st" "$hl")" ;;
    running/unhealthy) ng  "$(printf '%-20s %s (%s) ← 需要看日志' "$c" "$st" "$hl")" ;;
    *)                 ng  "$(printf '%-20s %s' "$c" "$st")" ;;
  esac
done
for c in "${ONESHOT[@]}"; do
  code=$(docker inspect -f '{{.State.ExitCode}}' "$c" 2>/dev/null || echo "-")
  if [[ "$code" == "0" ]]; then ok "$(printf '%-20s 已退出(0)，一次性任务正常' "$c")"
  else ng "$(printf '%-20s 退出码=%s' "$c" "$code")"; fi
done

hdr "2. MySQL"
if docker exec mysql-onto mysql -uroot -ppassword -N -B -e "SELECT 1" >/dev/null 2>&1; then
  ok "可连接"
  cs=$(docker exec mysql-onto mysql -uroot -ppassword -N -B -e "SELECT CONCAT(@@character_set_server,' / ',@@collation_server)" 2>/dev/null)
  if [[ "$cs" == utf8mb4* ]]; then ok "字符集 = $cs"
  else ng "字符集 = $cs ← 中文可能乱码，应为 utf8mb4"; fi
  db=$(docker exec mysql-onto mysql -uroot -ppassword -N -B -e "SHOW DATABASES LIKE 'ontology_db'" 2>/dev/null)
  [[ -n "$db" ]] && ok "数据库 ontology_db 存在" || ng "数据库 ontology_db 不存在"
else
  ng "连不上（容器内 mysql -uroot -ppassword 失败）"
fi

hdr "3. Redis"
if docker exec redis-onto redis-cli ping 2>/dev/null | grep -q PONG; then
  ok "PING -> PONG"
  pol=$(docker exec redis-onto redis-cli config get maxmemory-policy 2>/dev/null | tail -1)
  [[ "$pol" == "noeviction" ]] && ok "maxmemory-policy = noeviction（队列安全）" \
                               || wn "maxmemory-policy = $pol ← 队列场景应为 noeviction"
else
  ng "PING 失败"
fi

hdr "4. Neo4j"
if curl -fs --max-time 10 http://localhost:7474 >/dev/null 2>&1; then ok "HTTP 7474 可访问"
else wn "HTTP 7474 还没通（Neo4j 首次启动较慢）"; fi
if docker exec neo4j-onto cypher-shell -u neo4j -p password "RETURN 1" >/dev/null 2>&1; then
  ok "cypher-shell 可连接"
  n10s=$(docker exec neo4j-onto cypher-shell -u neo4j -p password --format plain \
    "SHOW PROCEDURES YIELD name WHERE name STARTS WITH 'n10s.' RETURN count(name)" 2>/dev/null | tr -dc '0-9')
  apoc=$(docker exec neo4j-onto cypher-shell -u neo4j -p password --format plain \
    "SHOW PROCEDURES YIELD name WHERE name STARTS WITH 'apoc.' RETURN count(name)" 2>/dev/null | tr -dc '0-9')
  [[ -n "${apoc:-}" && "$apoc" -gt 0 ]] && ok "APOC 过程数 = $apoc" || ng "APOC 未加载（NEO4J_PLUGINS 下载失败？）"
  [[ -n "${n10s:-}" && "$n10s" -gt 0 ]] && ok "n10s 过程数 = $n10s" \
    || ng "n10s 未加载 ← 检查 docker logs neo4j-onto | grep -i n10s"
else
  wn "cypher-shell 还连不上（可能在下载插件，属首次启动正常现象）"
fi

hdr "5. Milvus"
curl -fs --max-time 10 http://localhost:9091/healthz >/dev/null 2>&1 && ok "healthz -> OK" || ng "healthz 无响应"
curl -fs --max-time 10 http://localhost:8001 >/dev/null 2>&1 && ok "attu (8001) 可访问" || wn "attu 还没就绪"

hdr "6. MinIO（业务）"
if curl -fs --max-time 10 http://localhost:9010/minio/health/live >/dev/null 2>&1; then ok "S3 API 9010 存活"
else ng "S3 API 9010 无响应"; fi
MC_IMG=$(docker inspect -f '{{.Config.Image}}' minio-init 2>/dev/null || echo "")
if [[ -n "$MC_IMG" ]]; then
  buckets=$(docker run --rm --network ontology-network --entrypoint sh "$MC_IMG" -c \
    "mc alias set l http://minio-business:9000 minioadmin minioadmin >/dev/null 2>&1 && mc ls l" 2>/dev/null)
  for b in ontology-uploads ontology-parsed ontology-exports; do
    echo "$buckets" | grep -q "$b" && ok "bucket $b 存在" || ng "bucket $b 缺失"
  done
else
  wn "取不到 mc 镜像名，跳过 bucket 检查"
fi
echo "  (Milvus 内部 MinIO 9002/9003 由 Milvus 自己管理，不用单独验)"

hdr "7. 资源占用"
docker stats --no-stream --format '  {{.Name}}  CPU={{.CPUPerc}}  MEM={{.MemUsage}}' 2>/dev/null | sort
echo
printf '\033[1m结果：OK=%s  WARN=%s  FAIL=%s\033[0m\n' "$pass" "$warn" "$fail"
[[ $fail -eq 0 ]] && echo "中间件层健康。" || echo "有失败项，见上面 FAIL 行。"
