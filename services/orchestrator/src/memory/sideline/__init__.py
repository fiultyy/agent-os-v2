"""
Sideline Memory Agent - 旁路记忆系统

基于 D-25 三层记忆架构设计：
1. Baseline (Hermes): 明文 wiki 搜索
2. Sideline Memory: RAG + KG 混合检索
3. Sideline Verifier: recall 质量评分

作为 Main Agent 的旁路系统，独立评估和注入记忆。
"""

from .agent import SidelineMemoryAgent
from .baseline import HermesWikiBaseline
from .rag_engine import RAGEngine
from .verifier import RecallVerifier

__all__ = [
    "SidelineMemoryAgent",
    "HermesWikiBaseline",
    "RAGEngine",
    "RecallVerifier",
]