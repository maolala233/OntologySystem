# 后端 API 接口规范

## 基础配置

**Base URL**: `http://localhost:8000/api`

**认证方式**: JWT Bearer Token

**请求头**:
```
Authorization: Bearer {access_token}
Content-Type: application/json
```

---

## 1. 认证接口 (Auth)

### 1.1 用户登录
```http
POST /api/auth/login
Content-Type: multipart/form-data

username=admin&password=123456
```

**响应**:
```json
{
  "access_token": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9...",
  "token_type": "bearer",
  "user": {
    "id": 1,
    "username": "admin",
    "email": "admin@example.com"
  }
}
```

### 1.2 用户注册
```http
POST /api/auth/register
Content-Type: application/json

{
  "username": "newuser",
  "email": "user@example.com",
  "password": "password123"
}
```

**响应**: 同登录接口

### 1.3 获取当前用户信息
```http
GET /api/auth/me
Authorization: Bearer {token}
```

**响应**:
```json
{
  "id": 1,
  "username": "admin",
  "email": "admin@example.com"
}
```

---

## 2. 项目管理接口 (Projects)

### 2.1 获取我的项目列表
```http
GET /api/projects/my
Authorization: Bearer {token}
```

**响应**:
```json
[
  {
    "id": 1,
    "name": "工业本体",
    "description": "工业领域的本体模型",
    "graph_data": {
      "nodes": [...],
      "edges": [...]
    },
    "is_published": false,
    "owner_id": 1,
    "owner": {
      "id": 1,
      "username": "admin",
      "email": "admin@example.com"
    },
    "created_at": "2026-01-28T10:00:00",
    "updated_at": "2026-01-28T12:00:00"
  }
]
```

### 2.2 获取公共已发布项目
```http
GET /api/projects/public
```

**响应**: 同上，但只返回 `is_published=true` 的项目

### 2.3 获取项目详情
```http
GET /api/projects/{project_id}
Authorization: Bearer {token}
```

**响应**: 单个项目对象（同上）

### 2.4 创建项目
```http
POST /api/projects
Authorization: Bearer {token}
Content-Type: application/json

{
  "name": "新项目",
  "description": "项目描述（可选）"
}
```

**响应**:
```json
{
  "id": 2,
  "name": "新项目",
  "description": "项目描述",
  "graph_data": {
    "nodes": [],
    "edges": []
  },
  "is_published": false,
  "owner_id": 1,
  "created_at": "2026-01-28T14:00:00",
  "updated_at": "2026-01-28T14:00:00"
}
```

### 2.5 更新项目（保存草稿）
```http
PUT /api/projects/{project_id}
Authorization: Bearer {token}
Content-Type: application/json

{
  "name": "更新后的名称（可选）",
  "description": "更新后的描述（可选）",
  "graph_data": {
    "nodes": [
      {
        "id": "node_1",
        "type": "default",
        "position": { "x": 100, "y": 100 },
        "data": {
          "label": "实体1",
          "type": "Entity",
          "properties": {}
        }
      }
    ],
    "edges": [
      {
        "id": "edge_1",
        "source": "node_1",
        "target": "node_2",
        "type": "smoothstep",
        "data": {
          "label": "关联",
          "relation": "related_to"
        }
      }
    ]
  }
}
```

**响应**: 更新后的项目对象

### 2.6 发布项目
```http
POST /api/projects/{project_id}/publish
Authorization: Bearer {token}
```

**功能**:
1. 将 `is_published` 设置为 `true`
2. 更新 TTL 文件
3. 同步数据到 Neo4j 图数据库

**响应**: 更新后的项目对象

### 2.7 删除项目
```http
DELETE /api/projects/{project_id}
Authorization: Bearer {token}
```

**响应**:
```json
{
  "message": "项目已删除"
}
```

### 2.8 上传文档并提取本体
```http
POST /api/projects/{project_id}/upload
Authorization: Bearer {token}
Content-Type: multipart/form-data

file=@document.pdf
```

**功能**:
1. 接收文档文件（.txt, .pdf, .doc, .docx）
2. 使用 LLM 提取本体结构
3. 返回提取的节点和关系数据

**响应**:
```json
{
  "nodes": [
    {
      "id": "node_1",
      "type": "default",
      "position": { "x": 100, "y": 100 },
      "data": {
        "label": "产品",
        "type": "Class",
        "properties": {}
      }
    }
  ],
  "edges": [
    {
      "id": "edge_1",
      "source": "node_1",
      "target": "node_2",
      "type": "smoothstep",
      "data": {
        "label": "包含",
        "relation": "contains"
      }
    }
  ]
}
```

---

## 3. 数据库设计

### 3.1 用户表 (users)
```sql
CREATE TABLE users (
    id INT PRIMARY KEY AUTO_INCREMENT,
    username VARCHAR(50) UNIQUE NOT NULL,
    email VARCHAR(100) UNIQUE NOT NULL,
    hashed_password VARCHAR(255) NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
);
```

