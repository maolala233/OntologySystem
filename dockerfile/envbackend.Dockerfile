# Backend Dockerfile
FROM python:3.11-slim
ARG DEBIAN_MIRROR=mirrors.aliyun.com

# 设置工作目录
WORKDIR /app

# 安装系统依赖包，包括antiword；apt 源切到国内镜像
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
    apt-get install -y --no-install-recommends antiword; \
    rm -rf /var/lib/apt/lists/*

# 复制requirements文件
COPY requirements.txt .

# 升级pip并安装Python依赖
RUN pip install --upgrade pip -i https://pypi.tuna.tsinghua.edu.cn/simple
RUN pip install --no-cache-dir -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple