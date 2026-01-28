# Knora 本体建模平台 - 前端文档

## 📋 项目概述

基于 React + TypeScript + Ant Design + React Flow 构建的现代化本体建模平台前端。

## 🎨 设计风格

参考 Knora 平台设计，采用：
- **深色侧边栏导航**：提供清晰的功能模块划分
- **现代化 UI**：渐变色、卡片式布局、微动画
- **响应式设计**：适配不同屏幕尺寸

## 🏗️ 项目结构

```
src/
├── api/                    # API 接口层
│   ├── client.ts          # Axios 客户端配置
│   ├── auth.ts            # 认证相关 API
│   └── projects.ts        # 项目管理 API
├── components/            # 组件
│   ├── Layout/           # 布局组件
│   │   ├── AppLayout.tsx # 主布局（侧边栏+内容区）
│   │   └── Navbar.tsx    # 顶部导航栏
│   ├── OntologyCanvas.tsx # 旧版画布组件（已弃用）
│   └── ProtectedRoute.tsx # 路由保护组件
├── pages/                 # 页面组件
│   ├── LoginPage.tsx     # 登录/注册页面
│   ├── HomePage.tsx      # 首页
│   ├── MyProjectsPage.tsx # 我的项目
│   ├── OntologyBuilderPage.tsx # 本体编辑器
│   ├── AssetCenterPage.tsx # 资产中心（列表）
│   └── AssetDetailPage.tsx # 资产详情（只读）
├── types/                 # TypeScript 类型定义
│   └── ontology.ts       # 本体相关类型
├── App.tsx               # 应用根组件（路由配置）
├── main.tsx              # 应用入口
└── index.css             # 全局样式

```

## 🚀 核心功能

### 1. 用户认证系统
- **登录/注册**：JWT Token 认证
- **路由保护**：未登录用户自动重定向到登录页
- **Token 管理**：自动在请求头添加 Token，401 自动登出

### 2. 项目管理
- **创建项目**：支持添加名称和描述
- **项目列表**：卡片式展示，显示节点/关系统计
- **删除项目**：二次确认防误删

### 3. 本体可视化编辑器 (核心)
基于 React Flow 实现：

#### 功能特性
- ✅ **文档上传**：上传文档，AI 自动提取本体
- ✅ **节点操作**：
  - 新增节点（实体/类/属性/概念）
  - 拖拽移动节点
  - 点击编辑节点属性
  - 删除节点
- ✅ **关系操作**：
  - 拖拽连线建立关系
  - 编辑关系类型和标签
  - 删除关系
- ✅ **属性面板**：右侧抽屉，支持编辑名称、类型、自定义属性
- ✅ **保存草稿**：实时保存到关系型数据库（JSON 格式）
- ✅ **发布到图数据库**：同步到 Neo4j

#### 节点类型
- `Entity` - 实体（蓝色）
- `Class` - 类（蓝色）
- `Property` - 属性（绿色）
- `Concept` - 概念（橙色）

#### 关系类型
- `related_to` - 关联
- `subclass_of` - 子类
- `instance_of` - 属于
- `contains` - 包含
- `depends_on` - 依赖

### 4. 资产中心（公共本体浏览）
- **已发布本体列表**：所有用户可见
- **搜索和排序**：按名称搜索，按时间/节点数排序
- **只读查看**：点击查看本体详情（不可编辑）

## 🔄 完整使用流程

```
1. 用户注册/登录
   ↓
2. 创建新项目
   ↓
3. 上传文档 → AI 自动提取本体
   ↓
4. 可视化调整修改
   - 拖拽节点
   - 添加/删除节点和关系
   - 编辑属性
   ↓
5. 保存草稿（多次保存）
   ↓
6. 发布到图数据库
   - 数据同步到 Neo4j
   - 在资产中心公开展示
   ↓
7. 其他用户可在资产中心查看
```

## 🔌 API 接口设计

### 认证接口
```typescript
POST /api/auth/login       // 登录
POST /api/auth/register    // 注册
GET  /api/auth/me          // 获取当前用户信息
```

### 项目接口
```typescript
GET    /api/projects/my              // 获取我的项目列表
GET    /api/projects/public          // 获取公共已发布项目
GET    /api/projects/:id             // 获取项目详情
POST   /api/projects                 // 创建项目
PUT    /api/projects/:id             // 更新项目（保存草稿）
DELETE /api/projects/:id             // 删除项目
POST   /api/projects/:id/publish     // 发布项目
POST   /api/projects/:id/upload      // 上传文档提取本体
```

## 📦 依赖包

### 核心依赖
- `react` & `react-dom` - React 框架
- `react-router-dom` - 路由管理
- `antd` - UI 组件库
- `reactflow` - 可视化图谱编辑
- `axios` - HTTP 客户端
- `lucide-react` - 图标库

### 开发依赖
- `vite` - 构建工具
- `typescript` - 类型检查
- `tailwindcss` - CSS 框架

## 🎨 样式系统

### 配色方案
- **主色调**：蓝色 (#3b82f6) → 紫色 (#8b5cf6) 渐变
- **成功色**：绿色 (#10b981)
- **警告色**：橙色 (#f59e0b)
- **危险色**：红色 (#ef4444)

### 动画效果
- `animate-blob` - 背景装饰动画
- 卡片悬停效果
- 平滑过渡动画

## 🔐 权限设计

### 项目权限
- **Private（草稿态）**：
  - 仅创建者可见
  - 仅创建者可编辑
  - 不在资产中心展示

- **Published（发布态）**：
  - 全平台用户可见（资产中心）
  - 仅创建者可编辑
  - 已同步到 Neo4j

## 🚀 启动项目

### 开发环境
```bash
cd src/frontend
npm install
npm run dev
```

访问：http://localhost:5174

### 生产构建
```bash
npm run build
npm run preview
```

## 🔧 环境变量

创建 `.env` 文件：
```env
VITE_API_BASE_URL=http://localhost:8000/api
```

## 📝 待办事项

- [ ] 添加本体导出功能（TTL/JSON）
- [ ] 支持批量操作节点
- [ ] 添加撤销/重做功能
- [ ] 实现协作编辑（WebSocket）
- [ ] 添加本体版本管理
- [ ] 优化大规模图谱性能

## 🐛 已知问题

1. CSS Lint 警告：`@tailwind` 指令警告（可忽略，这是 Tailwind CSS 的正常语法）
2. 需要后端 API 支持才能完整运行

## 📚 参考资源

- [React Flow 文档](https://reactflow.dev/)
- [Ant Design 文档](https://ant.design/)
- [Vite 文档](https://vitejs.dev/)
