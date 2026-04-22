# D-30: Sideline Agent Prompt System — Revised

**状态**: 设计完成（v2）
**日期**: 2026-04-22
**Proposal**: Committee + Butterfly Signal + Independent Scoring Abstraction

---

## Fundamental Principles

### 1. Butterfly Signal 驱动进化

不是按时间或阈值触发精炼，而是按 butterfly wing 强度：

```
强 forward wing（X → Y 经常发生）= Y 是有价值的结果
强 backward wing（因为 X 导致 Y）= X 是关键决策点
两者都强 = 强因果链 = 高价值 pattern = 应该打包
```

**蝴蝶信号公式**：
```
butterfly_signal = forward_strength × backward_strength × co_occurrence_rate
```

### 2. LLM 的角色是"解释者"而非"决策者"

```
结构化规则（高 reuse_score + 强 butterfly wing）→ 决策
LLM → 解释：为什这个值得打包，生成 human-readable description
JSON schema → 确保结构化，不依赖 LLM 遵循自由文本
```

### 3. Profile Generation Committee 不是 pipeline，是 committee

三个角色共享同一个 L2 KG view，通过 JSON 协商达成共识，不是线性 pipeline。

### 4. **ScoringPolicy 独立抽象（核心要求）**

打分机制必须是独立抽象，与 committee 逻辑解耦，方便迭代调整。

---

## 系统架构

```
Layer 1 KG (raw transcripts)
     ↓ SidelineTranscriber
Layer 2 KG (experience + butterfly wings)
     ↓ butterfly_signal
┌─────────────────────────────────────────────┐
│           ScoringPolicy Abstraction             │
│  (Independent module, pluggable strategies)     │
│                                             │
│  ┌─────────────────┐  ┌──────────────────┐  │
│  │ ButterflySignalPolicy │  │ ReuseThresholdPolicy │  │
│  │  (wing strength)    │  │  (reuse_score)      │  │
│  └────────┬─────────┘  └──────────┬───────┘  │
│           │                         │          │
│           └──────┬──────────────────┘          │
│                  ▼                             │
│         ┌────────────────┐                    │
│         │  ScoringEngine │  ← 独立引擎      │
│         │  (统一接口)    │                    │
│         └────────┬───────┘                    │
└──────────────────┼────────────────────────────┘
                   │ scoring_signal
                   ▼
┌─────────────────────────────────────────────┐
│       Profile Generation Committee            │
│  ┌──────────┐ ┌──────────┐ ┌──────────┐  │
│  │Transcriber│ │  Refiner   │ │ Architect │  │
│  └─────┬────┘ └────┬────┘ └───┬────┘  │
│        └────────────┼──────────┘          │
│                     ▼                      │
│            JSON Negotiation                 │
│         {proposal, vote, commit}           │
└────────────────┬───────────────────────────┘
                 │
       ┌────────┴────────┐
       ▼                 ▼
  Skill Bundles    BaseProfile Bundles
       │                 │
       └────────┬────────┘
                ▼
         Active Agent 使用
                ▼
         表现反馈 → Layer 2 KG
                ▼
         Butterfly 自我强化 ♻️
```

---

## 核心抽象

### ScoringPolicy Protocol

```python
from typing import Protocol, runtime_checkable
from dataclasses import dataclass

@dataclass
class ScoringSignal:
    """打分信号的统一格式"""
    trigger: bool                    # 是否触发进化
    score: float                    # 综合分数 0-1
    confidence: float               # 置信度 0-1
    breakdown: dict[str, float]    # 各维度分项
    reason: str                    # 触发原因（给 LLM 解释用）
    metadata: dict                 # 附加信息

@runtime_checkable
class ScoringPolicy(Protocol):
    """打分策略协议——所有打分策略必须实现此接口"""
    
    name: str  # 策略名称，如 "butterfly_signal", "reuse_threshold"
    
    def evaluate(self, context: "ScoringContext") -> ScoringSignal:
        """评估是否触发进化"""
        ...
    
    def get_trigger_threshold(self) -> float:
        """返回当前触发阈值（可配置）"""
        ...

@dataclass
class ScoringContext:
    """打分上下文——传递给 ScoringPolicy 的数据"""
    # L2 KG 相关
    nodes: list["ExperienceNode"]
    forward_wings: list["Wing"]
    backward_wings: list["Wing"]
    
    # KG 图结构统计（客观量化信号）
    graph_stats: dict | None = None  # {"out_degree": int, "in_degree": int, "pagerank": float, "cluster_coef": float}
    
    # 时间相关
    time_window_hours: int
    
    # 元数据
    domain: str | None = None
    agent_id: str | None = None
    metadata: dict | None = None
```

