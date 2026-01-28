from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from typing import List
from app.infrastructure.database import get_db, Project, User
from app.schemas.ontology import ProjectCreate, ProjectUpdate, ProjectResponse
from app.core.config import settings
import json

router = APIRouter(prefix="/api/ontology", tags=["ontology"])

@router.get("/{id}", response_model=ProjectResponse)
def get_ontology(id: int, db: Session = Depends(get_db)):
    project = db.query(Project).filter(Project.id == id).first()
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    return project

@router.put("/{id}", response_model=ProjectResponse)
def update_ontology_draft(id: int, project_update: ProjectUpdate, db: Session = Depends(get_db)):
    db_project = db.query(Project).filter(Project.id == id).first()
    if not db_project:
        raise HTTPException(status_code=404, detail="Project not found")
    
    if project_update.graph_data is not None:
        db_project.graph_data = project_update.graph_data
    
    db.commit()
    db.refresh(db_project)
    return db_project

@router.post("/{id}/publish")
def publish_ontology(id: int, db: Session = Depends(get_db)):
    db_project = db.query(Project).filter(Project.id == id).first()
    if not db_project:
        raise HTTPException(status_code=404, detail="Project not found")
    
    # 1. 将 JSON 图数据转换为 TTL 结构 (逻辑需调用现有的 rdflib 转换工具)
    # ttl_content = convert_json_to_ttl(db_project.graph_data)
    # db_project.ttl_content = ttl_content
    
    # 2. 同步到 Neo4j
    # sync_to_neo4j(ttl_content)
    
    db_project.is_published = True
    db.commit()
    
    return {"message": "Ontology published and synced to Neo4j successfully"}

@router.post("/extract")
async def extract_ontology(file_content: str):
    # 复用现有逻辑调用 LLM 提取
    # result = await extractor.extract_from_text(file_content)
    # return result
    return {"status": "success", "data": {}}
