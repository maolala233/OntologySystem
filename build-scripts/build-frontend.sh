#!/bin/bash

# Frontend构建脚本
# 功能：构建前端Docker镜像并运行容器

set -e  # 遇到错误立即退出

echo "🎨 开始构建前端服务..."

# 构建前端Docker镜像
echo "📦 构建前端Docker镜像..."
docker build -f dockerfile/frontend.Dockerfile -t ontology-frontend:latest .

echo "✅ 前端镜像构建完成！"
echo "🐳 镜像名称: ontology-frontend:latest"
echo "📋 后续部署命令:"
echo "   docker run -d --name ontology-frontend -p 80:80 ontology-frontend:latest"
echo "   或使用 docker-compose up frontend"