### ScoringEngine

```python
class ScoringEngine:
    """
    统一打分引擎。
    
    支持多策略组合（weighted average / max / min）。
    策略可插拔，随时切换或叠加。
    """
    
    def __init__(self, policies: list[ScoringPolicy], combine_mode: str = "weighted"):
        self._policies = policies
        self._combine_mode = combine_mode  # "weighted" | "max" | "min" | "any"
        self._weights = {p.name: 1.0 for p in policies}
    
    def set_weights(self, weights: dict[str, float]) -> None:
        """动态调整策略权重"""
        self._weights.update(weights)
    
    def add_policy(self, policy: ScoringPolicy, weight: float = 1.0) -> None:
        """运行时添加新策略"""
        self._policies.append(policy)
        self._weights[policy.name] = weight
    
    def evaluate(self, context: ScoringContext) -> ScoringSignal:
        """多策略评估，返回综合信号"""
        signals = [p.evaluate(context) for p in self._policies]
        
         # 按 combine_mode 聚合
        if self._combine_mode == "weighted":
            total_weight = sum(self._weights.get(s.name, 1.0) for s in signals)
            combined_score = sum(s.score * self._weights.get(s.name, 1.0) for s in signals) / total_weight
            combined_trigger = any(s.trigger for s in signals)
            combined_confidence = sum(s.confidence * self._weights.get(s.name, 1.0) for s in signals) / total_weight
        elif self._combine_mode == "max":
            combined_score = max(s.score for s in signals)
            combined_trigger = any(s.trigger for s in signals)
            combined_confidence = max(s.confidence for s in signals)
        elif self._combine_mode == "all":
            combined_trigger = all(s.trigger for s in signals)
            combined_score = min(s.score for s in signals)
            combined_confidence = min(s.confidence for s in signals)
        else:  # any
            combined_trigger = any(s.trigger for s in signals)
            combined_score = max(s.score for s in signals)
            combined_confidence = max(s.confidence for s in signals)
        
        breakdown = {s.name: s.score for s in signals}
        return ScoringSignal(
            trigger=combined_trigger,
            score=combined_score,
            confidence=combined_confidence,
            breakdown=breakdown,
            reason=self._build_reason(signals),
            metadata={"policies": list(self._policies)}
        )
```

---

## 策略实现

### ButterflySignalPolicy

