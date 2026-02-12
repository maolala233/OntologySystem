@echo off
REM Windows后端构建脚本

echo 🚀 开始构建后端服务...

REM 构建后端Docker镜像
echo 📦 构建后端Docker镜像...
docker build -f dockerfile\backend.Dockerfile -t ontology-backend:latest .

echo ✅ 后端镜像构建完成！
echo 🐳 镜像名称: ontology-backend:latest
echo 📋 后续部署命令:
echo    docker run -d --name ontology-backend -p 3001:3001 ontology-backend:latest
echo    或使用 docker-compose up backend

pause