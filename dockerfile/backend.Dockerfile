# Backend Dockerfile
FROM ontology-backend-env:0.0.3

# docling 表格模型依赖 opencv（cv2），需 X 系统库（缺 libxcb.so.1 会令 docling 熔断降级 native）
RUN apt-get update && apt-get install -y --no-install-recommends libxcb1 libgl1 libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/*

# 复制源代码
COPY src/backend /app

# 创建必要的目录
RUN mkdir -p TTL logs

# 暴露端口
EXPOSE 3001

# 启动命令
CMD ["python", "main.py"]