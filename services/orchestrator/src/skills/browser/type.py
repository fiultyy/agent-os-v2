"""
browser_type - 向输入框输入文本

使用 Playwright 执行真实的浏览器输入操作，支持：
- 清空后输入
- 追加模式
- 逐字符输入
"""

import logging
from typing import Dict, Any, Optional, List

logger = logging.getLogger(__name__)


def browser_type(
    selector: str,
    text: str,
    append: bool = False,
    clear_first: bool = True,
    type_speed: int = 0,
    modifiers: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """
    向输入框输入文本（Playwright 实现）

    Args:
        selector: CSS 选择器 (必填)
        text: 要输入的文本 (必填)
        append: 是否追加，默认 False
        clear_first: 输入前是否清空，默认 True
        type_speed: 打字速度（字符/秒），0=瞬时
        modifiers: 前置修饰键 (可选)

    Returns:
        {"success": bool, "selector": str, "chars_typed": int, "error": str}
    """
    result = {
        "success": False,
        "selector": selector,
        "text": text,
        "chars_typed": 0,
        "action": None,
        "error": None,
    }

    if not selector:
        result["error"] = "Selector is required"
        return result

    if not text:
        result["error"] = "Text is required"
        return result

    try:
        from ._browser import get_page

        page = get_page()
        loc = page.locator(selector).first

        if loc.count() == 0:
            result["error"] = f"Element not found: {selector}"
            return result

        # Focus the element
        loc.click()

        # Handle clear / append
        if clear_first and not append:
            # Select all + delete
            page.keyboard.press("Control+a")
            page.keyboard.press("Backspace")

        # Type text
        if type_speed > 0:
            delay = max(1, int(1000 / type_speed))  # ms per char
            loc.type(text, delay=delay)
        else:
            # Already cleared via Ctrl+A/Backspace when clear_first=True,
            # so skip redundant loc.fill("") — loc.type fires the proper events
            loc.type(text)

        result["chars_typed"] = len(text)

        actions = []
        if modifiers:
            actions.append(f"modifiers: {', '.join(modifiers)}")
        if clear_first and not append:
            actions.append("clear")
        actions.append(f"type_speed: {type_speed}cps" if type_speed > 0 else "instant")

        result["action"] = " | ".join(actions)
        result["success"] = True

    except Exception as e:
        result["error"] = f"Type error: {str(e)}"
        logger.warning(f"browser_type failed: {e}")

    return result
