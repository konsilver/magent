#!/usr/bin/env python3
"""
阿里云 OSS 存储后端真实连通性测试。

此脚本使用 .env 中配置的 OSS 凭证对实际 Bucket 进行上传、下载、
生成预签名 URL、删除等操作，验证 OSSStorageBackend 完整可用。

运行方式：
    python selftests/oss_live_test.py
"""

import os
import sys
import tempfile
import time
from pathlib import Path

# 加载 .env
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

try:
    from dotenv import load_dotenv
    load_dotenv(os.path.join(os.path.dirname(__file__), "..", ".env"))
except ImportError:
    # 手动读取 .env（兼容没有 python-dotenv 的环境）
    env_path = os.path.join(os.path.dirname(__file__), "..", ".env")
    if os.path.exists(env_path):
        with open(env_path) as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    key, _, val = line.partition("=")
                    val = val.strip().strip('"').strip("'")
                    os.environ.setdefault(key.strip(), val)

# 强制使用 oss
os.environ["STORAGE_TYPE"] = "oss"

from core.storage import OSSStorageBackend, get_storage_backend, generate_storage_key
from core.infra.exceptions import StorageError


def banner(title: str) -> None:
    print(f"\n{'=' * 60}")
    print(f"  {title}")
    print('=' * 60)


def ok(msg: str) -> None:
    print(f"  ✓ {msg}")


def fail(msg: str) -> None:
    print(f"  ✗ {msg}")
    sys.exit(1)


def main() -> int:
    banner("阿里云 OSS 存储后端 — 实际连通性测试")

    # ── 初始化 ──────────────────────────────────────────────────
    print("\n[1] 初始化 OSSStorageBackend ...")
    try:
        storage: OSSStorageBackend = get_storage_backend()  # type: ignore
        assert isinstance(storage, OSSStorageBackend), "返回类型不是 OSSStorageBackend"
        ok(f"初始化成功 — bucket={storage.bucket_name}, prefix={storage.key_prefix!r}")
    except Exception as e:
        fail(f"初始化失败: {e}")

    # ── upload_bytes ─────────────────────────────────────────────
    print("\n[2] upload_bytes ...")
    test_content = b"Jingxin OSS live test - " + str(time.time()).encode()
    storage_key = generate_storage_key(
        env="test",
        user_id="oss_live_test",
        category="uploads",
        filename="test_hello.txt",
    )
    try:
        url = storage.upload_bytes(test_content, storage_key)
        ok(f"上传成功: {url}")
    except Exception as e:
        fail(f"upload_bytes 失败: {e}")

    # ── exists ───────────────────────────────────────────────────
    print("\n[3] exists ...")
    try:
        assert storage.exists(storage_key), "exists() 应返回 True"
        ok("exists() 返回 True")
    except AssertionError as e:
        fail(str(e))
    except Exception as e:
        fail(f"exists() 异常: {e}")

    # ── download_bytes ───────────────────────────────────────────
    print("\n[4] download_bytes ...")
    try:
        downloaded = storage.download_bytes(storage_key)
        assert downloaded == test_content, (
            f"内容不匹配: expected={test_content!r}, got={downloaded!r}"
        )
        ok("内容一致，download_bytes 成功")
    except AssertionError as e:
        fail(str(e))
    except Exception as e:
        fail(f"download_bytes 失败: {e}")

    # ── upload (from file) ───────────────────────────────────────
    print("\n[5] upload（从文件） ...")
    file_key = generate_storage_key(
        env="test",
        user_id="oss_live_test",
        category="uploads",
        filename="test_from_file.txt",
    )
    with tempfile.NamedTemporaryFile(delete=False, suffix=".txt", mode="wb") as tmp:
        tmp.write(b"OSS file upload test content")
        tmp_path = tmp.name
    try:
        url2 = storage.upload(tmp_path, file_key)
        ok(f"文件上传成功: {url2}")
    except Exception as e:
        fail(f"upload 失败: {e}")
    finally:
        os.unlink(tmp_path)

    # ── download (to file) ───────────────────────────────────────
    print("\n[6] download（下载到文件） ...")
    with tempfile.NamedTemporaryFile(delete=False, suffix=".txt") as tmp2:
        dest_path = tmp2.name
    try:
        storage.download(file_key, dest_path)
        with open(dest_path, "rb") as f:
            content = f.read()
        assert content == b"OSS file upload test content", f"内容不匹配: {content!r}"
        ok("文件下载内容一致")
    except AssertionError as e:
        fail(str(e))
    except Exception as e:
        fail(f"download 失败: {e}")
    finally:
        if os.path.exists(dest_path):
            os.unlink(dest_path)

    # ── generate_presigned_url ───────────────────────────────────
    print("\n[7] generate_presigned_url ...")
    try:
        presigned = storage.generate_presigned_url(storage_key, expires_in=300)
        assert presigned.startswith("http"), f"预签名 URL 格式异常: {presigned}"
        ok(f"预签名 URL 生成成功（前 100 字符）:\n     {presigned[:100]}...")
    except Exception as e:
        fail(f"generate_presigned_url 失败: {e}")

    # ── delete ───────────────────────────────────────────────────
    print("\n[8] delete ...")
    try:
        storage.delete(storage_key)
        assert not storage.exists(storage_key), "删除后 exists() 应返回 False"
        storage.delete(file_key)
        assert not storage.exists(file_key), "删除后 exists() 应返回 False"
        ok("两个测试对象均已删除")
    except AssertionError as e:
        fail(str(e))
    except Exception as e:
        fail(f"delete 失败: {e}")

    # ── 异常处理：访问不存在的 key ───────────────────────────────
    print("\n[9] 下载不存在的 key（预期 StorageError）...")
    try:
        storage.download_bytes("test/nonexistent/ghost_file_xyz.txt")
        fail("应抛出 StorageError 但未抛出")
    except StorageError:
        ok("StorageError 正确抛出")
    except Exception as e:
        fail(f"抛出了意外异常类型: {type(e).__name__}: {e}")

    banner("全部测试通过 ✓")
    return 0


if __name__ == "__main__":
    sys.exit(main())
