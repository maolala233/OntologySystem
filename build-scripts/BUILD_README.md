# 本体系统构建指南

## 📋 目录结构

```
build-scripts/
├── build-backend.sh      # Linux/macOS后端构建脚本
├── build-frontend.sh     # Linux/macOS前端构建脚本
├── build-all.sh         # Linux/macOS完整构建脚本
├── setup-dev.sh         # Linux/macOS开发环境设置
├── cleanup.sh           # Linux/macOS清理脚本
├── build-backend.bat    # Windows后端构建脚本
├── build-frontend.bat   # Windows前端构建脚本
└── BUILD_README.md      # 本说明文件
```

## 🚀 快速开始

### Linux/macOS 系统

#### 一键完整构建
```bash
chmod +x build-scripts/*.sh
./build-scripts/build-all.sh
```

#### 分步构建
```bash
# 构建后端
./build-scripts/build-backend.sh

# 构建前端  
./build-scripts/build-frontend.sh
```

#### 开发环境
```bash
./build-scripts/setup-dev.sh
```

#### 清理环境
```bash
./build-scripts/cleanup.sh
```

### Windows 系统

#### 分步构建
```cmd
# 构建后端
build-scripts\build-backend.bat

# 构建前端
build-scripts\build-frontend.bat
```

## 📦 镜像构建命令

### 后端镜像构建

**Docker方式：**
```bash
# 构建镜像
docker build -f dockerfile/backend.Dockerfile -t ontology-backend:latest .
```

**后续部署命令：**
```bash
# 使用docker run部署
docker run -d \
    --name ontology-backend \
    --network milvus \
    -p 3001:3001 \
    -v "$(pwd)/TTL:/app/TTL" \
    -v "$(pwd)/logs:/app/logs" \
    -e NEO4J_URI=bolt://neo4j:7687 \
    -e MYSQL_HOST=mysql \
    -e MYSQL_PORT=3306 \
    ontology-backend:latest

# 或使用docker-compose（推荐）
docker-compose up -d backend
```

**开发模式运行：**
```bash
cd src/backend
python main.py
```

### 前端镜像构建

**Docker方式：**
```bash
# 构建镜像
docker build -f dockerfile/frontend.Dockerfile -t ontology-frontend:latest .
```

**后续部署命令：**
```bash
# 使用docker run部署
docker run -d \
    --name ontology-frontend \
    --network milvus \
    -p 80:80 \
    --link ontology-backend:backend \
    ontology-frontend:latest

# 或使用docker-compose（推荐）
docker-compose up -d frontend
```

**开发模式运行：**
```bash
cd src/frontend
npm install
npm run dev
```

## 🔧 环境变量配置

### 后端环境变量
```bash
NEO4J_URI=bolt://localhost:7687
NEO4J_USERNAME=neo4j
NEO4J_PASSWORD=password
MYSQL_HOST=localhost
MYSQL_PORT=3309
MYSQL_USER=root
MYSQL_PASSWORD=password
MYSQL_DATABASE=ontology_db
```

### 前端环境变量
```bash
VITE_API_BASE_URL=http://localhost:3001
```

## 📊 服务地址

| 服务 | 地址 | 说明 |
|------|------|------|
| 前端界面 | http://localhost | 用户界面 |
| 后端API | http://localhost:3001 | API服务 |
| 健康检查 | http://localhost:3001/health | 服务状态 |
| Neo4j | http://localhost:7474 | 图数据库 |
| Milvus | http://localhost:19530 | 向量数据库 |
| Attu | http://localhost:8000 | Milvus管理界面 |

## 🛠️ 常用管理命令

```bash
# 查看运行中的容器
docker ps

# 查看容器日志
docker logs -f ontology-backend
docker logs -f ontology-frontend

# 进入容器
docker exec -it ontology-backend sh
docker exec -it ontology-frontend sh

# 重启服务
docker restart ontology-backend
docker restart ontology-frontend

# 停止所有服务
docker-compose down
```

## ⚠️ 注意事项

1. **首次运行**：确保已安装Docker和Docker Compose
2. **端口占用**：确认3001、80等端口未被占用
3. **权限问题**：Linux系统可能需要sudo权限
4. **网络配置**：确保Docker网络配置正确
5. **依赖安装**：Windows用户需确保Docker Desktop正常运行

## 🐛 故障排除

### 常见问题

1. **端口被占用**
   ```bash
   # 查找占用端口的进程
   lsof -i :3001
   # 杀死进程
   kill -9 <PID>
   ```

2. **Docker权限问题**
   ```bash
   sudo usermod -aG docker $USER
   # 重新登录生效
   ```

3. **构建失败**
   ```bash
   # 清理Docker缓存
   docker system prune -a
   # 重新构建
   docker build --no-cache -f dockerfile/backend.Dockerfile .
   ```

4. **网络连接问题**
   ```bash
   # 检查Docker网络
   docker network ls
   docker network inspect milvus
   ```