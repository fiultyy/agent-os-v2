"""
browser_navigate - 导航到指定 URL

使用 Playwright 进行真实浏览器导航，支持：
- URL 验证
- 页面加载等待
- 页面标题获取
"""

import re
import logging
from typing import Dict, Any

logger = logging.getLogger(__name__)


def browser_navigate(
    url: str,
    reload: bool = False,
    timeout: int = 30,
) -> Dict[str, Any]:
    """
    导航到指定 URL（Playwright 实现）

    Args:
        url: 目标 URL (必填)
        reload: 是否强制刷新，默认 False
        timeout: 超时秒数，默认 30

    Returns:
        {
            "success": bool,
            "url": str,
            "status": str,
            "title": str,
            "error": str (if failed)
        }
    """
    result = {
        "success": False,
        "url": url,
        "status": "unknown",
        "title": None,
        "error": None,
    }

    url_pattern = re.compile(
        r'^(?:http|https)://'
        r'(?:(?:[A-Z0-9](?:[A-Z0-9-]{0,61}[A-Z0-9])?\.)+[A-Z]{2,6}\.?|'
        r'localhost|'
        r'\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3})'
        r'(?::\d+)?'
        r'(?:/?|[/?]\S+)$', re.IGNORECASE
    )

    if not url_pattern.match(url):
        result["error"] = f"Invalid URL format: {url}"
        return result

    try:
        from ._browser import get_page

        page = get_page()

        if reload and page.url == url:
            page.reload(timeout=timeout * 1000)
        else:
            page.goto(url, timeout=timeout * 1000, wait_until="domcontentloaded")

        result["status"] = "loaded"
        result["title"] = page.title()
        result["url"] = page.url
        result["success"] = True

    except Exception as e:
        result["status"] = "error"
        result["error"] = f"Navigation error: {str(e)}"
        logger.warning(f"browser_navigate failed: {e}")

    return result
