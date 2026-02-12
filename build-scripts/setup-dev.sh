#!/bin/bash

# 开发环境构建脚本
# 功能：为开发人员提供快速启动开发环境

set -e

echo "👨‍💻 设置开发环境..."

# 检查依赖
echo "🔍 检查系统依赖..."
command -v docker >/dev/null 2>&1 || { echo "❌ 请先安装Docker"; exit 1; }
command -v docker-compose >/dev/null 2>&1 || { echo "❌ 请先安装Docker Compose"; exit 1; }

# 启动基础设施服务
echo "🏗️  启动基础设施服务..."
docker-compose up -d mysql neo4j standalone etcd minio attu

# 等待服务启动
echo "⏳ 等待基础设施服务启动..."
sleep 30

# 构建并启动后端开发服务
echo "🚀 启动后端开发服务..."
if [ ! -d "venv" ]; then
    echo "🐍 创建Python虚拟环境..."
    python3 -m venv venv
    source venv/bin/activate
    pip install -r requirements.txt
else
    source venv/bin/activate
fi

# 在后台运行后端服务
cd src/backend
nohup python main.py > ../../logs/backend-dev.log 2>&1 &
BACKEND_PID=$!
cd ../..

echo "🎨 安装前端依赖..."
cd src/frontend
if [ ! -d "node_modules" ]; then
    npm install
fi

# 启动前端开发服务
echo "🎨 启动前端开发服务..."
nohup npm run dev > ../../logs/frontend-dev.log 2>&1 &
FRONTEND_PID=$!
cd ../..

echo "✅ 开发环境启动完成！"
echo "==============================="
echo "📝 PID记录:"
echo "   后端PID: $BACKEND_PID"
echo "   前端PID: $FRONTEND_PID"
echo "==============================="
echo "🔗 开发环境地址:"
echo "   前端开发: http://localhost:5173"
echo "   后端API:  http://localhost:3001"
echo "   数据库:   mysql://root:password@localhost:3309/ontology_db"
echo "==============================="
echo "📋 管理命令:"
echo "   查看后端日志: tail -f logs/backend-dev.log"
echo "   查看前端日志: tail -f logs/frontend-dev.log"
echo "   停止服务: pkill -P $$"