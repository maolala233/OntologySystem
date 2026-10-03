# tests/test_security.py - AES-256-GCM 加解密与掩码（docs/design/01 §8）
import pytest

from app.core.security import decrypt_str, encrypt_str, mask_secret, sha256_hex


def test_aes_roundtrip_ascii():
    token = encrypt_str("sk-abcdef1234567890")
    assert decrypt_str(token) == "sk-abcdef1234567890"


def test_aes_roundtrip_unicode():
    secret = "密钥-测试- 🔑 key_2026"
    assert decrypt_str(encrypt_str(secret)) == secret


def test_aes_ciphertext_not_plaintext():
    token = encrypt_str("sk-abcdef1234567890")
    assert b"sk-abcdef" not in token


def test_aes_unique_nonce():
    assert encrypt_str("same") != encrypt_str("same")


def test_aes_tamper_detected():
    token = bytearray(encrypt_str("sensitive"))
    token[20] ^= 0xFF  # 翻转密文字节
    with pytest.raises(Exception):
        decrypt_str(bytes(token))


def test_aes_short_ciphertext_rejected():
    import base64

    with pytest.raises(ValueError):
        decrypt_str(base64.b64encode(b"tooshort"))


def test_mask_secret():
    assert mask_secret("sk-abcdefgh12345678") == "sk-***5678"
    assert mask_secret("short") == "****"      # 短密钥整体打码，不泄漏前缀
    assert mask_secret("") == ""


def test_sha256_hex_stable():
    assert sha256_hex("abc") == sha256_hex("abc")
    assert len(sha256_hex("abc")) == 64
    assert sha256_hex("abc") != sha256_hex("abd")
