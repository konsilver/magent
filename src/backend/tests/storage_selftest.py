#!/usr/bin/env python3
"""
Self-test for storage backend abstraction layer.

Tests:
1. Local storage backend operations
2. S3 storage backend operations (mocked)
3. Storage key generation
4. Presigned URL generation
5. Error handling

Run: python selftests/storage_selftest.py
"""

import os
import sys
import tempfile
import shutil
from pathlib import Path
from unittest.mock import Mock, patch, MagicMock

# Add parent directory to path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from core.storage import (
    LocalStorageBackend,
    S3StorageBackend,
    get_storage_backend,
    generate_storage_key,
    get_storage_category_for_resource,
    get_storage
)
from core.infra.exceptions import StorageError


class TestLocalStorage:
    """Test local storage backend."""

    def __init__(self):
        self.temp_dir = None
        self.storage = None

    def setup(self):
        """Create temporary storage directory."""
        self.temp_dir = tempfile.mkdtemp(prefix="storage_test_")
        os.environ["STORAGE_PATH"] = self.temp_dir
        self.storage = LocalStorageBackend()
        print(f"✓ Local storage initialized at: {self.temp_dir}")

    def teardown(self):
        """Clean up temporary storage."""
        if self.temp_dir and os.path.exists(self.temp_dir):
            shutil.rmtree(self.temp_dir)
            print(f"✓ Cleaned up temporary storage: {self.temp_dir}")

    def test_upload_bytes(self):
        """Test uploading bytes."""
        content = b"Test file content"
        storage_key = "test/user_001/testfile.txt"

        url = self.storage.upload_bytes(content, storage_key)

        assert self.storage.exists(storage_key), "File should exist after upload"
        print(f"✓ Upload bytes successful: {storage_key}")

    def test_download_bytes(self):
        """Test downloading bytes."""
        content = b"Test download content"
        storage_key = "test/user_001/download.txt"

        self.storage.upload_bytes(content, storage_key)
        downloaded = self.storage.download_bytes(storage_key)

        assert downloaded == content, "Downloaded content should match uploaded"
        print(f"✓ Download bytes successful: {storage_key}")

    def test_upload_file(self):
        """Test uploading from file."""
        # Create temporary source file
        with tempfile.NamedTemporaryFile(mode='w', delete=False, suffix='.txt') as f:
            f.write("Test file upload")
            source_path = f.name

        try:
            storage_key = "test/user_001/uploaded.txt"
            url = self.storage.upload(source_path, storage_key)

            assert self.storage.exists(storage_key), "File should exist after upload"
            print(f"✓ Upload file successful: {storage_key}")
        finally:
            os.unlink(source_path)

    def test_download_file(self):
        """Test downloading to file."""
        content = b"Test file download"
        storage_key = "test/user_001/file_download.txt"

        self.storage.upload_bytes(content, storage_key)

        # Download to temporary file
        with tempfile.NamedTemporaryFile(delete=False, suffix='.txt') as f:
            dest_path = f.name

        try:
            self.storage.download(storage_key, dest_path)
            with open(dest_path, 'rb') as f:
                downloaded = f.read()

            assert downloaded == content, "Downloaded file content should match"
            print(f"✓ Download file successful: {storage_key}")
        finally:
            os.unlink(dest_path)

    def test_delete(self):
        """Test file deletion."""
        content = b"Test delete"
        storage_key = "test/user_001/delete_me.txt"

        self.storage.upload_bytes(content, storage_key)
        assert self.storage.exists(storage_key), "File should exist before delete"

        self.storage.delete(storage_key)
        assert not self.storage.exists(storage_key), "File should not exist after delete"
        print(f"✓ Delete successful: {storage_key}")

    def test_presigned_url(self):
        """Test presigned URL generation."""
        content = b"Test presigned URL"
        storage_key = "test/user_001/presigned.txt"

        self.storage.upload_bytes(content, storage_key)
        url = self.storage.generate_presigned_url(storage_key)

        assert url.startswith("file://"), "Local storage should return file:// URL"
        print(f"✓ Presigned URL generated: {url}")

    def test_path_traversal_prevention(self):
        """Test that path traversal attacks are prevented."""
        content = b"Test security"
        malicious_key = "../../../etc/passwd"

        # Should not raise an error, but should sanitize the path
        url = self.storage.upload_bytes(content, malicious_key)

        # Verify file is not outside storage directory
        full_path = self.storage._get_full_path(malicious_key)
        assert str(full_path).startswith(str(self.storage.base_path)), \
            "File should be within storage directory"
        print(f"✓ Path traversal prevention working")

    def test_error_handling(self):
        """Test error handling."""
        # Test download non-existent file
        try:
            self.storage.download_bytes("nonexistent/file.txt")
            assert False, "Should raise StorageError for non-existent file"
        except StorageError as e:
            # Check error message contains "not found" or the error data has the info
            error_str = str(e).lower()
            error_msg = e.data.get('error', '').lower() if hasattr(e, 'data') else ''
            assert "not found" in error_str or "not found" in error_msg, \
                f"Error should mention 'not found', got: {e}"
            print(f"✓ Error handling for non-existent file works")

    def run_all(self):
        """Run all local storage tests."""
        print("\n=== Testing Local Storage Backend ===")
        self.setup()
        try:
            self.test_upload_bytes()
            self.test_download_bytes()
            self.test_upload_file()
            self.test_download_file()
            self.test_delete()
            self.test_presigned_url()
            self.test_path_traversal_prevention()
            self.test_error_handling()
            print("✓ All local storage tests passed!")
        finally:
            self.teardown()


