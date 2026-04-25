"""Object storage backend abstraction layer.

Re-exports all public symbols from the split modules for backwards compatibility
with ``from core.storage import get_storage, generate_storage_key`` etc.
"""

from core.storage.protocol import StorageBackend
from core.storage.local import LocalStorageBackend
from core.storage.s3 import S3StorageBackend
from core.storage.oss import OSSStorageBackend
from core.storage.factory import (
    get_storage_backend,
    get_storage,
    generate_storage_key,
    get_storage_category_for_resource,
)

__all__ = [
    "StorageBackend",
    "LocalStorageBackend",
    "S3StorageBackend",
    "OSSStorageBackend",
    "get_storage_backend",
    "get_storage",
    "generate_storage_key",
    "get_storage_category_for_resource",
]
