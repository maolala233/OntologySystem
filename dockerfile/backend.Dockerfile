# Backend Dockerfile
# 默认用瘦身基础镜像（envbackend-slim.Dockerfile 构建的 0.0.4-slim，~2.5GB）；
# 如需回退旧全量基础镜像：
#   docker build --build-arg BASE_IMAGE=ontology-backend-env:0.0.3 -f dockerfile/backend.Dockerfile .
ARG BASE_IMAGE=ontology-backend-env:0.0.4-slim
FROM ${BASE_IMAGE}

# opencv 已为 headless-only（见 envbackend-slim.Dockerfile/requirements-slim.txt），
# 无需 X 系统库；如回退旧基础镜像 0.0.3（含完整版 opencv），需恢复：
#   RUN apt-get update && apt-get install -y --no-install-recommends libxcb1 libgl1 libglib2.0-0 && rm -rf /var/lib/apt/lists/*

# 复制源代码
COPY src/backend /app

# 创建必要的目录
RUN mkdir -p TTL logs

# 暴露端口
EXPOSE 3001

# 启动命令
CMD ["python", "main.py"]