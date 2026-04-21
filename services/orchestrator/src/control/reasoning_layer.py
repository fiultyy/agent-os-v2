"""ReasoningLayer — 推理追踪层。

职责：
- 追踪 LLM 推理过程（thinking chains）
- 提供推理路径可视化
- 支持推理回溯和审计
"""

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any


@dataclass
class ReasoningStep:
    """推理步骤。

    Attributes:
        id: 步骤唯一标识。
        parent_id: 父步骤 ID（用于构建树结构），None 表示根步骤。
        content: 推理内容文本。
        model: 生成此步骤的模型名称。
        timestamp: ISO 8601 时间戳。
    """

    id: str
    parent_id: str | None
    content: str
    model: str
    timestamp: str


class ReasoningLayer:
    """Chain-of-Thought 推理追踪层。

    记录每个 session 的推理链，支持树结构构建和路径摘要。

    Example:
        layer = ReasoningLayer()
        await layer.capture_step("s1", ReasoningStep(
            id="1", parent_id=None, content="Let's think...",
            model="gpt-4", timestamp=datetime.now().isoformat()
        ))
        tree = layer.build_tree("s1")
    """

    def __init__(self, max_sessions: int = 100) -> None:
        self._chains: dict[str, list[ReasoningStep]] = {}
        self._max_sessions = max_sessions

    async def capture_step(self, session_id: str, step: ReasoningStep) -> None:
        """记录一个推理步骤。

        若 session_id 不存在则自动创建。

        Args:
            session_id: 会话 ID。
            step: 推理步骤数据。
        """
        if session_id not in self._chains:
            if len(self._chains) >= self._max_sessions:
                # Remove oldest session
                oldest = next(iter(self._chains))
                del self._chains[oldest]
            self._chains[session_id] = []
        self._chains[session_id].append(step)

    def get_chain(self, session_id: str) -> list[ReasoningStep]:
        """获取完整推理链（按插入顺序）。

        Args:
            session_id: 会话 ID。

        Returns:
            该 session 的所有推理步骤列表，若不存在则返回空列表。
        """
        return list(self._chains.get(session_id, []))

    def build_tree(self, session_id: str) -> dict[str, Any]:
        """将推理链构建为树结构用于可视化。

        使用 parent_id 构建多叉树，返回根节点列表及嵌套 children。

        Args:
            session_id: 会话 ID。

        Returns:
            树结构字典，格式：{"roots": [TreeNode, ...]}。
            每个 TreeNode 含 id/content/model/timestamp/children。
        """
        steps = self._chains.get(session_id, [])
        # id -> node map
        nodes: dict[str, dict[str, Any]] = {}
        for step in steps:
            nodes[step.id] = {
                "id": step.id,
                "content": step.content,
                "model": step.model,
                "timestamp": step.timestamp,
                "parent_id": step.parent_id,
                "children": [],
            }

        roots: list[dict[str, Any]] = []
        for step in steps:
            node = nodes[step.id]
            if step.parent_id is None:
                roots.append(node)
            else:
                parent = nodes.get(step.parent_id)
                if parent is not None:
                    parent["children"].append(node)

        return {"session_id": session_id, "roots": roots}

    def get_path_summary(self, session_id: str) -> str:
        """生成推理路径摘要文本。

        沿根到叶的最长路径生成摘要，每步显示 id + content 前 50 字符。

        Args:
            session_id: 会话 ID。

        Returns:
            多行摘要文本，格式为 "Step {id}: {content[:50]}..."。
            若 session 不存在或无步骤，返回空字符串。
        """
        steps = self._chains.get(session_id, [])
        if not steps:
            return ""

        # Build tree to find deepest leaf along each root
        tree = self.build_tree(session_id)
        lines: list[str] = []

        def deepest_path(node: dict[str, Any]) -> list[dict[str, Any]]:
            """Return list of nodes from root to deepest descendant."""
            path = [node]
            children = node.get("children", [])
            if children:
                longest = max(children, key=lambda c: _depth(c))
                path.extend(deepest_path(longest))
            return path

        def _depth(node: dict[str, Any]) -> int:
            children = node.get("children", [])
            if not children:
                return 1
            return 1 + max(_depth(c) for c in children)

        for root in tree["roots"]:
            for node in deepest_path(root):
                snippet = (
                    node["content"]
                    if len(node["content"]) <= 80
                    else node["content"][:80] + "..."
                )
                lines.append(f"Step {node['id']}: {snippet}")

        return "\n".join(lines)

    def clear(self, session_id: str) -> None:
        """清除指定 session 的推理链。

        Args:
            session_id: 会话 ID。
        """
        self._chains.pop(session_id, None)

    def clear_all(self) -> None:
        """Clear all reasoning chains."""
        self._chains.clear()