```python
@dataclass
class ButterflySignalPolicy:
    """蝴蝶信号策略——基于 butterfly wing 强度 + KG 图结构特征触发进化"""
    
    name: str = "butterfly_signal"
    forward_threshold: float = 0.6    # forward wing 触发阈值
    backward_threshold: float = 0.6   # backward wing 触发阈值
    co_occurrence_min: int = 3        # 最小共现次数
    structural_weight_enable: bool = True  # 是否启用 KG 图结构权重
    
    def evaluate(self, context: ScoringContext) -> ScoringSignal:
        # 计算 forward/backward wing 强度
        forward_strength = self._calc_forward_strength(context.forward_wings, context.nodes)
        backward_strength = self._calc_backward_strength(context.backward_wings, context.nodes)
        
        # 共现率
        co_occurrence = self._calc_co_occurrence(context.forward_wings, context.backward_wings)
        co_rate = min(co_occurrence / max(self.co_occurrence_min, 1), 1.0)
        
        # Butterfly signal core
        butterfly_core = forward_strength * backward_strength * co_rate
        
        # KG 图结构权重（可选）
        if self.structural_weight_enable and context.graph_stats:
            structural = self._calc_structural_weight(context.graph_stats)
        else:
            structural = 1.0
        
        signal = butterfly_core * structural
        
        trigger = (forward_strength >= self.forward_threshold and 
                   backward_strength >= self.backward_threshold)
        
        return ScoringSignal(
            trigger=trigger,
            score=signal,
            confidence=(forward_strength + backward_strength) / 2,
            breakdown={
                "forward_strength": forward_strength,
                "backward_strength": backward_strength,
                "co_occurrence_rate": co_rate,
                "structural_weight": structural if self.structural_weight_enable else 1.0,
                "butterfly_core": butterfly_core,
            },
            reason=f"butterfly: fwd={forward_strength:.2f}, bwd={backward_strength:.2f}, struct={structural:.2f}",
            metadata={"policy": self.name}
        )
    
    def _calc_structural_weight(self, graph_stats: dict) -> float:
        """
        基于 KG 图结构特征计算权重。
        
        图结构因子（客观量化）：
        - out_degree: entity 指向多少其他 entity（结论丰富度）
        - in_degree: 有多少 entity 指向它（原因重要度）
        - pagerank: 全局重要性（0-1）
        - cluster_coef: 聚类系数（是否在密集子图中心）
        
        归一化权重公式：
        structural = (norm(out_degree) + norm(in_degree) × 1.5 + pagerank × 2 + cluster_coef) / 5
        """
        out_d = graph_stats.get("out_degree", 0)
        in_d = graph_stats.get("in_degree", 0)
        pr = graph_stats.get("pagerank", 0.0)
        cc = graph_stats.get("cluster_coef", 0.0)
        
        # 归一化（假设 max_out_degree = 20, max_in_degree = 20）
        norm_out = min(out_d / 20, 1.0)
        norm_in = min(in_d / 20, 1.0)
        
        # 加权求和
        structural = (
            norm_out +
            norm_in * 1.5 +
            pr * 2.0 +
            cc
        ) / 5.0
        
        return min(max(structural, 0.1), 2.0)  # 限制在 [0.1, 2.0] 避免极端值
```

### ReuseThresholdPolicy

```python
@dataclass
class ReuseThresholdPolicy:
    """复用阈值策略——基于 reuse_score 触发进化"""
    
    name: str = "reuse_threshold"
    reuse_threshold: float = 0.7       # reuse_score 触发阈值
    min_node_count: int = 5            # 最少节点数
    
    def evaluate(self, context: ScoringContext) -> ScoringSignal:
        if not context.nodes:
            return ScoringSignal(trigger=False, score=0.0, confidence=1.0, 
                               breakdown={}, reason="no nodes", metadata={})
        
        avg_reuse = sum(n.reuse_score for n in context.nodes) / len(context.nodes)
        max_reuse = max(n.reuse_score for n in context.nodes)
        
        trigger = (max_reuse >= self.reuse_threshold and 
                   len(context.nodes) >= self.min_node_count)
        
        return ScoringSignal(
            trigger=trigger,
            score=avg_reuse,
            confidence=min(len(context.nodes) / self.min_node_count, 1.0),
            breakdown={
                "avg_reuse": avg_reuse,
                "max_reuse": max_reuse,
                "node_count": len(context.nodes),
            },
            reason=f"reuse: avg={avg_reuse:.2f}, max={max_reuse:.2f}, count={len(context.nodes)}",
            metadata={"policy": self.name}
        )
```

---

## Profile Generation Committee

### Committee Member Schema

```yaml
committee_member:
  role: "transcriber" | "refiner" | "architect"
  version: str
  
  # 触发条件（由 ScoringEngine 驱动，不是定时）
  trigger: ScoringSignal  # 传入的打分信号
  
  # 输入输出 schema
  input_schema: JSONSchema
  output_schema: JSONSchema
  
  # LLM 角色（"explain" | "validate" | "none"）
  llm_role: str
  
  # 协商协议
  negotiation: "vote" | "consensus" | "single"
  
  # 路由
  routing:
    success: destination
    failure: error_handler
```

### Transcriber（角色 1）

**职责**: L1→L2 提取，管理 relevant/discarded 分类

```yaml
role: transcriptor
task: |
  将 ActionUnits 提取为 fact records，决定哪些入 L2 KG。
  分类标准：
  - 有工具调用 + 有执行结果 → relevant=true
  - 闲聊/探索/无工具 → relevant=false（留 L1）
  - 工具失败但有调试价值 → relevant=true（标注 outcome=failure）
input_schema:
  type: ActionUnit
  fields:
    - id: string (required)
    - timestamp: string (required)
    - user_intent: string (required)
    - tool_calls: array[object] (required)
    - tool_results: array[object] (optional)
output_schema:
  type: object
  fields:
    relevant_facts: array[FactRecord]
    discarded_facts: array[FactRecord]
    extraction_metadata:
      total_action_units: int
      relevant_count: int
      discarded_count: int
llm_role: "explain"  # 为什这个 fact 值得入 L2
negotiation: "single"  # Transcriber 单独决策
```

