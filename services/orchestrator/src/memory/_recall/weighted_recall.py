"""WeightedRecall — Part 2 ⑦ 召回加权计算引擎（``match × lif``）。

设计依据：``docs/memory-kernel-design.md`` §5（召回是确定性计算引擎）+
``docs/memory-kernel-impl-plan.md`` Part 2 ⑦。

核心契约（审查修正，high）：
  1. ``lif_weight`` 用**相对排名分位**（rank-based，非绝对值），否则 lif 长期低电位
     时 ``score ≈ match × 0.1`` 被压制（复用
     :meth:`NeuralFieldEngine.lif_weight` 的 rank-based 分位实现）。
  2. ``score`` 对 **memory item** 计算（非"关系组 g"）：
     ``score = match_item(query, item) × lif_item(field, item.涉及概念)``。
     "关系组 g"概念废弃，仅用于扩散。

本模块是纯函数集合——不持有 LLM，不依赖 service/路由层，可被
:class:`RetrieverAgent`（Part 1 注入点）或 :class:`ButterflyRecallStrategy`
（Part 2 改造）直接复用。Part 2 阶段从 :class:`NeuralState` 读 ``field``。

分层边界（与 retriever_agent.py 一致）：本模块**不改 service.recall 内部**，
仅在拿到候选 memory items 后做确定性加权排序。
"""

from __future__ import annotations

import logging
from typing import Any, Callable, Protocol, runtime_checkable

from src.memory.types import MemoryItem

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────
# 协议——LIF 场的最小接口（NeuralState 天然满足，测试可注入鸭子类型）
# ─────────────────────────────────────────────────────────────────


@runtime_checkable
class _LifStateLike(Protocol):
    """lif_weight 需要的最小接口：``field: dict[concept→potential]``。"""

    field: dict[str, float]


# ─────────────────────────────────────────────────────────────────
# match_item / lif_item 纯函数
# ─────────────────────────────────────────────────────────────────


def query_tokens(query: str) -> list[str]:
    """与 KeywordRecall / RetrieverAgent 一致的 case-fold 分词。"""
    return [t for t in (query or "").lower().split() if t]


def match_item(
    item: MemoryItem,
    query_tokens: list[str] | None = None,
    query_lower: str | None = None,
) -> float:
    """``match_item(query, item)`` —— query 与 item 内容的子串命中率，``∈ [0, 1]``。

    审查修正（high）：score 对 **memory item** 算。这里复用 RetrieverAgent
    的 keyword-substring 命中策略（不直接 import RetrieverAgent 以避免循环依赖，
    RetrieverAgent 可选地把它替换为自身更丰富的 match_score —— 见
    :func:`rank_items` 的 ``match_fn`` 注入点）。

    Args:
        item: 待评分的 memory item。
        query_tokens: 预分词（可选；为 None 则从 query_lower 现分）。
        query_lower: 已小写的整句（可选；verbatim 全句命中加成）。

    Returns:
        ``[0.0, 1.0]`` 之间的匹配分。
    """
    if query_tokens is None:
        query_lower = (query_lower or "").strip().lower()
        query_tokens = [t for t in query_lower.split() if t]

    content_lower = (item.content or "").lower()

    if not query_tokens:
        return 0.0

    hits = sum(1 for tok in query_tokens if tok and tok in content_lower)
    match = hits / len(query_tokens)
    # verbatim 全句命中加成（最强信号）
    if query_lower and query_lower in content_lower:
        match = min(1.0, match + 0.2)
    return float(max(0.0, min(1.0, match)))


def item_concepts(item: MemoryItem, field: dict[str, float] | None = None) -> list[str]:
    """抽取 item "涉及概念"。

    Item 本身不显式携带概念清单（Part 1 设计），故采用与
    RetrieverAgent._aggregate_potential 一致的策略：取场中所有概念里
    出现在 item 内容中的那些。field 为空时返回空列表（lif 退化）。
    """
    if not field:
        return []
    content_lower = (item.content or "").lower()
    return [c for c in field if c and c.lower() in content_lower]


