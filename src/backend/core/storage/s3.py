"""S3-compatible object storage backend for production."""

import logging
import os
from pathlib import Path

from core.infra.exceptions import StorageError
from core.storage.protocol import StorageBackend

logger = logging.getLogger(__name__)


class S3StorageBackend(StorageBackend):
    """S3-compatible object storage backend for production."""

    def __init__(self):
        try:
            import boto3
            from botocore.config import Config
            from botocore.exceptions import ClientError

            self.ClientError = ClientError

            self.bucket = os.getenv("S3_BUCKET")
            if not self.bucket:
                raise ValueError("S3_BUCKET environment variable is required")

            endpoint_url = os.getenv("S3_ENDPOINT")
            region_name = os.getenv("S3_REGION", "us-east-1")
            access_key = os.getenv("S3_ACCESS_KEY")
            secret_key = os.getenv("S3_SECRET_KEY")

            self.cdn_domain = os.getenv("S3_CDN_DOMAIN")
            self.presigned_url_expiry = int(os.getenv("S3_PRESIGNED_URL_EXPIRY", "900"))

            config = Config(signature_version='s3v4', retries={'max_attempts': 3, 'mode': 'standard'})

            session_kwargs = {'region_name': region_name, 'config': config}
            if endpoint_url:
                session_kwargs['endpoint_url'] = endpoint_url
            if access_key and secret_key:
                session_kwargs['aws_access_key_id'] = access_key
                session_kwargs['aws_secret_access_key'] = secret_key

            self.s3_client = boto3.client('s3', **session_kwargs)
            logger.info(f"S3StorageBackend initialized with bucket: {self.bucket}")

        except ImportError:
            raise ImportError("boto3 is required for S3StorageBackend. Install with: pip install boto3")
        except Exception as e:
            logger.error(f"Failed to initialize S3 storage backend: {e}")
            raise StorageError(operation="init", error=str(e))

    def upload(self, file_path: str, storage_key: str) -> str:
        try:
            source = Path(file_path)
            if not source.exists():
                raise StorageError(operation="upload", error=f"Source file not found: {file_path}")
            self.s3_client.upload_file(str(source), self.bucket, storage_key)
            logger.info(f"File uploaded to S3: {storage_key}")
            return f"s3://{self.bucket}/{storage_key}"
        except self.ClientError as e:
            logger.error(f"S3 ClientError during upload: {e}")
            raise StorageError(operation="upload", error=str(e))
        except Exception as e:
            logger.error(f"Failed to upload file to S3: {e}")
            raise StorageError(operation="upload", error=str(e))

    def upload_bytes(self, content: bytes, storage_key: str) -> str:
        try:
            self.s3_client.put_object(Bucket=self.bucket, Key=storage_key, Body=content)
            logger.info(f"Bytes uploaded to S3: {storage_key}")
            return f"s3://{self.bucket}/{storage_key}"
        except self.ClientError as e:
            logger.error(f"S3 ClientError during upload: {e}")
            raise StorageError(operation="upload", error=str(e))
        except Exception as e:
            logger.error(f"Failed to upload bytes to S3: {e}")
            raise StorageError(operation="upload", error=str(e))

    def download(self, storage_key: str, local_path: str) -> None:
        try:
            destination = Path(local_path)
            destination.parent.mkdir(parents=True, exist_ok=True)
            self.s3_client.download_file(self.bucket, storage_key, str(destination))
            logger.info(f"File downloaded from S3: {storage_key}")
        except self.ClientError as e:
            if e.response['Error']['Code'] == '404':
                raise StorageError(operation="download", error=f"File not found in S3: {storage_key}")
            logger.error(f"S3 ClientError during download: {e}")
            raise StorageError(operation="download", error=str(e))
        except Exception as e:
            logger.error(f"Failed to download file from S3: {e}")
            raise StorageError(operation="download", error=str(e))

    def download_bytes(self, storage_key: str) -> bytes:
        try:
            response = self.s3_client.get_object(Bucket=self.bucket, Key=storage_key)
            content = response['Body'].read()
            logger.info(f"Bytes downloaded from S3: {storage_key}")
            return content
        except self.ClientError as e:
            if e.response['Error']['Code'] == 'NoSuchKey':
                raise StorageError(operation="download", error=f"File not found in S3: {storage_key}")
            logger.error(f"S3 ClientError during download: {e}")
            raise StorageError(operation="download", error=str(e))
        except Exception as e:
            logger.error(f"Failed to download bytes from S3: {e}")
            raise StorageError(operation="download", error=str(e))

    def generate_presigned_url(self, storage_key: str, expires_in: int = 900) -> str:
        try:
            expiry = expires_in if expires_in != 900 else self.presigned_url_expiry
            url = self.s3_client.generate_presigned_url(
                'get_object', Params={'Bucket': self.bucket, 'Key': storage_key}, ExpiresIn=expiry,
            )
            if self.cdn_domain:
                import urllib.parse
                parsed = urllib.parse.urlparse(url)
                url = f"https://{self.cdn_domain}{parsed.path}?{parsed.query}"
            logger.info(f"Presigned URL generated for: {storage_key}")
            return url
        except self.ClientError as e:
            logger.error(f"S3 ClientError during presigned URL generation: {e}")
            raise StorageError(operation="generate_presigned_url", error=str(e))
        except Exception as e:
            logger.error(f"Failed to generate presigned URL: {e}")
            raise StorageError(operation="generate_presigned_url", error=str(e))

    def delete(self, storage_key: str) -> None:
        try:
            self.s3_client.delete_object(Bucket=self.bucket, Key=storage_key)
            logger.info(f"File deleted from S3: {storage_key}")
        except self.ClientError as e:
            logger.error(f"S3 ClientError during delete: {e}")
            raise StorageError(operation="delete", error=str(e))
        except Exception as e:
            logger.error(f"Failed to delete file from S3: {e}")
            raise StorageError(operation="delete", error=str(e))

    def exists(self, storage_key: str) -> bool:
        try:
            self.s3_client.head_object(Bucket=self.bucket, Key=storage_key)
            return True
        except self.ClientError as e:
            if e.response['Error']['Code'] == '404':
                return False
            logger.error(f"S3 ClientError during exists check: {e}")
            return False
        except Exception as e:
            logger.error(f"Failed to check file existence in S3: {e}")
            return False
