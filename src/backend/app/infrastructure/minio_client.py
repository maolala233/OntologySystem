# app/infrastructure/minio_client.py - MinIO 对象存储客户端（docs/design/03 §7 / 02 §2）
# M3-1：文档原件/解析文本/导出物的统一存储入口。
# 桶：ontology-uploads（原件）/ ontology-parsed（解析文本）/ ontology-exports（导出物，M3-7 用）
# storage_key 约定：{project_id}/{uuid8}.{ext}；download 走预签名 URL（302），API 进程不代理字节流。
import logging
import uuid
from datetime import timedelta
from functools import lru_cache
from urllib.parse import urlsplit, urlunsplit

from minio import Minio
from minio.error import S3Error

from app.core.config import settings
from app.core.exceptions import APIError

logger = logging.getLogger("ontology_system")


class MinIOClient:
    """MinIO 薄封装：进程内单例（get_minio_client），业务层禁止直接 import minio。"""

    def __init__(self):
        endpoint, access_key, secret_key, secure, buckets = self._resolve_config()
        self.client = Minio(
            endpoint,
            access_key=access_key,
            secret_key=secret_key,
            secure=secure,
        )
        self._buckets = buckets
        self._ensure_buckets()

    def _resolve_config(self):
        """连接参数：platform_config（DB 覆盖，env_config_service）→ .env 默认。"""
        try:
            from app.services.env_config_service import get_effective_raw
            from app.infrastructure.database import SessionLocal
            db = SessionLocal()
            try:
                cfg = get_effective_raw(db)
                return (cfg["minio_endpoint"], cfg["minio_access_key"],
                        cfg["minio_secret_key"], bool(cfg["minio_secure"]),
                        (settings.MINIO_BUCKET_UPLOADS, settings.MINIO_BUCKET_PARSED,
                         settings.MINIO_BUCKET_EXPORTS))
            finally:
                db.close()
        except Exception as e:
            logger.debug(f"[MinIO] 读 DB 配置失败，回退 .env：{e}")
        return (settings.MINIO_ENDPOINT, settings.MINIO_ACCESS_KEY,
                settings.MINIO_SECRET_KEY, settings.MINIO_SECURE,
                (settings.MINIO_BUCKET_UPLOADS, settings.MINIO_BUCKET_PARSED,
                 settings.MINIO_BUCKET_EXPORTS))

    def _ensure_buckets(self):
        for bucket in self._buckets:
            try:
                if not self.client.bucket_exists(bucket):
                    self.client.make_bucket(bucket)
                    logger.info(f"[MinIO] 已创建桶: {bucket}")
            except S3Error as e:
                logger.error(f"[MinIO] 桶 {bucket} 检查/创建失败: {e}")
                raise APIError("对象存储不可用", code="MINIO_UNAVAILABLE", status_code=503)

    # ---- 基础对象操作 ----

    @staticmethod
    def build_key(project_id: int, filename: str) -> str:
        """storage_key：{project_id}/{uuid8}.{ext}（02 §3.4）。ext 取原名后缀，防路径注入。"""
        ext = ""
        name = (filename or "").strip()
        if "." in name:
            candidate = name.rsplit(".", 1)[1].lower()
            if candidate.isalnum() and len(candidate) <= 10:
                ext = f".{candidate}"
        return f"{project_id}/{uuid.uuid4().hex[:8]}{ext}"

    def put_file(self, bucket: str, key: str, local_path: str, content_type: str = "application/octet-stream") -> str:
        try:
            self.client.fput_object(bucket, key, local_path, content_type=content_type)
            return key
        except S3Error as e:
            logger.error(f"[MinIO] 上传失败 bucket={bucket} key={key}: {e}")
            raise APIError("文件存储失败", code="MINIO_PUT_FAILED", status_code=502)

    def put_bytes(self, bucket: str, key: str, data: bytes,
                  content_type: str = "application/octet-stream") -> str:
        try:
            import io

            self.client.put_object(bucket, key, io.BytesIO(data), length=len(data),
                                   content_type=content_type)
            return key
        except S3Error as e:
            logger.error(f"[MinIO] 上传失败 bucket={bucket} key={key}: {e}")
            raise APIError("文件存储失败", code="MINIO_PUT_FAILED", status_code=502)

    def get_bytes(self, bucket: str, key: str) -> bytes:
        """对象内容读取（解析管道取原件用）。"""
        try:
            resp = self.client.get_object(bucket, key)
            try:
                return resp.read()
            finally:
                resp.close()
                resp.release_conn()
        except S3Error as e:
            logger.error(f"[MinIO] 读取失败 bucket={bucket} key={key}: {e}")
            raise APIError("文件读取失败", code="MINIO_GET_FAILED", status_code=502)

    def get_presigned_url(self, bucket: str, key: str, expires: timedelta = timedelta(hours=1)) -> str:
        try:
            return self.client.presigned_get_object(bucket, key, expires=expires)
        except S3Error as e:
            logger.error(f"[MinIO] 预签名失败 bucket={bucket} key={key}: {e}")
            raise APIError("生成下载链接失败", code="MINIO_PRESIGN_FAILED", status_code=502)

    def get_browser_presigned_url(self, bucket: str, key: str, request=None,
                                  expires: timedelta = timedelta(hours=1)) -> str:
        """面向浏览器的预签名 URL：内网签名 → 同域 /minio 前缀改写（方案C）。

        签名覆盖的是签发时的 Host 头（=MINIO_ENDPOINT，容器内网名），浏览器侧由
        nginx 把 /minio/* 反代回内网并原样回传该 Host，MinIO 侧签名校验仍通过。
        MINIO_PUBLIC_PATH_PREFIX 未配置或无请求上下文时原样返回（dev 直连 9010）。"""
        return to_browser_url(self.get_presigned_url(bucket, key, expires), request)

    def stat_object(self, bucket: str, key: str) -> dict | None:
        try:
            st = self.client.stat_object(bucket, key)
            return {"size": st.size, "etag": st.etag, "last_modified": st.last_modified}
        except S3Error:
            return None

    def remove_object(self, bucket: str, key: str) -> bool:
        """软删策略下的物理清理仅在有明确指令时调用；失败只记日志（孤儿对象可离线清扫）。"""
        try:
            self.client.remove_object(bucket, key)
            return True
        except S3Error as e:
            logger.warning(f"[MinIO] 删除失败 bucket={bucket} key={key}: {e}")
            return False


