# Backend Dockerfile
FROM ontology-backend-env:0.0.1

# 复制源代码
COPY src/backend /app

# 创建必要的目录
RUN mkdir -p TTL logs

# 暴露端口
EXPOSE 3001

# 启动命令
CMD ["python", "main.py"]