def lif_item(
    item: MemoryItem,
    field: dict[str, float] | None,
    lif_weight_fn: Callable[[list[str], dict[str, float]], float] | None = None,
) -> float:
    """``lif_item(field, item.涉及概念)`` —— item 概念在场中的相对排名分位，``∈ [0, 1]``。

    审查修正（high）：rank-based 分位（非绝对电位）。默认复用
    :meth:`NeuralFieldEngine.lif_weight`；调用方也可注入自定义 lif_weight
    纯函数（如直接调用 NeuralFieldEngine 实例的方法）。

    Args:
        item: 待评分的 memory item。
        field: 当前神经场电位（``NeuralState.field``）。None / 空 → 1.0
            （退化为纯 match 排序，等价 Part 1）。
        lif_weight_fn: 可选的 lif_weight 纯函数注入点。签名
            ``(item_concepts, field) -> float``。为 None 时实例化默认
            :class:`NeuralFieldEngine` 用其 ``lif_weight``。

    Returns:
        ``[0.0, 1.0]`` 之间的 lif 权重。field 缺失时返回 ``1.0``（不压制）。
    """
    if not field:
        return 1.0

    concepts = item_concepts(item, field)
    if lif_weight_fn is not None:
        try:
            return float(lif_weight_fn(concepts, field))
        except Exception:  # pragma: no cover - 防御
            logger.warning("lif_weight_fn raised; falling back to default", exc_info=True)

    # 默认走 NeuralFieldEngine.lif_weight（rank-based 分位）
    from src.memory.neural_field import NeuralFieldEngine  # 延迟 import 防循环

    return float(NeuralFieldEngine().lif_weight(concepts, field))


# ─────────────────────────────────────────────────────────────────
# rank_items —— 对一批 memory items 做 match×lif 加权排序
# ─────────────────────────────────────────────────────────────────


def rank_items(
    items: list[MemoryItem],
    query: str,
    field: dict[str, float] | None = None,
    *,
    match_fn: Callable[..., float] | None = None,
    lif_weight_fn: Callable[[list[str], dict[str, float]], float] | None = None,
    top_k: int | None = None,
) -> list[dict[str, Any]]:
    """对 memory items 计算 ``score = match_item × lif_item`` 并按 score 降序排序。

    程序化输出 ``[{"item": MemoryItem, "score": float, "match": float, "lif": float}, ...]``，
    解耦请求方（§5）：记忆系统只算 + 返回排序组合，请求方怎么消费是它的事。

    Args:
        items: 候选 memory items（通常来自 service.recall 的 kw+KG 双路径）。
        query: 召回 query 文本。
        field: Part 2 神经场电位（``NeuralState.field``）；None/空 → 纯 match 排序。
        match_fn: 可选的 match_item 注入（如 RetrieverAgent.match_score，带 KG 实体加成）。
        lif_weight_fn: 可选的 lif_weight 纯函数注入点。
        top_k: 截断长度；None 表示不截断。

    Returns:
        排序后的 ``[{item, score, match, lif}, ...]`` 列表。
    """
    if not items:
        return []

    q_lower = (query or "").strip().lower()
    q_tokens = query_tokens(q_lower)
    use_field = bool(field)

    results: list[dict[str, Any]] = []
    for item in items:
        if match_fn is not None:
            try:
                m = float(match_fn(item, q_tokens, q_lower))
            except Exception:  # match_fn 签名可能是 (item) → 兜底
                m = float(match_fn(item))  # type: ignore[call-arg]
        else:
            m = match_item(item, q_tokens, q_lower)

        if use_field:
            w = lif_item(item, field, lif_weight_fn)
        else:
            w = 1.0  # field 缺失：不压制，等价 Part 1

        results.append({
            "item": item,
            "match": m,
            "lif": w,
            "score": float(m * w),
        })

    results.sort(key=lambda r: r["score"], reverse=True)
    if top_k is not None:
        return results[: max(0, top_k)]
    return results


__all__ = [
    "query_tokens",
    "match_item",
    "item_concepts",
    "lif_item",
    "rank_items",
]