@lru_cache(maxsize=1)
def get_minio_client() -> MinIOClient:
    return MinIOClient()


def to_browser_url(presigned: str, request=None) -> str:
    """内网预签名 URL → 浏览器同域 /minio 相对路径（方案C，2026-11-06 加固）。

    改写为**相对 URL**（/minio/bucket/key?签名）：浏览器按"当前访问域"解析——
    服务器 IP/域名/端口怎么访问都成立，其他计算机不再 ERR_NAME_NOT_RESOLVED
    （旧实现用 Host 头拼绝对 URL：经 vite 代理或 prefix 未配置时会把
    127.0.0.1/内网容器名漏给浏览器）。签名校验不受影响：nginx /minio 段
    原样回传 MINIO_ENDPOINT 作为 Host。
    request 缺失时也返回相对改写（签名 URL 的 path/query 自带全部信息），
    仅当 URL 本就不是本 MinIO 端点（防御）时原样返回。
    """
    prefix = (settings.MINIO_PUBLIC_PATH_PREFIX or "/minio").rstrip("/")
    parts = urlsplit(presigned)
    endpoint = urlsplit(str(settings.MINIO_ENDPOINT or "").replace("http://", "").replace("https://", ""))
    # 防御：签名 URL 的 host 若与配置的 MinIO 端点完全无关（多 MinIO 场景），不改写
    if endpoint.netloc and parts.netloc and endpoint.netloc not in parts.netloc \
            and parts.netloc not in str(settings.MINIO_ENDPOINT):
        return presigned
    return urlunsplit(("", "", f"{prefix}{parts.path}", parts.query, ""))
