# app/api/v1/api.py - API路由器配置文件
# 功能：定义API版本1的路由器，并挂载各个端点（本体、RAG、文件）

from fastapi import APIRouter
from app.api.v1.endpoints import ontology, rag, files

api_router = APIRouter()

# 挂载各个端点
api_router.include_router(ontology.router, prefix="/ontology", tags=["ontology"])
api_router.include_router(rag.router, prefix="/rag", tags=["rag"])
api_router.include_router(files.router, prefix="/files", tags=["files"])