# Backend Dockerfile
FROM python:3.11-slim

# 设置工作目录
WORKDIR /app

# 安装系统依赖包，包括antiword
RUN apt-get update && apt-get install -y \
    antiword \
    && rm -rf /var/lib/apt/lists/*

# 复制requirements文件
COPY requirements.txt .

# 升级pip并安装Python依赖
RUN pip install --upgrade pip -i https://pypi.tuna.tsinghua.edu.cn/simple
RUN pip install --no-cache-dir -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple

# 复制源代码
COPY src/backend /app

# 创建必要的目录
RUN mkdir -p TTL logs

# 暴露端口
EXPOSE 3001

# 启动命令
CMD ["python", "main.py"]