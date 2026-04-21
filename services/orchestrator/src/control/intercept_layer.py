"""InterceptLayer — 请求/响应拦截层。

职责：
- 在 LLM 调用前拦截请求，阻断/修改/放行
- 在 LLM 响应后拦截响应，阻断/修改/放行
- 记录拦截日志用于审计
"""

from dataclasses import dataclass, field
from typing import Any


@dataclass
class InterceptResult:
    """拦截结果。

    Attributes:
        action: 执行动作，"allow" | "block" | "modify"。
        reason: 执行原因的简要说明。
        modified: 如果 action 为 "modify"，则为修改后的数据；否则为 None。
    """

    action: str
    reason: str
    modified: dict[str, Any] | None = None


class InterceptLayer:
    """请求/响应拦截层。

    在 LLM 调用前拦截请求，在 LLM 响应后拦截响应，
    支持 pattern 匹配（字符串包含判断）。

    规则按添加顺序逐一匹配，命中即执行对应 action。
    """

    def __init__(self) -> None:
        self._rules: list[dict[str, str]] = []
        self._log: list[dict[str, Any]] = []

    def add_rule(
        self,
        pattern: str,
        action: str,
        reason: str,
        modifier: Any | None = None,
    ) -> None:
        """添加拦截规则。

        Args:
            pattern: 匹配模式，字符串包含判断（case-sensitive）。
            action: 执行动作，"allow" | "block" | "modify"。
            reason: 规则说明。
            modifier: 可选的修改回调函数。当 action 为 "modify" 时，
                该回调接收 messages 并返回修改后的 messages。
                对请求拦截，签名为 (list[dict]) -> list[dict]。
                对响应拦截，签名为 (dict) -> dict。
        """
        rule: dict[str, Any] = {
            "pattern": pattern,
            "action": action,
            "reason": reason,
        }
        if modifier is not None:
            rule["modifier"] = modifier
        self._rules.append(rule)

    async def intercept_request(self, messages: list[dict[str, Any]]) -> InterceptResult:
        """拦截 LLM 请求，返回 allow/block/modify。

        对 messages 中的 content 文本进行 pattern 匹配，
        命中则返回对应 action。若所有规则都未命中，返回 allow。

        Args:
            messages: LLM 请求消息列表，每条消息包含 role/content 等字段。

        Returns:
            InterceptResult，描述拦截结果。
        """
        for rule in self._rules:
            pattern = rule["pattern"]
            for msg in messages:
                content = msg.get("content", "")
                if isinstance(content, str) and pattern in content:
                    modified_data: dict[str, Any] | None = None
                    if rule["action"] == "modify":
                        modifier_fn = rule.get("modifier")
                        if modifier_fn is not None:
                            modified_msgs = modifier_fn(messages)
                        else:
                            modified_msgs = messages
                        modified_data = {"messages": modified_msgs}
                    result = InterceptResult(
                        action=rule["action"],
                        reason=rule["reason"],
                        modified=modified_data,
                    )
                    self._log.append({
                        "type": "request",
                        "action": result.action,
                        "reason": result.reason,
                        "pattern": pattern,
                    })
                    return result

        return InterceptResult(action="allow", reason="no rule matched")

    async def intercept_response(self, response: dict[str, Any]) -> InterceptResult:
        """拦截 LLM 响应，返回 allow/block/modify。

        对 response 的 content 字段进行 pattern 匹配，
        命中则返回对应 action。若所有规则都未命中，返回 allow。

        Args:
            response: LLM 响应字典，至少包含 content 字段。

        Returns:
            InterceptResult，描述拦截结果。
        """
        content = response.get("content", "")
        for rule in self._rules:
            pattern = rule["pattern"]
            if isinstance(content, str) and pattern in content:
                modified_data: dict[str, Any] | None = None
                if rule["action"] == "modify":
                    modifier_fn = rule.get("modifier")
                    if modifier_fn is not None:
                        modified_data = modifier_fn(response)
                    else:
                        modified_data = response
                result = InterceptResult(
                    action=rule["action"],
                    reason=rule["reason"],
                    modified=modified_data,
                )
                self._log.append({
                    "type": "response",
                    "action": result.action,
                    "reason": result.reason,
                    "pattern": pattern,
                })
                return result

        return InterceptResult(action="allow", reason="no rule matched")

    def get_log(self) -> list[dict[str, Any]]:
        """返回拦截日志，按时间顺序返回所有拦截记录。

        Returns:
            拦截日志列表，每条记录包含 type/action/reason/pattern。
        """
        return list(self._log)

    def clear_log(self) -> None:
        """清空拦截日志。"""
        self._log.clear()
