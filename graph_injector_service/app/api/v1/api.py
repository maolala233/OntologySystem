"""
API v1 路由器配置
挂载各个功能端点
"""
from fastapi import APIRouter
from app.api.v1.endpoints import inject, schema, system, instance

api_router = APIRouter()

# 挂载各个端点
api_router.include_router(inject.router, tags=["inject"])
api_router.include_router(schema.router, tags=["schema"])
api_router.include_router(system.router, tags=["system"])
api_router.include_router(instance.router, tags=["instance"])
