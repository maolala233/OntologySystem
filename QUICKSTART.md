# 快速启动指南

## 🚀 一键启动完整系统

### 前提条件

- Docker 和 Docker Compose 已安装
- Python 3.8+ 已安装
- Node.js 16+ 已安装

### 启动步骤

#### 1. 启动数据库和基础设施

```bash
# 在项目根目录执行
docker-compose up -d mysql neo4j
```

等待 MySQL 启动完成（约 10-30 秒）。

#### 2. 启动后端服务

```bash
# 进入后端目录
cd src/backend

# 激活虚拟环境（如果有）
source ../../.venv/bin/activate

# 启动后端（会自动初始化数据库）
python main.py
```

**后端启动时会自动：**
- ✅ 连接到 MySQL
- ✅ 创建 `ontology_db` 数据库（如果不存在）
- ✅ 创建所有表（users, projects）
- ✅ 创建测试用户（admin / testuser）
- ✅ 创建示例项目

**后端运行在**: `http://localhost:3001`

#### 3. 启动前端服务

```bash
# 新开一个终端，进入前端目录
cd src/frontend

# 首次运行需要安装依赖
npm install

# 启动前端
npm run dev
```

**前端运行在**: `http://localhost:5174`

## 🔑 测试账号

后端启动后会自动创建以下测试账号：

| 用户名 | 密码 | 邮箱 | 说明 |
|--------|------|------|------|
| admin | 123456 | admin@example.com | 管理员账号，有示例项目 |
| testuser | 123456 | test@example.com | 普通用户账号 |

## 📊 系统架构

```
┌─────────────────────────────────────────────────────────┐
│                    浏览器访问                            │
│              http://localhost:5174                      │
└─────────────────────────────────────────────────────────┘
                          ↓
┌─────────────────────────────────────────────────────────┐
│                  前端 (React + Vite)                     │
│              - 用户界面                                  │
│              - 可视化编辑器                              │
│              - 路由管理                                  │
└─────────────────────────────────────────────────────────┘
                          ↓ HTTP API
┌─────────────────────────────────────────────────────────┐
│                后端 (FastAPI)                            │
│              http://localhost:3001                      │
│              - RESTful API                              │
│              - JWT 认证                                  │
│              - 业务逻辑                                  │
└─────────────────────────────────────────────────────────┘
                          ↓
        ┌─────────────────┴─────────────────┐
        ↓                                   ↓
┌──────────────────┐              ┌──────────────────┐
│  MySQL (3309)    │              │  Neo4j (7687)    │
│  - 用户数据      │              │  - 图谱数据      │
│  - 项目数据      │              │  - 已发布本体    │
│  - 草稿数据      │              │                  │
└──────────────────┘              └──────────────────┘
```

## 🧪 功能测试流程

### 1. 用户注册/登录

1. 访问 http://localhost:5174
2. 使用测试账号登录：
   - 用户名: `admin`
   - 密码: `123456`
3. 登录成功后跳转到首页

### 2. 查看我的项目

1. 点击左侧导航 "我的项目"
2. 可以看到 admin 用户的示例项目：
   - "工业本体示例"（草稿状态）
   - "已发布的公共本体"（已发布）

### 3. 编辑项目

1. 点击项目卡片上的 "编辑" 按钮
2. 进入可视化编辑器
3. 可以看到预设的节点和关系
4. 尝试以下操作：
   - 拖拽节点移动位置
   - 点击 "新增实体" 添加新节点
   - 拖拽节点连线建立关系
   - 点击节点/关系编辑属性
   - 点击 "保存草稿"

### 4. 创建新项目

1. 在 "我的项目" 页面点击 "新建项目"
2. 填写项目名称和描述
3. 提交创建
4. 点击 "编辑" 进入编辑器
5. 添加节点和关系
6. 保存草稿

### 5. 发布项目

1. 在编辑器中点击 "发布到图数据库"
2. 确认发布
3. 项目状态变为 "已发布"
4. 前往 "资产中心" 可以看到该项目

### 6. 浏览资产中心

1. 点击左侧导航 "资产中心"
2. 可以看到所有已发布的公共本体
3. 点击项目卡片查看详情
4. 以只读模式浏览本体图谱

## 🔧 配置说明

### 环境变量配置

**根目录 `.env`** (后端配置):
```env
# MySQL 配置
MYSQL_HOST=localhost
MYSQL_PORT=3309
MYSQL_USER=root
MYSQL_PASSWORD=password
MYSQL_DATABASE=ontology_db

# JWT 配置
JWT_SECRET_KEY=your_super_secret_jwt_key_here
ALGORITHM=HS256
ACCESS_TOKEN_EXPIRE_MINUTES=1440

# Neo4j 配置
NEO4J_URI=bolt://localhost:7687
NEO4J_USERNAME=neo4j
NEO4J_PASSWORD=password
```

**前端 `.env`** (`src/frontend/.env`):
```env
VITE_API_BASE_URL=http://localhost:3001
```

## 🐛 常见问题

### 1. MySQL 连接失败

**问题**: 后端启动时提示无法连接 MySQL

**解决方案**:
```bash
# 检查 MySQL 容器是否运行
docker ps | grep mysql-onto

# 如果没有运行，启动它
docker-compose up -d mysql

# 查看 MySQL 日志
docker logs mysql-onto

# 等待 MySQL 完全启动（约 30 秒）
```

### 2. 前端无法连接后端

**问题**: 前端显示网络错误

**解决方案**:
- 确认后端已启动在 http://localhost:3001
- 检查 `src/frontend/.env` 中的 `VITE_API_BASE_URL`
- 重启前端开发服务器

### 3. 数据库已存在但表结构不对

**问题**: 后端启动报错，提示字段不存在

**解决方案**:
```bash
# 方法1: 删除旧数据库（会丢失数据）
docker exec -it mysql-onto mysql -uroot -ppassword -e "DROP DATABASE ontology_db;"

# 方法2: 使用 SQLite（不需要 MySQL）
# 修改 .env 中的配置，注释掉 MYSQL_URL
```

### 4. 端口被占用

**问题**: 启动时提示端口已被占用

**解决方案**:
```bash
# 查看端口占用
lsof -i :3001  # 后端端口
lsof -i :5174  # 前端端口
lsof -i :3309  # MySQL 端口

# 修改端口（在相应的配置文件中）
```

## 📝 开发提示

### 后端热重载

使用 `uvicorn` 的 `--reload` 参数：
```bash
uvicorn main:app --host 0.0.0.0 --port 3001 --reload
```

### 前端热重载

Vite 默认支持热重载，修改代码后自动刷新。

### 数据库管理

**使用 MySQL 客户端**:
```bash
docker exec -it mysql-onto mysql -uroot -ppassword ontology_db
```

**常用 SQL**:
```sql
-- 查看所有用户
SELECT * FROM users;

-- 查看所有项目
SELECT id, name, owner_id, is_published FROM projects;

-- 清空测试数据
DELETE FROM projects;
DELETE FROM users;
```

## 🎯 下一步

1. **集成 LLM 本体提取**: 连接现有的 LLM 服务实现真实的文档提取
2. **Neo4j 同步**: 实现发布时同步到 Neo4j 图数据库
3. **密码加密**: 使用 `passlib` 加密用户密码
4. **权限细化**: 实现更细粒度的权限控制
5. **性能优化**: 优化大规模图谱渲染性能

## 📚 相关文档

- [前后端整合指南](INTEGRATION_GUIDE.md)
- [前端 README](src/frontend/README.md)
- [后端 API 规范](BACKEND_API_SPEC.md)