### Refiner（角色 2）

**职责**: L2 nodes → Skill Bundle，基于 butterfly signal

```yaml
role: refiner
task: |
  将 high-reuse experience nodes 打包为 skill bundle。
  
  打包标准：
  - 同一 domain 下 butterfly_signal >= threshold → 打包
  - 跨 domain 但蝴蝶翼强连接 → 创建 composite skill
  - 单次出现节点 → 不打包
  
  每次打包输出包含：
  - name: 技能名称
  - description: 一句话描述（LLM 生成）
  - steps: 步骤序列
  - anti_patterns: 反模式
input_schema:
  type: object
  fields:
    nodes: array[ExperienceNode]
    scoring_signal: ScoringSignal
output_schema:
  type: object
  fields:
    new_skill_bundles: array[SkillBundle]
    retired_bundles: array[string]  # bundle_id
    merge_decisions: array[MergeDecision]
llm_role: "explain"  # 解释打包决策，生成 description
negotiation: "vote"  # Refiner + Architect 共同投票
```

### Architect（角色 3）

**职责**: L2 nodes → BaseProfile Bundle

```yaml
role: architect
task: |
  将 domain nodes 编译为 BaseProfile bundle。
  
  Layer 分配策略：
  - skill_domain + 高频工具调用 → L4 (Tool definitions)
  - user_intent patterns → L1 (Agent Identity)
  - failure + recovery patterns → L2 (Operational Guidelines)
  - domain context → L3 (Contextual Memory)
  
  触发条件：
  - scoring_signal.trigger == true
  - 同一 domain 下 nodes >= 5
input_schema:
  type: object
  fields:
    nodes: array[ExperienceNode]
    domain: string
    scoring_signal: ScoringSignal
output_schema:
  type: object
  fields:
    new_profiles: array[BaseProfileBundle]
    updated_profiles: array[BaseProfileBundle]
    retired_profiles: array[string]
llm_role: "explain"  # 生成 L1/L2 Layer content
negotiation: "vote"  # 与 Refiner 共同投票
```

---

## Committee 协商流程

```python
class ProfileGenerationCommittee:
    """
    Profile 生成委员会。
    
    三个角色按 butterfly signal 触发，
    通过 JSON 协商决定最终产出。
    """
    
    def __init__(
        self,
        scoring_engine: ScoringEngine,
        transcriptor: "TranscriberAgent",
        refiner: "RefinerAgent",
        architect: "ArchitectAgent",
    ):
        self._scoring = scoring_engine
        self._agents = {
            "transcriber": transcriptor,
            "refiner": refiner,
            "architect": architect,
        }
    
    async def process(self, context: ScoringContext) -> CommitteeResult:
        """
        执行委员会流程。
        
        1. ScoringEngine 评估是否触发
        2. 触发则依次执行三个角色
        3. 角色间 JSON 协商
        4. 投票决定是否 commit
        """
        # Step 1: 评估触发条件
        signal = self._scoring.evaluate(context)
        
        if not signal.trigger:
            return CommitteeResult(
                triggered=False,
                signal=signal,
                outputs={},
                votes=[],
            )
        
        # Step 2: 执行各角色
        outputs = {}
        for role_name, agent in self._agents.items():
            output = await agent.process(context, signal)
            outputs[role_name] = output
        
        # Step 3: 投票（Refiner + Architect 共同决定）
        votes = self._collect_votes(outputs, signal)
        
        # Step 4: 共识检查
        approved = self._check_consensus(votes, signal)
        
        return CommitteeResult(
            triggered=True,
            signal=signal,
            outputs=outputs if approved else {},
            votes=votes,
            approved=approved,
        )
```

---

## 与现有代码的关系

| 现有实现 | 对应新设计 | 状态 |
|---------|----------|------|
| SidelineTranscriber | Transcriber role | 改造：加 prompt schema + JSON 输出 |
| SidelineTranscriber._llm_extract_relations | Transcriber.llm_role="explain" | 复用 |
| experience_kg.create_skill_bundle() | Refiner output | 已有，需验证 |
| AgentBaseProfile | Architect output | 已有，需加 from_experience_nodes() |
| ReuseTracker | ReuseThresholdPolicy | 复用 |
| butterfly_wing.build_butterfly_associations() | ButterflySignalPolicy | 复用 |

