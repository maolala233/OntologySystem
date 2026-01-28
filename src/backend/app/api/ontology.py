from fastapi import APIRouter, Depends, HTTPException, status, UploadFile, File
from sqlalchemy.orm import Session
from typing import List
from app.infrastructure.database import get_db, Project, User
from app.schemas.ontology import ProjectCreate, ProjectUpdate, ProjectResponse
from app.api.auth import get_current_user
from app.core.config import settings
import json
import os
import tempfile

router = APIRouter(prefix="/api/projects", tags=["projects"])

# 获取我的项目列表
@router.get("/my", response_model=List[ProjectResponse])
def get_my_projects(current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    projects = db.query(Project).filter(Project.owner_id == current_user.id).all()
    return projects

# 获取公共已发布项目
@router.get("/public", response_model=List[ProjectResponse])
def get_public_projects(db: Session = Depends(get_db)):
    projects = db.query(Project).filter(Project.is_published == True).all()
    return projects

# 获取单个项目详情
@router.get("/{project_id}", response_model=ProjectResponse)
def get_project(project_id: int, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    project = db.query(Project).filter(Project.id == project_id).first()
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    
    # 权限检查：公开项目或自己的项目可查看
    if not project.is_published and project.owner_id != current_user.id:
        raise HTTPException(status_code=403, detail="No permission to access this project")
    
    return project

# 创建新项目
@router.post("", response_model=ProjectResponse)
def create_project(
    project_data: ProjectCreate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    new_project = Project(
        name=project_data.name,
        description=project_data.description,
        owner_id=current_user.id,
        graph_data={"nodes": [], "edges": []},
        is_published=False
    )
    db.add(new_project)
    db.commit()
    db.refresh(new_project)
    return new_project

# 更新项目（保存草稿）
@router.put("/{project_id}", response_model=ProjectResponse)
def update_project(
    project_id: int,
    project_update: ProjectUpdate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    db_project = db.query(Project).filter(Project.id == project_id).first()
    if not db_project:
        raise HTTPException(status_code=404, detail="Project not found")
    
    # 权限检查：只有创建者可以修改
    if db_project.owner_id != current_user.id:
        raise HTTPException(status_code=403, detail="No permission to modify this project")
    
    # 更新字段
    if project_update.name is not None:
        db_project.name = project_update.name
    if project_update.description is not None:
        db_project.description = project_update.description
    if project_update.graph_data is not None:
        db_project.graph_data = project_update.graph_data
    
    db.commit()
    db.refresh(db_project)
    return db_project

# 删除项目
@router.delete("/{project_id}")
def delete_project(
    project_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    db_project = db.query(Project).filter(Project.id == project_id).first()
    if not db_project:
        raise HTTPException(status_code=404, detail="Project not found")
    
    # 权限检查：只有创建者可以删除
    if db_project.owner_id != current_user.id:
        raise HTTPException(status_code=403, detail="No permission to delete this project")
    
    db.delete(db_project)
    db.commit()
    return {"message": "Project deleted successfully"}

# 发布项目
@router.post("/{project_id}/publish", response_model=ProjectResponse)
def publish_project(
    project_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    db_project = db.query(Project).filter(Project.id == project_id).first()
    if not db_project:
        raise HTTPException(status_code=404, detail="Project not found")
    
    # 权限检查：只有创建者可以发布
    if db_project.owner_id != current_user.id:
        raise HTTPException(status_code=403, detail="No permission to publish this project")
    
    # TODO: 实现以下功能
    # 1. 将 JSON 图数据转换为 TTL 结构
    # ttl_content = convert_json_to_ttl(db_project.graph_data)
    # db_project.ttl_content = ttl_content
    
    # 2. 同步到 Neo4j
    # sync_to_neo4j(db_project.id, db_project.graph_data)
    
    db_project.is_published = True
    db.commit()
    db.refresh(db_project)
    
    return db_project

# 上传文档并提取本体
@router.post("/{project_id}/upload")
async def upload_document(
    project_id: int,
    file: UploadFile = File(...),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    db_project = db.query(Project).filter(Project.id == project_id).first()
    if not db_project:
        raise HTTPException(status_code=404, detail="Project not found")
    
    # 权限检查：只有创建者可以上传
    if db_project.owner_id != current_user.id:
        raise HTTPException(status_code=403, detail="No permission to upload to this project")
    
    # 保存临时文件
    temp_dir = "temp_uploads"
    os.makedirs(temp_dir, exist_ok=True)
    temp_path = os.path.join(temp_dir, file.filename)
    
    with open(temp_path, "wb") as buffer:
        content = await file.read()
        buffer.write(content)
    
    try:
        # TODO: 调用现有的 LLM 提取逻辑
        # from app.services.extractor import extract_ontology_from_file
        # result = extract_ontology_from_file(temp_path)
        
        # 模拟返回数据
        mock_nodes = [
            {
                "id": f"node_{i}",
                "type": "default",
                "position": {"x": 100 + i * 150, "y": 100 + i * 50},
                "data": {
                    "label": f"实体{i}",
                    "type": "Entity",
                    "properties": {}
                },
                "style": {
                    "background": "#fff",
                    "border": "2px solid #3b82f6",
                    "borderRadius": "8px",
                    "padding": "10px"
                }
            }
            for i in range(1, 4)
        ]
        
        mock_edges = [
            {
                "id": "edge_1",
                "source": "node_1",
                "target": "node_2",
                "type": "smoothstep",
                "animated": True,
                "data": {
                    "label": "关联",
                    "relation": "related_to"
                }
            }
        ]
        
        return {
            "nodes": mock_nodes,
            "edges": mock_edges,
            "message": "Document uploaded and ontology extracted successfully"
        }
    
    finally:
        # 清理临时文件
        if os.path.exists(temp_path):
            os.remove(temp_path)

