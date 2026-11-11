# Backend Dockerfile
# 默认用瘦身基础镜像（envbackend-slim.Dockerfile 构建的 0.0.4-slim，~2.5GB）；
# 如需回退旧全量基础镜像：
#   docker build --build-arg BASE_IMAGE=ontology-backend-env:0.0.3 -f dockerfile/backend.Dockerfile .
ARG BASE_IMAGE=ontology-backend-env:0.0.4-slim
FROM ${BASE_IMAGE}
ARG DEBIAN_MIRROR=mirrors.aliyun.com

# opencv 已为 headless-only（见 envbackend-slim.Dockerfile/requirements-slim.txt），
# 无需 X 系统库；如回退旧基础镜像 0.0.3（含完整版 opencv），需恢复：
#   RUN apt-get update && apt-get install -y --no-install-recommends libxcb1 libgl1 libglib2.0-0 && rm -rf /var/lib/apt/lists/*

# opencv-python（semantica[all] 拉取的非 headless 版）和 torch 在 slim 基础镜像里
# 需要这些运行库，否则 import cv2 / torch 会因 libGL.so.1、libgomp.so.1 缺失失败。
# 先把 Debian apt 源切到国内镜像，避免 deb.debian.org 超时。
RUN set -eux; \
    if [ -f /etc/apt/sources.list ]; then \
      sed -i \
        -e "s|deb.debian.org|${DEBIAN_MIRROR}|g" \
        -e "s|security.debian.org|${DEBIAN_MIRROR}|g" \
        -e "s|archive.debian.org|${DEBIAN_MIRROR}|g" \
        /etc/apt/sources.list; \
    fi; \
    for f in /etc/apt/sources.list.d/*.sources /etc/apt/sources.list.d/*.list; do \
      [ -e "$f" ] || continue; \
      sed -i \
        -e "s|deb.debian.org|${DEBIAN_MIRROR}|g" \
        -e "s|security.debian.org|${DEBIAN_MIRROR}|g" \
        -e "s|archive.debian.org|${DEBIAN_MIRROR}|g" \
        "$f"; \
    done; \
    apt-get update; \
    apt-get install -y --no-install-recommends \
      libgl1 libglib2.0-0 libsm6 libxext6 libxrender1 libgomp1 antiword; \
    rm -rf /var/lib/apt/lists/*

# 复制源代码
COPY src/backend /app

# 创建必要的目录
RUN mkdir -p TTL logs

# 暴露端口
EXPOSE 3001

# 启动命令
CMD ["python", "main.py"]