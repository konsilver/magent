"""Aliyun OSS (Object Storage Service) backend for production."""

import logging
import os
from pathlib import Path

from core.infra.exceptions import StorageError
from core.storage.protocol import StorageBackend

logger = logging.getLogger(__name__)


class OSSStorageBackend(StorageBackend):
    """Aliyun OSS (Object Storage Service) backend for production."""

    def __init__(self):
        try:
            import oss2
            self._oss2 = oss2

            endpoint = os.getenv("OSS_ENDPOINT")
            if not endpoint:
                raise ValueError("OSS_ENDPOINT environment variable is required")

            self.bucket_name = os.getenv("OSS_BUCKET")
            if not self.bucket_name:
                raise ValueError("OSS_BUCKET environment variable is required")

            access_key_id = os.getenv("OSS_ACCESS_KEY_ID")
            access_key_secret = os.getenv("OSS_ACCESS_KEY_SECRET")
            if not access_key_id or not access_key_secret:
                raise ValueError("OSS_ACCESS_KEY_ID and OSS_ACCESS_KEY_SECRET are required")

            auth = oss2.Auth(access_key_id, access_key_secret)
            self.bucket = oss2.Bucket(auth, endpoint, self.bucket_name)

            self.key_prefix = os.getenv("OSS_KEY_PREFIX", "")
            if self.key_prefix and not self.key_prefix.endswith("/"):
                self.key_prefix += "/"

            self.presigned_url_expiry = int(os.getenv("OSS_PRESIGNED_URL_EXPIRY", "900"))

            logger.info(
                f"OSSStorageBackend initialized: bucket={self.bucket_name}, "
                f"endpoint={endpoint}, prefix={self.key_prefix!r}"
            )
        except ImportError:
            raise ImportError("oss2 is required for OSSStorageBackend. Install with: pip install oss2")
        except Exception as e:
            logger.error(f"Failed to initialize OSS storage backend: {e}")
            raise StorageError(operation="init", error=str(e))

    def _full_key(self, storage_key: str) -> str:
        return f"{self.key_prefix}{storage_key.lstrip('/')}"

    def upload(self, file_path: str, storage_key: str) -> str:
        try:
            source = Path(file_path)
            if not source.exists():
                raise StorageError(operation="upload", error=f"Source file not found: {file_path}")
            full_key = self._full_key(storage_key)
            self.bucket.put_object_from_file(full_key, str(source))
            logger.info(f"File uploaded to OSS: {full_key}")
            return f"oss://{self.bucket_name}/{full_key}"
        except self._oss2.exceptions.OssError as e:
            logger.error(f"OSS error during upload: {e}")
            raise StorageError(operation="upload", error=str(e))
        except StorageError:
            raise
        except Exception as e:
            logger.error(f"Failed to upload file to OSS: {e}")
            raise StorageError(operation="upload", error=str(e))

    def upload_bytes(self, content: bytes, storage_key: str) -> str:
        try:
            full_key = self._full_key(storage_key)
            self.bucket.put_object(full_key, content)
            logger.info(f"Bytes uploaded to OSS: {full_key}")
            return f"oss://{self.bucket_name}/{full_key}"
        except self._oss2.exceptions.OssError as e:
            logger.error(f"OSS error during upload_bytes: {e}")
            raise StorageError(operation="upload", error=str(e))
        except Exception as e:
            logger.error(f"Failed to upload bytes to OSS: {e}")
            raise StorageError(operation="upload", error=str(e))

    def download(self, storage_key: str, local_path: str) -> None:
        try:
            full_key = self._full_key(storage_key)
            destination = Path(local_path)
            destination.parent.mkdir(parents=True, exist_ok=True)
            self.bucket.get_object_to_file(full_key, str(destination))
            logger.info(f"File downloaded from OSS: {full_key}")
        except self._oss2.exceptions.NoSuchKey:
            raise StorageError(operation="download", error=f"File not found in OSS: {storage_key}")
        except self._oss2.exceptions.OssError as e:
            logger.error(f"OSS error during download: {e}")
            raise StorageError(operation="download", error=str(e))
        except StorageError:
            raise
        except Exception as e:
            logger.error(f"Failed to download file from OSS: {e}")
            raise StorageError(operation="download", error=str(e))

    def download_bytes(self, storage_key: str) -> bytes:
        try:
            full_key = self._full_key(storage_key)
            result = self.bucket.get_object(full_key)
            content = result.read()
            logger.info(f"Bytes downloaded from OSS: {full_key}")
            return content
        except self._oss2.exceptions.NoSuchKey:
            raise StorageError(operation="download", error=f"File not found in OSS: {storage_key}")
        except self._oss2.exceptions.OssError as e:
            logger.error(f"OSS error during download_bytes: {e}")
            raise StorageError(operation="download", error=str(e))
        except StorageError:
            raise
        except Exception as e:
            logger.error(f"Failed to download bytes from OSS: {e}")
            raise StorageError(operation="download", error=str(e))

    def generate_presigned_url(self, storage_key: str, expires_in: int = 900) -> str:
        try:
            full_key = self._full_key(storage_key)
            expiry = expires_in if expires_in != 900 else self.presigned_url_expiry
            url = self.bucket.sign_url("GET", full_key, expiry)
            logger.info(f"Presigned URL generated for OSS: {full_key}")
            return url
        except self._oss2.exceptions.OssError as e:
            logger.error(f"OSS error during presigned URL generation: {e}")
            raise StorageError(operation="generate_presigned_url", error=str(e))
        except Exception as e:
            logger.error(f"Failed to generate presigned URL for OSS: {e}")
            raise StorageError(operation="generate_presigned_url", error=str(e))

    def delete(self, storage_key: str) -> None:
        try:
            full_key = self._full_key(storage_key)
            self.bucket.delete_object(full_key)
            logger.info(f"File deleted from OSS: {full_key}")
        except self._oss2.exceptions.OssError as e:
            logger.error(f"OSS error during delete: {e}")
            raise StorageError(operation="delete", error=str(e))
        except Exception as e:
            logger.error(f"Failed to delete file from OSS: {e}")
            raise StorageError(operation="delete", error=str(e))

    def exists(self, storage_key: str) -> bool:
        try:
            full_key = self._full_key(storage_key)
            return self.bucket.object_exists(full_key)
        except self._oss2.exceptions.OssError as e:
            logger.error(f"OSS error during exists check: {e}")
            return False
        except Exception as e:
            logger.error(f"Failed to check file existence in OSS: {e}")
            return False
