"""
查询缓存 — Redis 实现（CACHE_BACKEND=redis）或空实现（CACHE_BACKEND=none）。
无 TTL：演示数据集静态，永久缓存可保证演示时相同查询秒级响应。
"""
import hashlib
import json
from typing import Any, Optional

from app.config import settings

_redis_client = None


async def _get_redis():
    global _redis_client
    if _redis_client is None:
        import redis.asyncio as aioredis
        _redis_client = await aioredis.from_url(
            f"redis://{settings.redis_host}:{settings.redis_port}",
            encoding="utf-8",
            decode_responses=True,
        )
    return _redis_client


def _cache_key(prefix: str, **kwargs) -> str:
    raw = json.dumps(kwargs, sort_keys=True, ensure_ascii=False)
    digest = hashlib.md5(raw.encode()).hexdigest()
    return f"ragent:{prefix}:{digest}"


async def cache_get(prefix: str, **kwargs) -> Optional[Any]:
    if settings.cache_backend != "redis":
        return None
    try:
        r = await _get_redis()
        val = await r.get(_cache_key(prefix, **kwargs))
        return json.loads(val) if val else None
    except Exception as e:
        print(f"[cache] GET 失败（降级）: {e}", flush=True)
        return None


async def cache_set(prefix: str, value: Any, **kwargs) -> None:
    if settings.cache_backend != "redis":
        return
    try:
        r = await _get_redis()
        await r.set(_cache_key(prefix, **kwargs), json.dumps(value, ensure_ascii=False))
    except Exception as e:
        print(f"[cache] SET 失败（忽略）: {e}", flush=True)
