# 前后端整合指南

## 🎯 整合概述

本文档说明如何启动和测试前后端整合的本体建模平台。

## 📋 环境要求

### 后端
- Python 3.8+
- SQLite 或 MySQL
- Neo4j (可选，用于发布功能)

### 前端
- Node.js 16+
- npm 或 yarn

## 🚀 快速启动

### 1. 启动后端

```bash
cd src/backend

# 创建测试用户
python create_test_user.py

# 启动后端服务
python main.py
```

后端将运行在: **http://localhost:3001**

### 2. 启动前端

```bash
cd src/frontend

# 安装依赖（首次运行）
npm install

# 启动开发服务器
npm run dev
```

前端将运行在: **http://localhost:5174**

## 🔑 测试账号

创建测试用户后，可使用以下账号登录：

- **用户名**: `admin`
- **密码**: `123456`

或

- **用户名**: `testuser`
- **密码**: `123456`

## 📡 API 端点映射

### 前端 API 调用 → 后端路由

| 前端 API 方法 | 后端路由 | 说明 |
|-------------|---------|------|
| `authAPI.login()` | `POST /api/auth/login` | 用户登录 |
| `authAPI.register()` | `POST /api/auth/register` | 用户注册 |
| `authAPI.getCurrentUser()` | `GET /api/auth/me` | 获取当前用户 |
| `projectsAPI.getMyProjects()` | `GET /api/projects/my` | 获取我的项目 |
| `projectsAPI.getPublicProjects()` | `GET /api/projects/public` | 获取公共项目 |
| `projectsAPI.getProject(id)` | `GET /api/projects/{id}` | 获取项目详情 |
| `projectsAPI.createProject()` | `POST /api/projects` | 创建项目 |
| `projectsAPI.updateProject(id)` | `PUT /api/projects/{id}` | 更新项目 |
| `projectsAPI.deleteProject(id)` | `DELETE /api/projects/{id}` | 删除项目 |
| `projectsAPI.publishProject(id)` | `POST /api/projects/{id}/publish` | 发布项目 |
| `projectsAPI.uploadDocument(id, file)` | `POST /api/projects/{id}/upload` | 上传文档 |

## 🧪 测试流程

### 1. 测试用户注册和登录

1. 访问 http://localhost:5174
2. 点击"注册"标签
3. 填写用户名、邮箱、密码
4. 提交注册
5. 自动登录并跳转到首页

### 2. 测试项目创建

1. 点击左侧导航 "我的项目"
2. 点击 "新建项目" 按钮
3. 填写项目名称和描述
4. 提交创建
5. 查看项目卡片

### 3. 测试本体编辑

1. 在项目卡片上点击 "编辑"
2. 进入可视化编辑器
3. 点击 "新增实体" 添加节点
4. 拖拽节点连线建立关系
5. 点击节点编辑属性
6. 点击 "保存草稿"

### 4. 测试文档上传（模拟）

1. 在编辑器中点击 "上传文档"
2. 选择任意文档文件
3. 系统会返回模拟的本体数据
4. 查看自动生成的节点和关系

### 5. 测试发布功能

1. 在编辑器中点击 "发布到图数据库"
2. 确认发布
3. 项目状态变为 "已发布"
4. 在 "资产中心" 可以看到该项目

## 🔧 配置说明

### 前端配置 (src/frontend/.env)

```env
VITE_API_BASE_URL=http://localhost:3001
```

### 后端配置 (根目录 .env)

```env
# 数据库配置
MYSQL_HOST=localhost
MYSQL_PORT=3309
MYSQL_USER=root
MYSQL_PASSWORD=password
MYSQL_DATABASE=ontology_db

# 如果没有 MySQL，会自动使用 SQLite
SQLITE_PATH=./src/backend/ontology_system.db

# JWT 配置
JWT_SECRET_KEY=your_super_secret_jwt_key_here
ALGORITHM=HS256
ACCESS_TOKEN_EXPIRE_MINUTES=1440

# Neo4j 配置（发布功能需要）
NEO4J_URI=bolt://localhost:7687
NEO4J_USERNAME=neo4j
NEO4J_PASSWORD=password
```

