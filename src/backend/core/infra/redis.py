"""Redis client singleton for session storage."""

from typing import Optional

import redis.asyncio as aioredis

from core.config.settings import settings
from core.infra.logging import get_logger

logger = get_logger(__name__)

_redis_pool: Optional[aioredis.Redis] = None


def get_redis() -> aioredis.Redis:
    """Get or create the shared async Redis connection pool."""
    global _redis_pool
    if _redis_pool is None:
        url = settings.redis.url
        _redis_pool = aioredis.from_url(
            url,
            decode_responses=True,
            max_connections=20,
        )
        logger.info("redis_pool_created", url=url.split("@")[-1])  # hide password
    return _redis_pool


async def close_redis() -> None:
    """Gracefully close the Redis connection pool (call on shutdown)."""
    global _redis_pool
    if _redis_pool is not None:
        await _redis_pool.aclose()
        _redis_pool = None
        logger.info("redis_pool_closed")
