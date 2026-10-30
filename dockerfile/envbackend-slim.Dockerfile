# Backend ENV Dockerfile（瘦身版）
# 原 envbackend.Dockerfile + requirements.txt(semantica[all]) 构建出 8.65GB 的
# ontology-backend-env:0.0.3；本文件构建精简版，目标 ~2.5GB。
# 瘦身要点：
#   1. torch 从 PyTorch CPU 源先装（占住依赖位），阻断后续 docling 从 PyPI 拉
#      CUDA 版 torch + nvidia/triton 全家桶（约省 4.2GB）；
#   2. 依赖清单换 dockerfile/requirements-slim.txt（剔除 semantica[all]，
#      显式声明源码 lazy import 的 docling/rapidocr/pyoxigraph/xlrd/lxml/pillow）。
# 构建方式（必须在仓库根目录执行，构建上下文 = 仓库根）：
#   docker build -f dockerfile/envbackend-slim.Dockerfile -t ontology-backend-env:0.0.4-slim .
FROM python:3.11-slim

WORKDIR /app

# 系统依赖：antiword（.doc 解析）
RUN apt-get update && apt-get install -y --no-install-recommends antiword \
    && rm -rf /var/lib/apt/lists/*

# 关键一步：先装 CPU 版 torch（不带 CUDA/nvidia/triton），占住 docling 的 torch 依赖位。
# download.pytorch.org 国内直连易超时，用阿里云 pytorch-wheels 镜像（cp311 最高 2.6.0+cpu，
# 满足 docling-ibm-models 的 torch>=2.2.2,<3.0 约束）；torchvision 由 tuna/同源配对 0.21.0。
RUN pip install --no-cache-dir torch==2.6.0+cpu torchvision==0.21.0 \
    --find-links https://mirrors.aliyun.com/pytorch-wheels/cpu/ \
    -i https://pypi.tuna.tsinghua.edu.cn/simple

# 其余依赖（tuna 源）
COPY dockerfile/requirements-slim.txt /tmp/requirements-slim.txt
RUN pip install --no-cache-dir -r /tmp/requirements-slim.txt \
    -i https://pypi.tuna.tsinghua.edu.cn/simple \
    && rm -f /tmp/requirements-slim.txt