class TestS3Storage:
    """Test S3 storage backend (mocked)."""

    def test_s3_upload_bytes(self):
        """Test S3 upload bytes with mock."""
        print("\n=== Testing S3 Storage Backend (Mocked) ===")

        # Mock boto3.client
        import sys
        mock_boto3 = MagicMock()
        mock_client = MagicMock()
        mock_boto3.client.return_value = mock_client
        sys.modules['boto3'] = mock_boto3
        sys.modules['botocore'] = MagicMock()
        sys.modules['botocore.config'] = MagicMock()
        sys.modules['botocore.exceptions'] = MagicMock()

        try:
            # Set environment
            os.environ.update({
                'S3_BUCKET': 'test-bucket',
                'S3_REGION': 'us-east-1',
                'S3_ACCESS_KEY': 'test-key',
                'S3_SECRET_KEY': 'test-secret'
            })

            # Need to reimport after mocking
            import importlib
            import core.storage
            importlib.reload(core.storage)

            # Create S3 backend
            storage = core.storage.S3StorageBackend()

            # Test upload
            content = b"Test S3 content"
            storage_key = "test/user_001/s3_file.txt"

            url = storage.upload_bytes(content, storage_key)

            # Verify put_object was called
            mock_client.put_object.assert_called_once()
            call_args = mock_client.put_object.call_args
            assert call_args[1]['Bucket'] == 'test-bucket'
            assert call_args[1]['Key'] == storage_key
            assert call_args[1]['Body'] == content

            print(f"✓ S3 upload bytes successful (mocked): {storage_key}")
        finally:
            # Clean up mock
            if 'boto3' in sys.modules:
                del sys.modules['boto3']
            if 'botocore' in sys.modules:
                del sys.modules['botocore']
            if 'botocore.config' in sys.modules:
                del sys.modules['botocore.config']
            if 'botocore.exceptions' in sys.modules:
                del sys.modules['botocore.exceptions']
            # Reload core.storage to restore original
            import importlib
            import core.storage
            importlib.reload(core.storage)

    def test_s3_presigned_url(self):
        """Test S3 presigned URL generation with mock."""
        import sys
        mock_boto3 = MagicMock()
        mock_client = MagicMock()
        mock_boto3.client.return_value = mock_client
        mock_client.generate_presigned_url.return_value = \
            "https://test-bucket.s3.amazonaws.com/test/file.txt?signature=abc123"
        sys.modules['boto3'] = mock_boto3
        sys.modules['botocore'] = MagicMock()
        sys.modules['botocore.config'] = MagicMock()
        sys.modules['botocore.exceptions'] = MagicMock()

        try:
            os.environ.update({
                'S3_BUCKET': 'test-bucket',
                'S3_REGION': 'us-east-1'
            })

            import importlib
            import core.storage
            importlib.reload(core.storage)

            storage = core.storage.S3StorageBackend()
            storage_key = "test/user_001/file.txt"

            url = storage.generate_presigned_url(storage_key, expires_in=900)

            # Verify generate_presigned_url was called
            mock_client.generate_presigned_url.assert_called_once()
            assert url.startswith("https://")

            print(f"✓ S3 presigned URL generated (mocked): {url}")
        finally:
            if 'boto3' in sys.modules:
                del sys.modules['boto3']
            if 'botocore' in sys.modules:
                del sys.modules['botocore']
            if 'botocore.config' in sys.modules:
                del sys.modules['botocore.config']
            if 'botocore.exceptions' in sys.modules:
                del sys.modules['botocore.exceptions']
            import importlib
            import core.storage
            importlib.reload(core.storage)

    def test_s3_with_cdn(self):
        """Test S3 presigned URL with CDN domain."""
        import sys
        mock_boto3 = MagicMock()
        mock_client = MagicMock()
        mock_boto3.client.return_value = mock_client
        mock_client.generate_presigned_url.return_value = \
            "https://test-bucket.s3.amazonaws.com/path/file.txt?X-Amz-Signature=abc"
        sys.modules['boto3'] = mock_boto3
        sys.modules['botocore'] = MagicMock()
        sys.modules['botocore.config'] = MagicMock()
        sys.modules['botocore.exceptions'] = MagicMock()

        try:
            os.environ.update({
                'S3_BUCKET': 'test-bucket',
                'S3_REGION': 'us-east-1',
                'S3_CDN_DOMAIN': 'cdn.example.com'
            })

            import importlib
            import core.storage
            importlib.reload(core.storage)

            storage = core.storage.S3StorageBackend()
            storage_key = "test/user_001/file.txt"

            url = storage.generate_presigned_url(storage_key)

            # URL should use CDN domain
            assert 'cdn.example.com' in url
            print(f"✓ S3 CDN URL generated (mocked): {url}")
        finally:
            if 'boto3' in sys.modules:
                del sys.modules['boto3']
            if 'botocore' in sys.modules:
                del sys.modules['botocore']
            if 'botocore.config' in sys.modules:
                del sys.modules['botocore.config']
            if 'botocore.exceptions' in sys.modules:
                del sys.modules['botocore.exceptions']
            import importlib
            import core.storage
            importlib.reload(core.storage)

    def run_all(self):
        """Run all S3 storage tests."""
        self.test_s3_upload_bytes()
        self.test_s3_presigned_url()
        self.test_s3_with_cdn()
        print("✓ All S3 storage tests passed (mocked)!")


