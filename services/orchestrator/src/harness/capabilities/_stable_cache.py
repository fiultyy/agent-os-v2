"""Stable prefix hash 缓存(G1)。

pydantic-ai 每 ``.run()`` 重调 ``get_instructions``;Layer/Discipline capability
的 instructions 由不可变构造参数决定,可按 key_parts 哈希缓存,免每轮 f-string 重拼。

ponytail(ADR L24 否决 CacheStrategy 接口):进程内 dict + LRU 粗淘汰(next(iter) pop),
上限 32。不抽接口、不引依赖。upgrade path:若实测命中率低/需分 capa 隔离 → 再上 functools.lru_cache。
"""

from __future__ import annotations

import hashlib
import json
from typing import Any, Callable

# ponytail: 进程内全局 dict + 粗 LRU(上限 32,next(iter) pop 最旧)。
# 单进程多 capability 共享,免每 capa 各自维护。
_STABLE_CACHE_LIMIT = 32
_stable_cache: dict[str, str] = {}


def _cached(key_parts: tuple[Any, ...], build: Callable[[], str]) -> str:
    """按 key_parts sha256 缓存 build() 结果。

    key = sha256(json.dumps(key_parts, ensure_ascii=False, sort_keys=True))。
    命中→直接返;未命中→调 build()、写 cache、超上限淘汰最旧(next(iter))。
    """
    key = hashlib.sha256(
        json.dumps(key_parts, ensure_ascii=False, sort_keys=True).encode("utf-8")
    ).hexdigest()
    cached = _stable_cache.get(key)
    if cached is not None:
        return cached
    value = build()
    _stable_cache[key] = value
    # ponytail: 粗 LRU —— 超 32 直接 pop 最旧(next(iter)),不做访问序更新。
    # 进程内 stable prefix 数量本就接近 capa 数(十几),淘汰是兜底而非热路径。
    while len(_stable_cache) > _STABLE_CACHE_LIMIT:
        _stable_cache.pop(next(iter(_stable_cache)))
    return value
