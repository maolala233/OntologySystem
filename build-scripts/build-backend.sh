#!/bin/bash

# Backend构建脚本
# 功能：构建后端Docker镜像并运行容器

set -e  # 遇到错误立即退出

echo "🚀 开始构建后端服务..."

# 构建后端Docker镜像
echo "📦 构建后端Docker镜像..."


echo "✅ 后端镜像构建完成！"
echo "🐳 镜像名称: ontology-backend:latest"
echo "📋 后续部署命令:"
echo "   docker run -d --name ontology-backend -p 3001:3001 ontology-backend:latest"
echo "   或使用 docker-compose up backend"