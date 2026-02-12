#!/bin/bash

# 完整项目构建脚本
# 功能：一键构建和部署整个本体系统

set -e  # 遇到错误立即退出

echo "🏗️  开始完整项目构建..."

# 创建必要的目录
echo "📂 创建必要目录..."
mkdir -p TTL logs volumes/{mysql,neo4j,milvus}

# 构建后端
echo "🚀 构建后端服务..."
./build-scripts/build-backend.sh

# 等待后端启动
echo "⏳ 等待后端服务启动..."
sleep 10

# 构建前端
echo "🎨 构建前端服务..."
./build-scripts/build-frontend.sh

# 显示服务状态
echo "📋 服务状态检查..."
docker ps -f name=ontology-

echo "🎉 项目构建完成！"
echo "==============================="
echo "🔗 访问地址:"
echo "   前端界面: http://localhost"
echo "   后端API:  http://localhost:3001"
echo "   Neo4j:    http://localhost:7474"
echo "   Milvus:   http://localhost:19530"
echo "   Attu:     http://localhost:8000"
echo "==============================="
echo "🔧 管理命令:"
echo "   查看日志: docker logs -f ontology-backend"
echo "   停止服务: docker-compose down"
echo "   重启服务: docker-compose restart"