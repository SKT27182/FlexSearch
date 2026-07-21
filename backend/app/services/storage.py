"""
FlexSearch Backend - Object Storage Service

MinIO/S3-compatible storage abstraction.
"""

from io import BytesIO
from typing import BinaryIO

from minio import Minio
from minio.commonconfig import CopySource
from minio.error import S3Error

from app.core.config import settings
from app.utils.logger import create_logger

logger = create_logger(__name__)


class StorageService:
    """MinIO/S3-compatible object storage service."""

    def __init__(self) -> None:
        self._client = Minio(
            settings.minio_endpoint,
            access_key=settings.minio_access_key,
            secret_key=settings.minio_secret_key,
            secure=settings.minio_secure,
        )
        self._bucket = settings.minio_bucket
        self._ensure_bucket()

    def _ensure_bucket(self) -> None:
        """Ensure the bucket exists."""
        try:
            if not self._client.bucket_exists(self._bucket):
                self._client.make_bucket(self._bucket)
                logger.info(f"Created bucket: {self._bucket}")
        except S3Error as e:
            logger.error(f"Failed to create bucket: {e}")
            raise

    def upload_file(
        self,
        path: str,
        data: bytes,
        content_type: str = "application/octet-stream",
    ) -> str:
        """
        Upload a file to storage.

        Args:
            path: Storage path (e.g., "project_id/filename")
            data: File content as bytes
            content_type: MIME type

        Returns:
            The storage path
        """
        try:
            self._client.put_object(
                self._bucket,
                path,
                BytesIO(data),
                length=len(data),
                content_type=content_type,
            )
            logger.info(f"Uploaded file: {path}")
            return path
        except S3Error as e:
            logger.error(f"Failed to upload file: {e}")
            raise

    def upload_stream(
        self,
        path: str,
        stream: BinaryIO,
        length: int,
        content_type: str = "application/octet-stream",
    ) -> str:
        """Upload a file stream to storage."""
        try:
            self._client.put_object(
                self._bucket,
                path,
                stream,
                length=length,
                content_type=content_type,
            )
            logger.info(f"Uploaded file stream: {path}")
            return path
        except S3Error as e:
            logger.error(f"Failed to upload file stream: {e}")
            raise

    def download_file(self, path: str) -> bytes:
        """
        Download a file from storage.

        Args:
            path: Storage path

        Returns:
            File content as bytes
        """
        try:
            response = self._client.get_object(self._bucket, path)
            data = response.read()
            response.close()
            response.release_conn()
            return data
        except S3Error as e:
            logger.error(f"Failed to download file: {e}")
            raise

    def delete_file(self, path: str) -> None:
        """Delete a file from storage."""
        try:
            self._client.remove_object(self._bucket, path)
            logger.info(f"Deleted file: {path}")
        except S3Error as e:
            logger.error(f"Failed to delete file: {e}")
            raise

    def promote_file(self, temporary_path: str, final_path: str) -> None:
        """Atomically publish a temporary object and remove the temporary key."""
        if self.file_exists(final_path) and not self.file_exists(temporary_path):
            return
        self._client.copy_object(
            self._bucket,
            final_path,
            CopySource(self._bucket, temporary_path),
        )
        self._client.remove_object(self._bucket, temporary_path)

    def delete_prefix(self, prefix: str) -> int:
        """Delete all objects under a prefix. Returns count deleted."""
        deleted = 0
        try:
            objects = self._client.list_objects(
                self._bucket,
                prefix=prefix,
                recursive=True,
            )
            for obj in objects:
                self._client.remove_object(self._bucket, obj.object_name)
                deleted += 1
            if deleted:
                logger.info("Deleted %s objects under prefix: %s", deleted, prefix)
            return deleted
        except S3Error as e:
            logger.error(f"Failed to delete prefix {prefix}: {e}")
            raise

    def upload_directory(self, local_dir: str, prefix: str) -> None:
        """Upload all files from a local directory to storage under prefix."""
        from pathlib import Path

        root = Path(local_dir)
        for path in root.rglob("*"):
            if path.is_file():
                rel = path.relative_to(root).as_posix()
                key = f"{prefix.rstrip('/')}/{rel}"
                data = path.read_bytes()
                content_type = "application/octet-stream"
                if path.suffix == ".yaml":
                    content_type = "application/x-yaml"
                elif path.suffix == ".parquet":
                    content_type = "application/vnd.apache.parquet"
                elif path.suffix == ".graphml":
                    content_type = "application/graphml+xml"
                elif path.suffix in {".md", ".txt", ".csv"}:
                    content_type = "text/plain; charset=utf-8"
                self.upload_file(key, data, content_type=content_type)

    def download_prefix(self, prefix: str, local_dir: str) -> int:
        """Download all objects under prefix into local_dir. Returns file count."""
        from pathlib import Path

        root = Path(local_dir)
        root.mkdir(parents=True, exist_ok=True)
        count = 0
        for key in self.list_files(prefix):
            if not key.startswith(prefix):
                continue
            rel = key[len(prefix) :].lstrip("/")
            if not rel:
                continue
            dest = root / rel
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(self.download_file(key))
            count += 1
        return count

    def file_exists(self, path: str) -> bool:
        """Check if a file exists."""
        try:
            self._client.stat_object(self._bucket, path)
            return True
        except S3Error:
            return False

    def list_files(self, prefix: str = "") -> list[str]:
        """List files with given prefix."""
        try:
            objects = self._client.list_objects(
                self._bucket, prefix=prefix, recursive=True
            )
            return [obj.object_name for obj in objects]
        except S3Error as e:
            logger.error(f"Failed to list files: {e}")
            raise

    def get_presigned_url(self, path: str, expires_hours: int = 1) -> str:
        """Get a presigned URL for temporary access."""
        from datetime import timedelta

        try:
            return self._client.presigned_get_object(
                self._bucket,
                path,
                expires=timedelta(hours=expires_hours),
            )
        except S3Error as e:
            logger.error(f"Failed to generate presigned URL: {e}")
            raise


# Singleton instance
_storage_service: StorageService | None = None


def get_storage_service() -> StorageService:
    """Get storage service singleton."""
    global _storage_service
    if _storage_service is None:
        _storage_service = StorageService()
    return _storage_service