### 新增模块

| 模块 | 文件 | 说明 |
|------|------|------|
| ScoringPolicy Protocol | `src/scoring/policy.py` | 抽象协议 |
| ScoringEngine | `src/scoring/engine.py` | 多策略组合引擎 |
| ButterflySignalPolicy | `src/scoring/policies/butterfly_signal.py` | 蝴蝶信号策略 |
| ReuseThresholdPolicy | `src/scoring/policies/reuse_threshold.py` | 复用阈值策略 |
| Committee | `src/scoring/committee.py` | Profile 生成委员会 |
| TaskSpec | `src/scoring/taskspec.py` | Committee member 定义 |
| ScoringCalibrationSystem | `src/scoring/calibration.py` | 支线系统：阈值自收敛 |

---

## 支线系统：Scoring Calibration（阈值自收敛）

### 定位

ScoringPolicy 的阈值参数（forward_threshold、backward_threshold、co_min 等）是拍脑袋的。需要用历史数据自迭代验证，找收敛值。

**重要**：这是支线系统，不阻塞主流程。Committee 按当前阈值运行，CalibrationSystem 在后台调优参数，定期更新阈值。

```
┌─────────────────────────────────────────────┐
│        Main: ScoringEngine + Committee         │
│   （使用当前阈值，定期读取更新后的阈值）          │
└─────────────────────┬───────────────────────┘
                      │ 定期触发（每天/每周）
                      ▼
┌─────────────────────────────────────────────┐
│      Side: ScoringCalibrationSystem           │
│   1. 收集历史 KG 数据                        │
│   2. 枚举阈值网格                            │
│   3. 回测验证                               │
│   4. F1 找最优                              │
│   5. 更新阈值参数                            │
└─────────────────────────────────────────────┘
```

### Ground Truth 定义

"该打包" 的 ground truth：打包出的 bundle 在后续 session 中被 agent 调用了。

```
True Positive:    打包了 + 且后续被调用了
False Positive:  打包了 + 但后续没被调用
False Negative:  没打包 + 但后续被调用了
```

### 实现

```python
@dataclass
class CalibrationResult:
    """一次调参结果"""
    forward_threshold: float
    backward_threshold: float
    co_min: int
    precision: float
    recall: float
    f1: float
    tp: int
    fp: int
    fn: int


class ScoringCalibrationSystem:
    """
    支线系统：离线回测 + F1 优化阈值。
    
    不阻塞主流程。
    定期运行，更新 ScoringPolicy 的阈值参数。
    """
    
    def __init__(
        self,
        kg: "KnowledgeGraph",
        scoring_policy: "ScoringPolicy",
        bundle_history: "BundleHistory",  # bundle_id → 后续调用次数
    ):
        self._kg = kg
        self._policy = scoring_policy
        self._history = bundle_history
    
    def run(self) -> CalibrationResult:
        """执行一次完整的校准流程"""
        # Step 1: 收集历史 KG 数据
        nodes = self._kg.get_all_nodes()
        wings = self._kg.get_all_wings()
        
        # Step 2: 枚举阈值网格
        best_f1 = 0.0
        best_params = {}
        
        for fwd_t in [0.3, 0.5, 0.7]:
            for bwd_t in [0.3, 0.5, 0.7]:
                for co_min in [2, 3, 5]:
                    # Step 3: 用这组阈值跑历史数据
                    triggered = self._simulate_trigger(
                        nodes, wings,
                        forward_threshold=fwd_t,
                        backward_threshold=bwd_t,
                        co_min=co_min
                    )
                    
                    # Step 4: 计算 TP/FP/FN
                    tp = fp = fn = 0
                    for bundle_id in triggered:
                        if self._history.was_called(bundle_id):
                            tp += 1
                        else:
                            fp += 1
                    
                    for bundle_id in self._history.get_all_bundles():
                        if bundle_id not in triggered and self._history.was_called(bundle_id):
                            fn += 1
                    
                    # 计算 F1
                    precision = tp / (tp + fp) if (tp + fp) > 0 else 0
                    recall = tp / (tp + fn) if (tp + fn) > 0 else 0
                    f1 = 2 * precision * recall / (precision + recall) if (precision + recall) > 0 else 0
                    
                    if f1 > best_f1:
                        best_f1 = f1
                        best_params = {
                            "forward_threshold": fwd_t,
                            "backward_threshold": bwd_t,
                            "co_min": co_min,
                            "precision": precision,
                            "recall": recall,
                            "f1": f1,
                            "tp": tp, "fp": fp, "fn": fn,
                        }
        
        # Step 5: 更新策略阈值
        self._policy.set_thresholds(
            forward_threshold=best_params["forward_threshold"],
            backward_threshold=best_params["backward_threshold"],
            co_min=best_params["co_min"],
        )
        
        return CalibrationResult(**best_params)
```

