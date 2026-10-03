# app/core/security.py - AES-256-GCM 加解密（docs/design/01 §8 / 02 §3.2）
# 用途：model_configs.api_key 等敏感字段落库加密（M2 起使用）；MCP 令牌哈希另走 SHA-256。
#
# 密文格式：base64( nonce[12] || AES-256-GCM(ciphertext + tag) )
# 主密钥：环境变量 SECRET_MASTER_KEY（32 字节由 SHA-256 派生）。
# 未配置时回退派生自 JWT_SECRET_KEY —— 仅限开发环境，生产必须显式设置 SECRET_MASTER_KEY。

import base64
import hashlib
import os

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

_NONCE_BYTES = 12


def _master_key() -> bytes:
    source = os.getenv("SECRET_MASTER_KEY", "").strip()
    if not source:
        from app.core.config import settings

        source = settings.SECRET_MASTER_KEY or settings.JWT_SECRET_KEY
    return hashlib.sha256(source.encode("utf-8")).digest()


def encrypt_str(plaintext: str) -> bytes:
    """加密字符串，返回可直接入 VARBINARY 列的密文字节。"""
    if not isinstance(plaintext, str):
        raise TypeError("encrypt_str expects str")
    nonce = os.urandom(_NONCE_BYTES)
    aes = AESGCM(_master_key())
    ciphertext = aes.encrypt(nonce, plaintext.encode("utf-8"), None)
    return base64.b64encode(nonce + ciphertext)


def decrypt_str(token: bytes) -> str:
    """解密 encrypt_str 的产物；密文被篡改时抛 InvalidTag（ValueError 子类）。"""
    if isinstance(token, str):
        token = token.encode("utf-8")
    raw = base64.b64decode(token)
    if len(raw) <= _NONCE_BYTES:
        raise ValueError("ciphertext too short")
    nonce, ciphertext = raw[:_NONCE_BYTES], raw[_NONCE_BYTES:]
    aes = AESGCM(_master_key())
    return aes.decrypt(nonce, ciphertext, None).decode("utf-8")


def mask_secret(plaintext: str) -> str:
    """回显用掩码：sk-abcdefgh12345678 -> sk-***5678；短于 12 位整体打码。"""
    import re

    if not plaintext:
        return ""
    if len(plaintext) < 12:
        return "****"
    tail = plaintext[-4:]
    m = re.match(r"^[A-Za-z0-9]{1,4}[-_]", plaintext)  # sk- / sk_ 风格前缀原样保留
    if m:
        return f"{m.group(0)}***{tail}"
    head = plaintext[:3] if plaintext[:3].isalnum() else ""
    return f"{head}***{tail}" if head else f"***{tail}"


def sha256_hex(data: str) -> str:
    """通用 SHA-256（MCP 令牌哈希、溯源校验和共用）。"""
    return hashlib.sha256(data.encode("utf-8")).hexdigest()
