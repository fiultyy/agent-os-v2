"""
Browser Skill - L3.3 Implementation
浏览器操作技能：navigate、snapshot、click、type

基于 Playwright 或 Selenium 风格的浏览器自动化接口。
实现为独立工具，不依赖外部浏览器驱动。
"""

from .navigate import browser_navigate
from .snapshot import browser_snapshot
from .click import browser_click
from .type import browser_type

__all__ = [
    "browser_navigate",
    "browser_snapshot",
    "browser_click",
    "browser_type",
]