## 🐛 常见问题

### 1. 前端无法连接后端

**问题**: 前端显示网络错误

**解决**:
- 确认后端已启动在 http://localhost:3001
- 检查 `src/frontend/.env` 中的 `VITE_API_BASE_URL` 配置
- 检查浏览器控制台的 CORS 错误

### 2. 登录失败

**问题**: 提示用户名或密码错误

**解决**:
- 运行 `python src/backend/create_test_user.py` 创建测试用户
- 使用正确的测试账号: admin / 123456

### 3. 数据库错误

**问题**: 后端启动时数据库连接失败

**解决**:
- 如果使用 MySQL，确保 MySQL 服务已启动
- 检查 `.env` 中的数据库配置
- 如果没有 MySQL，系统会自动使用 SQLite

### 4. 前端页面空白

**问题**: 访问前端显示空白页

**解决**:
- 检查浏览器控制台错误
- 确认前端开发服务器已启动
- 清除浏览器缓存并刷新

## 📊 数据流程图

```
用户操作 (前端)
    ↓
React 组件
    ↓
API Client (axios)
    ↓
HTTP 请求 → http://localhost:3001/api/*
    ↓
FastAPI 路由
    ↓
业务逻辑 (Services)
    ↓
数据库 (SQLite/MySQL)
    ↓
响应返回
    ↓
前端更新 UI
```

## 🔐 权限验证流程

```
1. 用户登录
   ↓
2. 后端生成 JWT Token
   ↓
3. 前端存储 Token 到 localStorage
   ↓
4. 后续请求自动添加 Authorization Header
   ↓
5. 后端验证 Token
   ↓
6. 返回用户数据或 401 错误
```

## 📝 开发建议

### 前端开发

1. **组件开发**: 在 `src/frontend/src/components` 添加新组件
2. **页面开发**: 在 `src/frontend/src/pages` 添加新页面
3. **API 调用**: 在 `src/frontend/src/api` 添加新的 API 方法
4. **路由配置**: 在 `src/frontend/src/App.tsx` 添加新路由

### 后端开发

1. **API 路由**: 在 `src/backend/app/api` 添加新路由
2. **数据模型**: 在 `src/backend/app/infrastructure/database.py` 添加新模型
3. **业务逻辑**: 在 `src/backend/app/services` 添加新服务
4. **Schema 定义**: 在 `src/backend/app/schemas` 添加新 Schema

## 🎨 UI 功能清单

- [x] 用户注册/登录
- [x] 深色侧边栏导航
- [x] 项目列表展示
- [x] 创建/编辑/删除项目
- [x] 可视化本体编辑器
- [x] 节点和关系操作
- [x] 属性编辑面板
- [x] 保存草稿功能
- [x] 发布到图数据库
- [x] 资产中心（公共本体浏览）
- [ ] 文档上传真实提取（需集成 LLM）
- [ ] Neo4j 同步（需配置 Neo4j）
- [ ] TTL 文件导出

## 🚧 待完成功能

### 后端

1. **密码加密**: 使用 `passlib` 加密用户密码
2. **LLM 集成**: 连接现有的本体提取服务
3. **Neo4j 同步**: 实现发布时同步到 Neo4j
4. **TTL 转换**: 实现 JSON 到 TTL 的转换

### 前端

1. **错误处理**: 完善错误提示和边界情况处理
2. **加载状态**: 优化加载动画和骨架屏
3. **响应式优化**: 改进移动端适配
4. **性能优化**: 大规模图谱渲染优化

## 📚 相关文档

- [前端 README](src/frontend/README.md)
- [后端 API 规范](BACKEND_API_SPEC.md)
- [数据库设计文档](docs/database-design.md)

## 🤝 贡献指南

1. Fork 项目
2. 创建功能分支
3. 提交代码
4. 发起 Pull Request

## 📄 许可证

MIT License
