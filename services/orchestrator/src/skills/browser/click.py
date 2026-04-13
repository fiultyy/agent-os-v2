"""
browser_click - 点击页面元素

使用 Playwright 执行真实点击操作。
"""

import logging
from typing import Dict, Any, Optional, List

logger = logging.getLogger(__name__)


def browser_click(
    selector: str,
    button: str = "left",
    click_count: int = 1,
    modifiers: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """
    点击页面元素（Playwright 实现）

    Args:
        selector: CSS 选择器 (必填)
        button: 鼠标按钮，"left"/"right"/"middle"，默认 "left"
        click_count: 点击次数，默认 1
        modifiers: 修饰键列表 (可选)

    Returns:
        {"success": bool, "selector": str, "action": str, "error": str}
    """
    result = {
        "success": False,
        "selector": selector,
        "element": None,
        "action": None,
        "error": None,
    }

    if not selector:
        result["error"] = "Selector is required"
        return result

    valid_buttons = ["left", "right", "middle"]
    if button not in valid_buttons:
        result["error"] = f"Invalid button: {button}. Must be one of {valid_buttons}"
        return result

    if click_count < 1 or click_count > 3:
        result["error"] = f"Invalid click_count: {click_count}. Must be 1-3"
        return result

    try:
        from ._browser import get_page

        page = get_page()
        loc = page.locator(selector).first

        if loc.count() == 0:
            result["error"] = f"Element not found: {selector}"
            return result

        # Build modifier list for Playwright
        pw_modifiers = []
        if modifiers:
            mod_map = {"Ctrl": "Control", "Cmd": "Meta", "Shift": "Shift", "Alt": "Alt"}
            pw_modifiers = [mod_map.get(m, m) for m in modifiers]

        loc.click(button=button, click_count=click_count, modifiers=pw_modifiers or None)

        # Read element info after click
        try:
            text = loc.text_content(timeout=1000) or ""
            tag = loc.evaluate("el => el.tagName.toLowerCase()")
            el_id = loc.get_attribute("id") or ""
            result["element"] = {
                "tag": tag,
                "id": el_id,
                "text": text[:200],
                "clickable": True,
            }
        except Exception:
            pass

        mod_str = f" with {'+'.join(modifiers)}" if modifiers else ""
        result["action"] = f"click{mod_str} ({button} button, {click_count}x)"
        result["success"] = True

    except Exception as e:
        result["error"] = f"Click error: {str(e)}"
        logger.warning(f"browser_click failed: {e}")

    return result
