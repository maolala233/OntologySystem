#!/bin/bash

# 清理脚本
# 功能：清理构建产物和运行中的容器

echo "🧹 开始清理..."

# 停止并移除容器
echo "🛑 停止运行中的容器..."
docker stop ontology-backend ontology-frontend 2>/dev/null || true
docker rm ontology-backend ontology-frontend 2>/dev/null || true

# 移除镜像
echo "🗑️  移除Docker镜像..."
docker rmi ontology-backend:latest ontology-frontend:latest 2>/dev/null || true

# 清理未使用的镜像
echo "🧼 清理未使用的Docker资源..."
docker system prune -f

# 清理构建产物
echo "🗂️  清理本地构建产物..."
rm -rf dist/
rm -rf build/
rm -rf TTL/*
rm -rf logs/*

echo "✅ 清理完成！"