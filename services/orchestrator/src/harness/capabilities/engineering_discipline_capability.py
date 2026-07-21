"""Engineering Discipline Capability — CC 5 条软件工程纪律 + context-mgmt 基线纪律段。

ADR: ``~/harness-adr.md`` 第一层「工程纪律」维度。抄 Claude Code system prompt 的
5 条软件工程纪律 + 「够信息就动」context-mgmt,作 harness 基线纪律段,进 stable
(``dynamic=False``)instructions,结构性可缓存,经 ``build_native_agent`` 默认注入。

覆盖边界(诚实标注,mustFix#2):
- **覆盖**:``build_native_agent`` 的所有调用方 —— harness routes(``/h``)、老 chat(``/v1``)、
  graph 多 agent 拓扑(经 ``agent_factory``)、smoke、check_r2_cache。
- **不覆盖**:meta-agent transient subagent 走 ``run_agent_turn → _state.llm_client.chat()``
  独立 llm 路径,不经 ``build_native_agent``,本段进不去(``agent_runner.py`` R2 主路径冻结
  红线,改它超 scope)。后续单独评估是否注入。

位置(非绝对首段,mustFix#1):``position=outermost`` 只让本段在 **capability-tier 内居首**。
pydantic-ai ``_get_instructions`` 永远把 base instructions(``Agent(instructions=...)``)
放最前、``cap_instructions``(含本段)在后 —— 故调用方传非空 instructions 时纪律段排在其后。
但整段仍在 stable(``dynamic=False``)prefix,模型每轮可见、可 cache。不宣称 instructions 绝对首段。

cache 隐性耦合(critique):pydantic-ai 把 base+cap instructions 包进单个
``InstructionPart(dynamic=False)``,本段 cache 资格**传染自 base 形态** —— 若将来 base
改 ``@system_prompt(dynamic=True)``,整段翻 dynamic 丢 cache 无告警。

glm A/B(ADR line114,optional#4):CC 5 条对非 Claude 模型(如 glm)有效性无 ab;英文原文对
Claude 有效,glm(中文)待 A/B 对照。默认抄 CC,提供 ``enabled`` / ``discipline_text`` 构造参数
+ ``AO2_DISCIPLINE_DISABLED`` env 旋钮(读于 ``build_native_agent`` 组装层)做 opt-out / 文本
替换,验证后再固化。

否决项(ADR 明示,不引入):80 字 micro-manifest、\"不可覆盖\"修辞、71K 字膨胀。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from pydantic_ai.capabilities import AbstractCapability, CapabilityOrdering

from src.harness.capabilities._stable_cache import _cached

_DISCIPLINE = """\
## Engineering Discipline

Baseline rules for every turn in this harness:

1. Tools over shell. Prefer the purpose-built tool when one fits; fall back to shell only when no tool covers the task.
2. Parallelize independent calls. Make independent tool calls in the same response instead of sequentially.
3. Reference code precisely. Cite `file_path:line_number` so locations are verifiable and clickable.
4. Match surrounding style. Naming, idiom, comment density — code that reads like its neighbors.
5. Report faithfully. If a check failed, show the output; if a step was skipped, say so. No false success, no hedging.

When you have enough information to act, act. Don't re-derive established facts or narrate options you won't pursue; if weighing a choice, give a recommendation rather than an exhaustive survey.
"""


@dataclass
class EngineeringDisciplineCapability(AbstractCapability[Any]):
    """CC 5 条工程纪律 + context-mgmt 基线段(stable,常驻,默认注入)。

    A/B 旋钮(ADR line114):``enabled=False`` 或 ``discipline_text=""`` 关段;
    ``discipline_text=<str>`` 换文本;默认走 ``_DISCIPLINE`` 常量。env 旋钮
    ``AO2_DISCIPLINE_DISABLED`` 在 ``build_native_agent`` 组装层读(native_agent.py),
    进程级读一次(启动时)。
    """

    id: str = "engineering_discipline"
    description: str = "Baseline engineering discipline (CC 5 rules + context-mgmt)"
    defer_loading: bool = False  # 常驻(stable 纪律段,不 defer)
    discipline_text: str | None = None  # None→默认 _DISCIPLINE;传 ""→关段(opt-out)
    enabled: bool = True  # False→关段

    def get_ordering(self) -> CapabilityOrdering:
        # outermost → capability-tier 内居首(非 instructions 绝对首段,见模块 docstring)。
        return CapabilityOrdering(position="outermost")

    def get_instructions(self) -> str:
        # G1: stable prefix hash 缓存 —— (enabled, discipline_text) 决定输出,
        # pydantic-ai 每 .run() 重调,缓存免每轮重读常量/重判分支。
        return _cached(
            ("discipline", self.discipline_text, self.enabled),
            self._build_instructions,
        )

    def _build_instructions(self) -> str:
        if not self.enabled:
            return ""
        if self.discipline_text is not None:
            return self.discipline_text
        return _DISCIPLINE
