"""
browser_snapshot - 页面快照

使用 Playwright 获取当前页面的真实 DOM 状态，包括：
- 可交互元素列表
- 元素文本、属性
- 页面 HTML（可选）
"""

import logging
from typing import Dict, Any, Optional

logger = logging.getLogger(__name__)


def browser_snapshot(
    selector: Optional[str] = None,
    max_elements: int = 50,
    include_html: bool = False,
) -> Dict[str, Any]:
    """
    获取页面快照（Playwright 实现）

    Args:
        selector: CSS 选择器，用于限定快照范围（可选）
        max_elements: 最大返回元素数，默认 50
        include_html: 是否包含 HTML 内容，默认 False

    Returns:
        {
            "success": bool,
            "elements": List[dict],
            "count": int,
            "html": str (if include_html=True),
            "error": str (if failed)
        }
    """
    result = {
        "success": False,
        "selector": selector,
        "elements": [],
        "count": 0,
        "html": None,
        "error": None,
    }

    try:
        from ._browser import get_page

        page = get_page()

        # 获取可交互元素
        js_script = """
        (args) => {
            const [selector, maxElements] = args;
            const root = selector ? document.querySelector(selector) : document;
            if (!root) return [];

            const interactable = root.querySelectorAll(
                'a, button, input, select, textarea, [role="button"], [role="link"], [onclick], [contenteditable="true"]'
            );

            const elements = [];
            for (let i = 0; i < Math.min(interactable.length, maxElements); i++) {
                const el = interactable[i];
                elements.push({
                    tag: el.tagName.toLowerCase(),
                    id: el.id || null,
                    class: el.className || null,
                    type: el.getAttribute('type') || null,
                    text: (el.textContent || '').trim().substring(0, 200),
                    href: el.getAttribute('href') || null,
                    placeholder: el.getAttribute('placeholder') || null,
                    clickable: true,
                });
            }
            return elements;
        }
        """

        elements = page.evaluate(js_script, [selector, max_elements])
        result["elements"] = elements
        result["count"] = len(elements)

        if include_html:
            if selector:
                loc = page.locator(selector).first
                result["html"] = loc.inner_html() if loc.count() > 0 else ""
            else:
                result["html"] = page.content()

        result["success"] = True

    except Exception as e:
        result["error"] = f"Snapshot error: {str(e)}"
        logger.warning(f"browser_snapshot failed: {e}")

    return result
