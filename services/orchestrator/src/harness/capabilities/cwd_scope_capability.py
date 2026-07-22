"""P1 MultiCwdScopeCapability — 动态层多 cwd 清单 + set_active_cwd 切换工具。

设计 §6:AO2 第一个动态 + 自带工具的 capability。per-session 实例化(routes
_build_native_session 注入 cwd_scope + session_key),get_instructions 运行时构建
cwd 清单(无 label 前缀,决策 2),get_toolset 提供 set_active_cwd(label) 工具(决策 6)。

指令排序:不实现 get_ordering(设计 §6 原稿返 ('L-CWD',65) 元组是错的——实测
observe_capability.get_ordering 返 CapabilityOrdering;LayerCapability/ProfileCapability
根本不覆写 get_ordering,指令序靠 caps list append 顺序)。T8 在 routes caps 列表里
控制 append 位置(MEMORY 后 TOOLS 前)即可。本 capability 无 wrap hook,继承默认。

工具名:设计 §6 措辞 'v2_set_active_cwd'(经 build_native_agent 统一 prefix)不准。
build_native_agent 本身不加 prefix,prefix 是 ToolBridgeCapability.get_toolset 的
.prefixed('v2')。本 capability 自带工具非 ToolBridge 桥接工具,返的 toolset 不经
ToolBridge prefix,故模型可见名就是 set_active_cwd(无 v2_ 前缀)。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from pydantic_ai import FunctionToolset, Tool
from pydantic_ai.capabilities import AbstractCapability
from pydantic_ai.toolsets import AbstractToolset

from src.agent.agent_registry import ResolvedCwd
from src.tools.cwd_scope import (
    _active_cwd,
    get_session_active_cwd,
    set_session_active_cwd,
)


@dataclass
class MultiCwdScopeCapability(AbstractCapability[Any]):
    """动态层多 cwd 清单 + set_active_cwd 切换工具(主路径常驻)。

    per-session 注入 cwd_scope(list[ResolvedCwd],T2 resolver 产出,永不空)
    + session_key(持久化 active_cwd,T8 routes 按 'harness_type:session_id' 传)。
    """

    id: str = "cwd_scope"
    description: str = "Multi-CWD scope — dynamic cwd manifest + set_active_cwd tool"
    defer_loading: bool = False  # 主路径常驻(非 recall 侧 defer)
    cwd_scope: list[ResolvedCwd] = field(default_factory=list)
    session_key: str = ""

    def get_instructions(self) -> str:
        # 运行时构建(决策 2:无 label 前缀规则)。
        lines = [
            "## 工作域(Multi-CWD Scope)",
            "你同时操作多个工作目录。",
            "- 绝对路径:直接用",
            "- 相对路径:相对当前激活 cwd(默认 workspace)",
            "- 切换激活 cwd:调用 set_active_cwd(label),之后相对路径走新基准",
            "",
            "可用 cwd(label 用于 set_active_cwd):",
        ]
        for e in self.cwd_scope:
            mark = " (default)" if e.default else ""
            lines.append(f"- {e.label}{mark} → {e.path_abs}")
        return "\n".join(lines)

    def get_toolset(self) -> AbstractToolset[Any]:
        # 决策 6:自带切换工具(非 ToolBridge 桥接,返的 toolset 不加 v2_ prefix)。
        cwd_scope = self.cwd_scope
        session_key = self.session_key

        async def set_active_cwd(label: str) -> str:
            for e in cwd_scope:
                if e.label == label:
                    set_session_active_cwd(session_key, e.path_abs)  # session 级持续
                    _active_cwd.set(Path(e.path_abs))
                    return f"激活 cwd 已切换到 [{label}] {e.path_abs}"
            # 设计 §10.3:未知 label 返错误串(含可用 label 列表),不 raise(防崩)。
            labels = [e.label for e in cwd_scope]
            return f"未知 label '{label}',可用: {labels}"

        ts = FunctionToolset[Any]()
        ts.add_tool(
            Tool(
                set_active_cwd,
                name="set_active_cwd",
                description=(
                    "切换当前激活 cwd(相对路径的新基准)。"
                    "label 见工作域清单(Multi-CWD Scope)。"
                ),
            )
        )
        return ts
