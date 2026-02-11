# main.py - FastAPI主应用程序入口点
# 功能：定义FastAPI应用实例，配置CORS中间件，注册API路由，提供健康检查接口

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
import uvicorn
from app.api.v1.api import api_router

from app.api import auth, ontology, system
from app.infrastructure.database import init_db

# 初始化数据库 (创建表)
init_db()

app = FastAPI(title="AI 本体构建系统 API", version="1.0.0")

# 添加 CORS 中间件
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 注册路由
app.include_router(auth.router)
app.include_router(ontology.router)
app.include_router(system.router)
app.include_router(api_router, prefix="/api/v1")

# 健康检查接口
@app.get('/health')
def get_health():
    return {'status': 'OK'}

if __name__ == '__main__':
    uvicorn.run(app, host='0.0.0.0', port=3001)