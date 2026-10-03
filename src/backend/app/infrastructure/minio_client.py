# app/infrastructure/minio_client.py - MinIO 对象存储客户端（docs/design/03 §7 / 02 §2）
# M3-1：文档原件/解析文本/导出物的统一存储入口。
# 桶：ontology-uploads（原件）/ ontology-parsed（解析文本）/ ontology-exports（导出物，M3-7 用）
# storage_key 约定：{project_id}/{uuid8}.{ext}；download 走预签名 URL（302），API 进程不代理字节流。
import logging
import uuid
from datetime import timedelta
from functools import lru_cache

from minio import Minio
from minio.error import S3Error

from app.core.config import settings
from app.core.exceptions import APIError

logger = logging.getLogger("ontology_system")


class MinIOClient:
    """MinIO 薄封装：进程内单例（get_minio_client），业务层禁止直接 import minio。"""

    def __init__(self):
        self.client = Minio(
            settings.MINIO_ENDPOINT,
            access_key=settings.MINIO_ACCESS_KEY,
            secret_key=settings.MINIO_SECRET_KEY,
            secure=settings.MINIO_SECURE,
        )
        self._buckets = (
            settings.MINIO_BUCKET_UPLOADS,
            settings.MINIO_BUCKET_PARSED,
            settings.MINIO_BUCKET_EXPORTS,
        )
        self._ensure_buckets()

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