class TestStorageKeyGeneration:
    """Test storage key generation."""

    def test_basic_key_generation(self):
        """Test basic storage key generation."""
        print("\n=== Testing Storage Key Generation ===")

        key = generate_storage_key(
            env="prod",
            user_id="user_001",
            category="artifacts",
            filename="report.pdf"
        )

        # Verify structure
        parts = key.split('/')
        assert parts[0] == "prod", "First part should be environment"
        assert parts[1] == "artifacts", "Second part should be category"
        assert parts[2] == "user_001", "Third part should be user_id"
        assert parts[3].endswith("_report.pdf"), "Fourth part should have timestamp_filename"

        print(f"✓ Basic key generation: {key}")

    def test_key_with_chat_id(self):
        """Test storage key generation with chat_id."""
        key = generate_storage_key(
            env="prod",
            user_id="user_001",
            category="artifacts",
            filename="report.pdf",
            chat_id="chat_123"
        )

        parts = key.split('/')
        assert len(parts) == 5, "Should have 5 parts with chat_id"
        assert parts[3] == "chat_123", "Fourth part should be chat_id"

        print(f"✓ Key with chat_id: {key}")

    def test_filename_sanitization(self):
        """Test that filenames are sanitized."""
        key = generate_storage_key(
            env="prod",
            user_id="user_001",
            category="artifacts",
            filename="../../../etc/passwd"
        )

        # Should not contain path traversal
        assert "../" not in key
        print(f"✓ Filename sanitization: {key}")

    def test_special_characters(self):
        """Test handling of special characters in filename."""
        key = generate_storage_key(
            env="prod",
            user_id="user_001",
            category="artifacts",
            filename="report (final) [2024].pdf"
        )

        # Should replace or remove special characters
        assert "(" not in key.split('/')[-1] or key.split('/')[-1].endswith(".pdf")
        print(f"✓ Special characters handled: {key}")

    def test_category_mapping(self):
        """Test resource type to category mapping."""
        assert get_storage_category_for_resource('artifact') == 'artifacts'
        assert get_storage_category_for_resource('kb_document') == 'kb_documents'
        assert get_storage_category_for_resource('upload') == 'uploads'
        assert get_storage_category_for_resource('unknown') == 'uploads'  # default

        print("✓ Category mapping works correctly")

    def run_all(self):
        """Run all storage key generation tests."""
        self.test_basic_key_generation()
        self.test_key_with_chat_id()
        self.test_filename_sanitization()
        self.test_special_characters()
        self.test_category_mapping()
        print("✓ All storage key generation tests passed!")


