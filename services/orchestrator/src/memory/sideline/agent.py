# ARCHIVED — side-agent parallel mechanism (pre-AO2),不挂入系统,待用 AO2 capability 重接。
"""
SidelineMemoryAgent - 旁路记忆系统主控

作为 Main Agent 的旁路系统，独立评估和注入记忆。
不依赖 LLM 遵循 prompt，而是通过结构化的 MemoryInput/MemoryDecision 进行通信。
"""

from typing import Dict, Any, List, Optional
from dataclasses import dataclass
from enum import Enum
from .baseline import HermesWikiBaseline
from .rag_engine import RAGEngine
from .verifier import RecallVerifier


class RecallMode(Enum):
    """召回模式"""
    KEYWORD = "keyword"      # 关键字召回（Baseline）
    SEMANTIC = "semantic"    # 语义召回（RAG）
    KG = "kg"                # 知识图谱召回
    HYBRID = "hybrid"        # 混合召回


@dataclass
class MemoryInput:
    """Sideline Memory 输入（来自 Main Agent 的结构化元数据）"""
    session_id: str
    agent_id: str
    query: str
    context_summary: str  # 当前上下文摘要
    recent_memories: List[Dict[str, Any]]  # 最近使用的记忆
    turn_count: int  # 当前轮次
    overflow_detected: bool = False  # 是否检测到溢出


@dataclass
class MemoryDecision:
    """Sideline Memory 决策输出"""
    inject: bool  # 是否需要注入
    memories: List[Dict[str, Any]]  # 要注入的记忆
    confidence: float  # 置信度 (0-1)
    mode: RecallMode  # 使用的召回模式
    reason: str  # 决策原因


class SidelineMemoryAgent:
    """
    旁路记忆系统主控
    
    设计原则：
    - 不依赖 LLM 遵循 prompt（借鉴 Hermes 教训）
    - 使用结构化输入/输出进行通信
    - Lightweight Classifier（非 LLM）做快速评估
    - LLM 仅在需要语义理解时调用（semantic recall）
    """

    def __init__(self, config: Optional[Dict[str, Any]] = None):
        self.config = config or {}
        self.baseline = HermesWikiBaseline(self.config)
        self.rag_engine = RAGEngine(self.config)
        self.verifier = RecallVerifier(self.config)
        
    def evaluate(self, memory_input: MemoryInput) -> MemoryDecision:
        """
        评估是否需要注入记忆
        
        Args:
            memory_input: 结构化输入
        
        Returns:
            MemoryDecision: 是否注入 + 注入内容
        """
        # 1. 快速检查：最近是否已使用相关记忆
        if self._is_recent(memory_input):
            return MemoryDecision(
                inject=False,
                memories=[],
                confidence=1.0,
                mode=RecallMode.KEYWORD,
                reason="recent_memory_hit"
            )
        
        # 2. Baseline 搜索（Hermes 明文 wiki）
        baseline_results = self.baseline.search(memory_input.query)
        
        # 3. 如果溢出或需要更深度检索，使用 RAG
        if memory_input.overflow_detected or len(baseline_results) < 3:
            semantic_results = self.rag_engine.search(
                query=memory_input.query,
                context=memory_input.context_summary,
                limit=5
            )
        else:
            semantic_results = []
        
        # 4. Lightweight Classifier 决定是否注入
        decision = self._classify(
            memory_input=memory_input,
            baseline_results=baseline_results,
            semantic_results=semantic_results
        )
        
        # 5. Verifier 评分（仅在需要时）
        if decision.inject:
            decision.confidence = self.verifier.score(
                decision.memories,
                memory_input.context_summary
            )
        
        return decision
    
    def _is_recent(self, memory_input: MemoryInput) -> bool:
        """检查最近是否已使用相关记忆"""
        for mem in memory_input.recent_memories:
            if self._similar_query(mem.get('query', ''), memory_input.query):
                return True
        return False
    
    def _similar_query(self, q1: str, q2: str) -> bool:
        """Jaccard 相似度匹配（替代子串匹配）"""
        words1 = set(q1.lower().split())
        words2 = set(q2.lower().split())
        if not words1 or not words2:
            return False
        intersection = words1 & words2
        union = words1 | words2
        jaccard = len(intersection) / len(union) if union else 0
        return jaccard > 0.4  # Jaccard 阈值 0.4
    
    def _classify(
        self,
        memory_input: MemoryInput,
        baseline_results: List[Dict],
        semantic_results: List[Dict]
    ) -> MemoryDecision:
        """
        Lightweight Classifier（轻量分类器）
        
        规则：
        - baseline 有结果 → 不注入（Baseline 足够）
        - semantic 有高置信结果 → 注入
        - 两者都没有 → 不注入
        """
        # 合并结果
        all_results = baseline_results + semantic_results
        
        if not all_results:
            return MemoryDecision(
                inject=False,
                memories=[],
                confidence=0.0,
                mode=RecallMode.KEYWORD,
                reason="no_results"
            )
        
        # 选择召回模式
        if semantic_results and len(semantic_results) > len(baseline_results):
            mode = RecallMode.SEMANTIC
        else:
            mode = RecallMode.KEYWORD
        
        # 简单评分：结果数量 + 基础相关性
        score = min(1.0, len(all_results) / 5.0)
        
        return MemoryDecision(
            inject=score > 0.3,  # 阈值可调
            memories=all_results[:5],
            confidence=score,
            mode=mode,
            reason=f"found_{len(all_results)}_results"
        )
    
    def write(self, memory_item: Dict[str, Any]) -> bool:
        """
        写入记忆（Main Agent 调用）
        
        实现 Backward 流程：
        1. 写入 wiki 明文
        2. 同步 file_graph
        3. 更新 KG 实体关系
        4. 添加向量索引
        """
        success = True
        
        # 1. Wiki 明文写入
        if not self.baseline.write(memory_item):
            success = False
        
        # 2. KG 实体关系
        if not self.rag_engine.add_entity(memory_item):
            success = False
        
        # 3. 向量索引
        if not self.rag_engine.add_vector(memory_item):
            success = False
        
        return success