"""
_browser — Shared Playwright browser context manager.

Provides a lazily-initialised, singleton-style Playwright browser instance
that is shared across all browser skill tools (navigate, snapshot, click, type).

Usage:
    from ._browser import get_page, cleanup

    page = get_page()        # returns a Playwright Page
    cleanup()                # close browser on shutdown
"""

from __future__ import annotations

import logging
import threading
from typing import Optional

from playwright.sync_api import sync_playwright, Page, Browser, Playwright

logger = logging.getLogger(__name__)

_lock = threading.Lock()
_playwright: Optional[Playwright] = None
_browser: Optional[Browser] = None
_page: Optional[Page] = None


def _ensure() -> tuple[Playwright, Browser, Page]:
    """Initialise Playwright + Chromium (headless) if not yet started."""
    global _playwright, _browser, _page
    if _page is None or _page.is_closed():
        with _lock:
            # double-check after acquiring lock
            if _page is None or _page.is_closed():
                _playwright = sync_playwright().start()
                _browser = _playwright.chromium.launch(headless=True)
                ctx = _browser.new_context(
                    user_agent="Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                               "(KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36",
                    viewport={"width": 1280, "height": 720},
                )
                _page = ctx.new_page()
                logger.info("Playwright browser initialised (headless Chromium)")
    return _playwright, _browser, _page


def get_page() -> Page:
    """Return the shared Playwright Page, creating one if needed."""
    _, _, page = _ensure()
    return page


def cleanup() -> None:
    """Close the shared browser and Playwright instance."""
    global _playwright, _browser, _page
    with _lock:
        if _browser is not None:
            try:
                _browser.close()
            except Exception:
                pass
        if _playwright is not None:
            try:
                _playwright.stop()
            except Exception:
                pass
        _browser = None
        _page = None
        _playwright = None
