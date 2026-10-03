# app/adapters/_compat.py - Semantica 版本探测与中文补丁清单（docs/design/01 §3.1）
# 版本 pin：semantica==0.7.*（requirements.txt）。版本升级只允许发生在本文件。

from __future__ import annotations

from importlib.metadata import PackageNotFoundError, version

# Semantica 对中文的已知缺陷与平台侧补法（docs/design/04 §10 / §11）：
#   1. split 模块分句正则只认英文句号 —— 平台用中文分句状态机替代（adapters/chunking）
#   2. semantic_extract 的 pattern NER 是英文正则、spaCy 默认英文模型 —— 全部禁用，
#      抽取只走 LLM 方法（adapters/extraction）
#   3. 归一化里的 isalnum() 对中文恒 False —— 平台归一化一律 isascii() 守卫
#      （adapters/resolution 第 1 层）
CHINESE_PATCHES = [
    "chunking: 中文分句状态机替代 _split_sentences_regex",
    "extraction: 禁用 pattern/spacy 方法，仅 LLM 方法",
    "resolution: 归一化 isascii() 守卫 + 拼音 blocking",
]

REQUIRED_MIN_VERSION = "0.7.0"


def get_semantica_version() -> str | None:
    """返回已安装的 semantica 版本号；未安装返回 None。"""
    try:
        return version("semantica")
    except PackageNotFoundError:
        return None


def assert_semantica_available() -> str:
    """确保 semantica 已安装且满足最低版本，返回版本号；否则抛 RuntimeError。

    M0 冒烟测试直接调用本函数（验收门槛之一）。
    """
    ver = get_semantica_version()
    if ver is None:
        raise RuntimeError(
            "semantica 未安装：pip install 'semantica[all]==0.7.*'"
        )
    minor_ok = tuple(int(p) for p in ver.split(".")[:2]) >= (0, 7)
    if not minor_ok:
        raise RuntimeError(f"semantica {ver} 过旧，要求 >= {REQUIRED_MIN_VERSION}")
    return ver


__all__ = ["CHINESE_PATCHES", "REQUIRED_MIN_VERSION", "get_semantica_version", "assert_semantica_available"]
