"""
知识域管理 API
提供知识域的增删改查功能
"""
from fastapi import APIRouter, Depends, HTTPException, status, Query
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from typing import List, Optional
from app.infrastructure.database import get_db, KnowledgeDomain, Project
from app.schemas.domain import (
    KnowledgeDomainCreate,
    KnowledgeDomainUpdate,
    KnowledgeDomainResponse,
)
from app.schemas.ontology import ProjectResponse
from app.api.auth import get_current_user
from app.core.deps import require_role
from app.infrastructure.database import User

router = APIRouter(prefix="/api/domains", tags=["domains"])


@router.get("", response_model=List[KnowledgeDomainResponse])
def get_all_domains(
    _user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """
    获取所有知识域列表
    用于前端下拉选择；附带 project_count（全部项目数，含未发布，与删除接口校验口径一致）
    登录即可读（知识域是慢变参考数据，登录后的下拉到处都要用）；此前无鉴权，未登录也能拉。
    """
    from sqlalchemy import func

    counts = dict(
        db.query(Project.domain_id, func.count(Project.id))
        .group_by(Project.domain_id)
        .all()
    )
    domains = db.query(KnowledgeDomain).order_by(KnowledgeDomain.name).all()
    return [
        KnowledgeDomainResponse(
            id=d.id,
            name=d.name,
            description=d.description,
            created_at=d.created_at,
            updated_at=d.updated_at,
            project_count=counts.get(d.id, 0),
        )
        for d in domains
    ]


@router.post("", response_model=KnowledgeDomainResponse, status_code=status.HTTP_201_CREATED)
def create_domain(
    domain_data: KnowledgeDomainCreate,
    _admin: User = Depends(require_role("admin")),
    db: Session = Depends(get_db)
):
    """
    创建新的知识域
    名称重复时返回 409（此前静默返回已存在的记录 + 201，前端会误报「已创建」）
    """
    # 检查是否已存在同名知识域
    existing = db.query(KnowledgeDomain).filter(
        KnowledgeDomain.name == domain_data.name
    ).first()

    if existing:
        raise HTTPException(status_code=409, detail="知识域名称已存在")
    
    # 创建新知识域
    new_domain = KnowledgeDomain(
        name=domain_data.name,
        description=domain_data.description,
    )
    db.add(new_domain)
    try:
        db.commit()
    except IntegrityError:
        # check-then-insert 之间有竞态：并发同名会撞唯一约束，按重名处理而不是 500
        db.rollback()
        raise HTTPException(status_code=409, detail="知识域名称已存在")
    db.refresh(new_domain)
    return new_domain


@router.get("/{domain_id}", response_model=KnowledgeDomainResponse)
def get_domain(
    domain_id: int,
    _user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """
    获取单个知识域详情
    """
    domain = db.query(KnowledgeDomain).filter(KnowledgeDomain.id == domain_id).first()
    if not domain:
        raise HTTPException(status_code=404, detail="知识域不存在")
    return domain


@router.put("/{domain_id}", response_model=KnowledgeDomainResponse)
def update_domain(
    domain_id: int,
    domain_data: KnowledgeDomainUpdate,
    _admin: User = Depends(require_role("admin")),
    db: Session = Depends(get_db)
):
    """
    更新知识域信息
    """
    domain = db.query(KnowledgeDomain).filter(KnowledgeDomain.id == domain_id).first()
    if not domain:
        raise HTTPException(status_code=404, detail="知识域不存在")

    if domain_data.name is not None:
        # 检查新名称是否与其他知识域冲突
        existing = db.query(KnowledgeDomain).filter(
            KnowledgeDomain.name == domain_data.name,
            KnowledgeDomain.id != domain_id
        ).first()
        if existing:
            raise HTTPException(status_code=409, detail="知识域名称已存在")
        domain.name = domain_data.name

    if domain_data.description is not None:
        domain.description = domain_data.description

    try:
        db.commit()
    except IntegrityError:
        # 并发改同名：唯一约束兜底，统一按重名处理
        db.rollback()
        raise HTTPException(status_code=409, detail="知识域名称已存在")
    db.refresh(domain)
    return domain


@router.get("/{domain_id}/projects")
def get_domain_projects(
    domain_id: int,
    _admin: User = Depends(require_role("admin")),
    db: Session = Depends(get_db)
):
    """
    获取知识域下的所有项目列表（包含详细统计信息）
    用于删除前查看需要迁移的项目——管理端迁移/删除流程专用，admin 才能看
    """
    from app.core.logging import logger

    # 检查知识域是否存在
    domain = db.query(KnowledgeDomain).filter(KnowledgeDomain.id == domain_id).first()
    if not domain:
        raise HTTPException(status_code=404, detail="知识域不存在")
    
    # 获取该知识域下的所有项目
    projects = db.query(Project).filter(Project.domain_id == domain_id).all()
    
    logger.info(f"[get_domain_projects] domain_id={domain_id}, found {len(projects)} projects")
    
    # 为每个项目添加统计信息
    result = []
    for project in projects:
        graph_data = project.graph_data or {}
        nodes = graph_data.get("nodes", [])
        edges = graph_data.get("edges", [])
        
        # 统计类数量（owl:Class 类型的节点）
        class_count = len([n for n in nodes if n.get("data", {}).get("type") == "owl:Class"])
        
        # 统计实例数量（owl:NamedIndividual 类型的节点）
        instance_count = len([n for n in nodes if n.get("data", {}).get("type") == "owl:NamedIndividual"])
        
        # 统计关系总数
        edge_count = len(edges)
        
        # 当前知识域：上面的查询已限定 Project.domain_id == domain_id，全量同一个域，
        # 直接复用 domain，不再逐项目回查（原 N+1）
        project_data = {
            "id": project.id,
            "name": project.name,
            "description": project.description,
            "is_published": project.is_published,
            "domain_id": project.domain_id,
            "current_domain_name": domain.name,
            "class_count": class_count,
            "instance_count": instance_count,
            "edge_count": edge_count,
        }
        
        logger.info(f"[get_domain_projects] project={project.name}, class_count={class_count}, instance_count={instance_count}, edge_count={edge_count}")
        
        result.append(project_data)
    
    logger.info(f"[get_domain_projects] returning {len(result)} projects with stats")
    
    return result


from pydantic import BaseModel

class ProjectMigrationItem(BaseModel):
    """单个项目迁移请求"""
    project_id: int
    target_domain_id: int

class BatchMigrationRequest(BaseModel):
    """批量迁移请求"""
    items: List[ProjectMigrationItem]


@router.post("/{domain_id}/migrate-projects")
def migrate_projects(
    domain_id: int,
    project_ids: List[int] = Query(..., description="要迁移的项目 ID 列表"),
    target_domain_id: int = Query(..., description="目标知识域 ID"),
    _admin: User = Depends(require_role("admin")),
    db: Session = Depends(get_db)
):
    """
    将知识域中的项目迁移到另一个知识域（旧版，所有项目迁移到同一目标）
    
    参数通过 query params 传递：
    - project_ids: 项目 ID 列表（可重复传递，如 ?project_ids=1&project_ids=2）
    - target_domain_id: 目标知识域 ID
    """
    # 检查源知识域是否存在
    source_domain = db.query(KnowledgeDomain).filter(KnowledgeDomain.id == domain_id).first()
    if not source_domain:
        raise HTTPException(status_code=404, detail="源知识域不存在")
    
    # 检查目标知识域是否存在
    target_domain = db.query(KnowledgeDomain).filter(KnowledgeDomain.id == target_domain_id).first()
    if not target_domain:
        raise HTTPException(status_code=404, detail="目标知识域不存在")
    
    # 迁移项目
    migrated_count = 0
    for project_id in project_ids:
        project = db.query(Project).filter(
            Project.id == project_id,
            Project.domain_id == domain_id
        ).first()
        if project:
            project.domain_id = target_domain_id
            migrated_count += 1
    
    db.commit()
    
    return {
        "message": f"成功迁移 {migrated_count} 个项目到 {target_domain.name}",
        "migrated_count": migrated_count,
        "target_domain": {
            "id": target_domain.id,
            "name": target_domain.name,
        }
    }


@router.post("/{domain_id}/migrate-projects-batch")
def migrate_projects_batch(
    domain_id: int,
    request: BatchMigrationRequest,
    _admin: User = Depends(require_role("admin")),
    db: Session = Depends(get_db)
):
    """
    批量迁移项目到不同的目标知识域
    
    请求体：
    - items: 迁移项目列表，每个项目包含 project_id 和 target_domain_id
    
    返回：
    - 迁移结果详情
    """
    # 检查源知识域是否存在
    source_domain = db.query(KnowledgeDomain).filter(KnowledgeDomain.id == domain_id).first()
    if not source_domain:
        raise HTTPException(status_code=404, detail="源知识域不存在")
    
    # 收集所有目标知识域 ID
    target_domain_ids = set(item.target_domain_id for item in request.items)
    
    # 验证所有目标知识域是否存在
    target_domains = {}
    for target_id in target_domain_ids:
        target_domain = db.query(KnowledgeDomain).filter(KnowledgeDomain.id == target_id).first()
        if not target_domain:
            raise HTTPException(status_code=404, detail=f"目标知识域 {target_id} 不存在")
        target_domains[target_id] = target_domain
    
    # 执行迁移
    migrated_count = 0
    migration_details = []
    
    for item in request.items:
        project = db.query(Project).filter(
            Project.id == item.project_id,
            Project.domain_id == domain_id
        ).first()
        if project:
            project.domain_id = item.target_domain_id
            migrated_count += 1
            migration_details.append({
                "project_id": item.project_id,
                "project_name": project.name,
                "from_domain": source_domain.name,
                "to_domain": target_domains[item.target_domain_id].name,
            })
    
    db.commit()
    
    return {
        "message": f"成功迁移 {migrated_count} 个项目",
        "migrated_count": migrated_count,
        "migration_details": migration_details,
    }


@router.delete("/{domain_id}")
def delete_domain(
    domain_id: int,
    migrate_to_domain_id: Optional[int] = None,
    _admin: User = Depends(require_role("admin")),
    db: Session = Depends(get_db)
):
    """
    删除知识域
    
    参数:
    - migrate_to_domain_id: 可选，如果要删除的知识域中有项目，可以指定目标知识域进行迁移
    
    如果有项目使用该知识域且未指定迁移目标，则不允许删除
    """
    domain = db.query(KnowledgeDomain).filter(KnowledgeDomain.id == domain_id).first()
    if not domain:
        raise HTTPException(status_code=404, detail="知识域不存在")
    
    # 检查是否有关联的项目
    related_projects = db.query(Project).filter(Project.domain_id == domain_id).all()
    
    if len(related_projects) > 0:
        if migrate_to_domain_id is not None:
            # 迁移项目到目标知识域
            target_domain = db.query(KnowledgeDomain).filter(KnowledgeDomain.id == migrate_to_domain_id).first()
            if not target_domain:
                raise HTTPException(status_code=404, detail="目标知识域不存在")
            
            # 执行迁移
            for project in related_projects:
                project.domain_id = migrate_to_domain_id
            
            db.commit()
            return {
                "message": f"知识域已删除，{len(related_projects)} 个项目已迁移到 {target_domain.name}",
                "migrated_count": len(related_projects),
                "target_domain": {
                    "id": target_domain.id,
                    "name": target_domain.name,
                }
            }
        else:
            # 未指定迁移目标，返回错误和项目列表
            raise HTTPException(
                status_code=400,
                detail={
                    "message": f"知识域中有 {len(related_projects)} 个项目，无法直接删除。请先迁移项目或指定 migrate_to_domain_id 参数。",
                    "project_count": len(related_projects),
                    "projects": [
                        {"id": p.id, "name": p.name, "is_published": p.is_published}
                        for p in related_projects
                    ]
                }
            )
    
    db.delete(domain)
    db.commit()
    return {"message": "知识域已删除"}
