"""
知识域管理 API
提供知识域的增删改查功能
"""
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from typing import List
from app.infrastructure.database import get_db, KnowledgeDomain
from app.schemas.domain import (
    KnowledgeDomainCreate,
    KnowledgeDomainUpdate,
    KnowledgeDomainResponse,
)
from app.api.auth import get_current_user
from app.infrastructure.database import User

router = APIRouter(prefix="/domains", tags=["domains"])


@router.get("", response_model=List[KnowledgeDomainResponse])
def get_all_domains(db: Session = Depends(get_db)):
    """
    获取所有知识域列表
    用于前端下拉选择
    """
    domains = db.query(KnowledgeDomain).order_by(KnowledgeDomain.name).all()
    return domains


@router.post("", response_model=KnowledgeDomainResponse, status_code=status.HTTP_201_CREATED)
def create_domain(
    domain_data: KnowledgeDomainCreate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """
    创建新的知识域
    如果名称已存在，则返回已存在的知识域
    """
    # 检查是否已存在同名知识域
    existing = db.query(KnowledgeDomain).filter(
        KnowledgeDomain.name == domain_data.name
    ).first()
    
    if existing:
        return existing
    
    # 创建新知识域
    new_domain = KnowledgeDomain(
        name=domain_data.name,
        description=domain_data.description,
    )
    db.add(new_domain)
    db.commit()
    db.refresh(new_domain)
    return new_domain


@router.get("/{domain_id}", response_model=KnowledgeDomainResponse)
def get_domain(
    domain_id: int,
    db: Session = Depends(get_db)
):
    """
    获取单个知识域详情
    """
    domain = db.query(KnowledgeDomain).filter(KnowledgeDomain.id == domain_id).first()
    if not domain:
        raise HTTPException(status_code=404, detail="Knowledge domain not found")
    return domain


@router.put("/{domain_id}", response_model=KnowledgeDomainResponse)
def update_domain(
    domain_id: int,
    domain_data: KnowledgeDomainUpdate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """
    更新知识域信息
    """
    domain = db.query(KnowledgeDomain).filter(KnowledgeDomain.id == domain_id).first()
    if not domain:
        raise HTTPException(status_code=404, detail="Knowledge domain not found")
    
    if domain_data.name is not None:
        # 检查新名称是否与其他知识域冲突
        existing = db.query(KnowledgeDomain).filter(
            KnowledgeDomain.name == domain_data.name,
            KnowledgeDomain.id != domain_id
        ).first()
        if existing:
            raise HTTPException(status_code=400, detail="Knowledge domain name already exists")
        domain.name = domain_data.name
    
    if domain_data.description is not None:
        domain.description = domain_data.description
    
    db.commit()
    db.refresh(domain)
    return domain


@router.delete("/{domain_id}")
def delete_domain(
    domain_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """
    删除知识域
    如果有项目使用该知识域，则不允许删除
    """
    domain = db.query(KnowledgeDomain).filter(KnowledgeDomain.id == domain_id).first()
    if not domain:
        raise HTTPException(status_code=404, detail="Knowledge domain not found")
    
    # 检查是否有关联的项目
    from app.infrastructure.database import Project
    related_projects = db.query(Project).filter(Project.domain_id == domain_id).count()
    if related_projects > 0:
        raise HTTPException(
            status_code=400,
            detail=f"Cannot delete domain: {related_projects} project(s) are using this domain"
        )
    
    db.delete(domain)
    db.commit()
    return {"message": "Knowledge domain deleted successfully"}