### BundleHistory 追踪

```python
class BundleHistory:
    """追踪 bundle 被调用的情况，用于 ground truth"""
    
    def record_call(self, bundle_id: str, session_id: str) -> None:
        """每次 agent 调用 bundle 时记录"""
        key = (bundle_id, session_id)
        self._calls[key] = datetime.now(timezone.utc).isoformat()
    
    def was_called(self, bundle_id: str) -> bool:
        """Bundle 在后续 session 中是否被调用过"""
        return any(k[0] == bundle_id for k in self._calls)
    
    def get_all_bundles(self) -> set[str]:
        """所有打包过的 bundle"""
        return {k[0] for k in self._calls}
```

### 与主流程的关系

```
Main Flow:
  ScoringEngine.evaluate(context)
      ↓
  Committee.process(context, signal)
      ↓
  bundle = Refiner.create_skill_bundle(...)
      ↓
  BundleHistory.record_call(bundle.id, session_id)  ← 记录 ground truth

Side Flow (定时):
  CalibrationSystem.run()
      ↓
  ScoringPolicy.set_thresholds(...)  ← 更新阈值
```

### 实施时机

- Phase 1-4 先实现主流程
- Phase 5 端到端集成时加入 CalibrationSystem
- 或者作为独立模块从 Day 1 就开始收集数据（即使阈值暂时是拍脑袋的）

---

## 实施计划

### Phase 1: Scoring 抽象层（核心，不改业务逻辑）
- [ ] `src/scoring/policy.py` — ScoringPolicy Protocol + ScoringContext + ScoringSignal
- [ ] `src/scoring/engine.py` — ScoringEngine（多策略组合）
- [ ] `src/scoring/policies/butterfly_signal.py` — ButterflySignalPolicy
- [ ] `src/scoring/policies/reuse_threshold.py` — ReuseThresholdPolicy
- [ ] 单元测试

### Phase 2: Committee 框架
- [ ] `src/scoring/committee.py` — Committee 协商流程
- [ ] `src/scoring/taskspec.py` — TaskSpec 定义
- [ ] `tests/scoring/test_engine.py`
- [ ] `tests/scoring/test_committee.py`

### Phase 3: Transcriber 改造
- [ ] 输出 JSON schema 格式化
- [ ] 加 relevant/discarded 分类
- [ ] TaskSpec 定义

### Phase 4: Refiner + Architect 实现
- [ ] Refiner agent（skill bundle 生成）
- [ ] Architect agent（BaseProfile bundle 生成）
- [ ] 与 profile_registry 集成

### Phase 5: 端到端集成
- [ ] ScoringEngine → Committee 触发链路
- [ ] Butterfly signal → KG wing 更新循环
- [ ] E2E 测试

---

## ScoringPolicy 迭代指南

**调整打分策略时，不需要改动 Committee 或 Agent 代码：**

```
场景 1: 调高蝴蝶信号阈值
→ 修改 ButterflySignalPolicy.forward_threshold = 0.7

场景 2: 从"蝴蝶信号"切换到"复用阈值"
→ ScoringEngine(policies=[ReuseThresholdPolicy()], combine_mode="any")

场景 3: 两个策略叠加（任一触发即可）
→ ScoringEngine(policies=[ButterflySignalPolicy(), ReuseThresholdPolicy()], combine_mode="any")

场景 4: 新增策略（如 "time_decay_policy"）
→ 实现 ScoringPolicy → add_policy()
```

---

*设计完成，等待实施确认*