### 3.2 项目表 (projects)
```sql
CREATE TABLE projects (
    id INT PRIMARY KEY AUTO_INCREMENT,
    name VARCHAR(200) NOT NULL,
    description TEXT,
    graph_data JSON,  -- 存储节点和边的 JSON 数据
    is_published BOOLEAN DEFAULT FALSE,
    owner_id INT NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    FOREIGN KEY (owner_id) REFERENCES users(id) ON DELETE CASCADE,
    INDEX idx_owner_id (owner_id),
    INDEX idx_is_published (is_published)
);
```

---

## 4. 图谱同步机制

### 编辑阶段（草稿态）
- 用户在前端画布上的所有修改（增删节点、改属性）
- **仅保存为 JSON** 存储在 `projects.graph_data` 字段
- **不写入 Neo4j**，避免频繁操作和脏数据

### 发布阶段
当用户点击"发布"按钮时：

1. **更新 TTL 文件**
   ```python
   # 将 graph_data JSON 转换为 TTL 格式
   ttl_content = convert_json_to_ttl(project.graph_data)
   save_ttl_file(f"ontologies/{project.id}.ttl", ttl_content)
   ```

2. **同步到 Neo4j**
   ```python
   from neo4j import GraphDatabase
   
   # 清空该项目的旧数据
   session.run("""
       MATCH (n {project_id: $project_id})
       DETACH DELETE n
   """, project_id=project.id)
   
   # 写入新节点
   for node in project.graph_data['nodes']:
       session.run("""
           CREATE (n:Entity {
               id: $id,
               label: $label,
               type: $type,
               project_id: $project_id
           })
       """, **node.data, project_id=project.id)
   
   # 写入新关系
   for edge in project.graph_data['edges']:
       session.run("""
           MATCH (a {id: $source}), (b {id: $target})
           CREATE (a)-[r:RELATION {
               type: $relation,
               label: $label
           }]->(b)
       """, **edge.data, source=edge.source, target=edge.target)
   ```

3. **更新发布状态**
   ```python
   project.is_published = True
   db.commit()
   ```

---

## 5. 权限控制

### 5.1 项目访问权限
```python
def check_project_permission(project_id: int, user_id: int, action: str):
    project = db.query(Project).filter(Project.id == project_id).first()
    
    if action == "read":
        # 公开项目或自己的项目可读
        return project.is_published or project.owner_id == user_id
    
    elif action in ["update", "delete", "publish"]:
        # 只有创建者可以修改/删除/发布
        return project.owner_id == user_id
    
    return False
```

### 5.2 FastAPI 依赖注入示例
```python
from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/auth/login")

async def get_current_user(token: str = Depends(oauth2_scheme)):
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        user_id = payload.get("sub")
        user = db.query(User).filter(User.id == user_id).first()
        if not user:
            raise HTTPException(status_code=401, detail="用户不存在")
        return user
    except JWTError:
        raise HTTPException(status_code=401, detail="Token 无效")

# 使用示例
@app.get("/api/projects/my")
async def get_my_projects(current_user: User = Depends(get_current_user)):
    projects = db.query(Project).filter(Project.owner_id == current_user.id).all()
    return projects
```

---

## 6. 错误响应格式

所有错误响应统一格式：
```json
{
  "detail": "错误描述信息"
}
```

常见状态码：
- `200` - 成功
- `201` - 创建成功
- `400` - 请求参数错误
- `401` - 未授权（Token 无效或过期）
- `403` - 禁止访问（无权限）
- `404` - 资源不存在
- `500` - 服务器内部错误

---

## 7. 环境变量配置

后端需要从 `.env` 文件读取以下配置：

```env
# JWT 配置
JWT_SECRET_KEY=your_super_secret_jwt_key_here
ALGORITHM=HS256
ACCESS_TOKEN_EXPIRE_MINUTES=1440

# 数据库配置
MYSQL_HOST=localhost
MYSQL_PORT=3309
MYSQL_USER=root
MYSQL_PASSWORD=password
MYSQL_DATABASE=ontology_db

# Neo4j 配置
NEO4J_URI=bolt://localhost:7687
NEO4J_USERNAME=neo4j
NEO4J_PASSWORD=password

# LLM 配置（用于本体提取）
LLM_API_KEY=EMPTY
LLM_MODEL_NAME=qwen2.5-7B
LLM_BASE_URL=http://localhost:9080/v1
```

---

## 8. 测试建议

### 使用 cURL 测试

**登录**:
```bash
curl -X POST http://localhost:8000/api/auth/login \
  -F "username=admin" \
  -F "password=123456"
```

**创建项目**:
```bash
curl -X POST http://localhost:8000/api/projects \
  -H "Authorization: Bearer YOUR_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"name": "测试项目", "description": "这是一个测试"}'
```

### 使用 Postman
1. 导入 API 集合
2. 设置环境变量 `base_url` 和 `token`
3. 测试各个接口

---

## 9. 前后端联调检查清单

- [ ] CORS 配置正确（允许前端域名）
- [ ] JWT Token 正确生成和验证
- [ ] 数据库表结构已创建
- [ ] Neo4j 连接正常
- [ ] 文件上传功能正常
- [ ] LLM 本体提取功能正常
- [ ] 所有接口返回格式符合前端预期