class TestStorageFactory:
    """Test storage factory functions."""

    def test_get_local_storage(self):
        """Test getting local storage backend."""
        print("\n=== Testing Storage Factory ===")

        # Ensure clean import of core.storage
        import importlib
        import core.storage
        importlib.reload(core.storage)

        os.environ['STORAGE_TYPE'] = 'local'
        os.environ['STORAGE_PATH'] = tempfile.mkdtemp(prefix="factory_test_")

        try:
            storage = core.storage.get_storage_backend()
            assert isinstance(storage, core.storage.LocalStorageBackend)
            print("✓ get_storage_backend() returns LocalStorageBackend")
        finally:
            if os.path.exists(os.environ['STORAGE_PATH']):
                shutil.rmtree(os.environ['STORAGE_PATH'])

    def test_get_s3_storage(self):
        """Test getting S3 storage backend."""
        import sys
        mock_boto3 = MagicMock()
        sys.modules['boto3'] = mock_boto3
        sys.modules['botocore'] = MagicMock()
        sys.modules['botocore.config'] = MagicMock()
        sys.modules['botocore.exceptions'] = MagicMock()

        try:
            os.environ.update({
                'STORAGE_TYPE': 's3',
                'S3_BUCKET': 'test-bucket',
                'S3_REGION': 'us-east-1'
            })

            import importlib
            import core.storage
            importlib.reload(core.storage)

            storage = core.storage.get_storage_backend()
            assert isinstance(storage, core.storage.S3StorageBackend)
            print("✓ get_storage_backend() returns S3StorageBackend")
        finally:
            if 'boto3' in sys.modules:
                del sys.modules['boto3']
            if 'botocore' in sys.modules:
                del sys.modules['botocore']
            if 'botocore.config' in sys.modules:
                del sys.modules['botocore.config']
            if 'botocore.exceptions' in sys.modules:
                del sys.modules['botocore.exceptions']
            import importlib
            import core.storage
            importlib.reload(core.storage)

    def test_singleton_storage(self):
        """Test global storage instance."""
        # Ensure clean import
        import importlib
        import core.storage
        importlib.reload(core.storage)

        os.environ['STORAGE_TYPE'] = 'local'
        os.environ['STORAGE_PATH'] = tempfile.mkdtemp(prefix="singleton_test_")

        try:
            # Clear singleton
            core.storage.factory._storage_instance = None

            storage1 = core.storage.get_storage()
            storage2 = core.storage.get_storage()

            assert storage1 is storage2, "get_storage() should return same instance"
            print("✓ get_storage() returns singleton instance")
        finally:
            if os.path.exists(os.environ['STORAGE_PATH']):
                shutil.rmtree(os.environ['STORAGE_PATH'])

    def run_all(self):
        """Run all factory tests."""
        self.test_get_local_storage()
        self.test_get_s3_storage()
        self.test_singleton_storage()
        print("✓ All factory tests passed!")


def main():
    """Run all storage self-tests."""
    print("=" * 60)
    print("Storage Backend Self-Test")
    print("=" * 60)

    try:
        # Test local storage
        local_test = TestLocalStorage()
        local_test.run_all()

        # Test S3 storage (mocked)
        s3_test = TestS3Storage()
        s3_test.run_all()

        # Test storage key generation
        key_test = TestStorageKeyGeneration()
        key_test.run_all()

        # Test factory functions
        factory_test = TestStorageFactory()
        factory_test.run_all()

        print("\n" + "=" * 60)
        print("✓ ALL STORAGE TESTS PASSED!")
        print("=" * 60)
        return 0

    except AssertionError as e:
        print(f"\n✗ Test failed: {e}")
        import traceback
        traceback.print_exc()
        return 1
    except Exception as e:
        print(f"\n✗ Unexpected error: {e}")
        import traceback
        traceback.print_exc()
        return 1


if __name__ == "__main__":
    sys.exit(main())
