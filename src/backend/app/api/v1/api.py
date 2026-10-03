# app/api/v1/api.py - API路由器配置文件
# 功能：定义API版本1的路由器，并挂载各个端点（本体、RAG）
# M3-1：files 端点已删除（03 §18 收编：新上传走 /api/projects/{id}/documents/upload）；
#       rag/ontology 将在 M5/M3-7 依次收编删除。

from fastapi import APIRouter
from app.api.v1.endpoints import ontology, rag

api_router = APIRouter()

# 挂载各个端点
api_router.include_router(ontology.router, prefix="/ontology", tags=["ontology"])
api_router.include_router(rag.router, prefix="/rag", tags=["rag"])
