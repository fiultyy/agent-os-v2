"""
browser_flow - 浏览器操作流程编排

组合 browser skill 工具实现完整的浏览器操作流程：
- navigate + snapshot + click + type

支持操作序列：
- {op: 'navigate', url: '...'}
- {op: 'snapshot', selector: '...'}
- {op: 'click', selector: '...'}
- {op: 'type', selector: '...', text: '...'}
- {op: 'wait', seconds: 1}

sync/async 共享核心逻辑，消除重复代码。
"""

import asyncio
import time
from typing import Dict, Any, List, Optional, Callable, Awaitable

# 导入 browser skill 工具
from ...skills.browser.navigate import browser_navigate
from ...skills.browser.snapshot import browser_snapshot
from ...skills.browser.click import browser_click
from ...skills.browser.type import browser_type


# ── Core shared logic ───────────────────────────────────────────────

def _init_result(url: str) -> Dict[str, Any]:
    """Create the initial result dict."""
    return {
        "success": False,
        "url": url,
        "steps": [],
        "step_count": 0,
        "error": None,
    }


def _validate(url: str, actions: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """Validate inputs. Returns an error result dict if invalid, else None."""
    if not url:
        return {"success": False, "url": url, "steps": [], "step_count": 0, "error": "URL is required"}
    if not actions:
        return {"success": False, "url": url, "steps": [], "step_count": 0, "error": "Actions list is required"}
    return None


def _execute_step(action: Dict[str, Any]) -> Dict[str, Any]:
    """Execute a single action and return a step dict.

    Returns {"op": str, "status": str, ...} with action-specific fields.
    Status is one of: "success", "failed", "skipped", "error".
    """
    op = action.get("op", "")

    if op == "snapshot":
        step_result = browser_snapshot(
            selector=action.get("selector"),
            include_html=action.get("include_html", False),
        )
        return {
            "op": "snapshot",
            "selector": action.get("selector"),
            "status": "success" if step_result["success"] else "failed",
            "count": step_result.get("count", 0),
            "result": step_result,
        }

    elif op == "click":
        step_result = browser_click(
            selector=action.get("selector"),
            button=action.get("button", "left"),
            click_count=action.get("click_count", 1),
        )
        return {
            "op": "click",
            "selector": action.get("selector"),
            "status": "success" if step_result["success"] else "failed",
            "result": step_result,
        }

    elif op == "type":
        step_result = browser_type(
            selector=action.get("selector"),
            text=action.get("text", ""),
            clear_first=action.get("clear_first", True),
        )
        return {
            "op": "type",
            "selector": action.get("selector"),
            "text_length": len(action.get("text", "")),
            "status": "success" if step_result["success"] else "failed",
            "result": step_result,
        }

    elif op == "navigate":
        step_result = browser_navigate(action.get("url", ""))
        return {
            "op": "navigate",
            "url": action.get("url"),
            "status": "success" if step_result["success"] else "failed",
            "result": step_result,
        }

    elif op == "wait":
        # wait is handled by the caller (sync vs async differ here)
        return {
            "op": "wait",
            "seconds": action.get("seconds", 1),
            "status": "success",
            "_needs_sleep": True,
        }

    else:
        return {
            "op": op,
            "status": "skipped",
            "error": f"Unknown operation: {op}",
        }


def _finalize(result: Dict[str, Any], steps: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Set steps and compute success flag."""
    # Strip internal markers
    clean_steps = []
    for s in steps:
        step = {k: v for k, v in s.items() if not k.startswith("_")}
        clean_steps.append(step)
    result["steps"] = clean_steps
    result["step_count"] = len(clean_steps)
    result["success"] = all(s.get("status") == "success" for s in clean_steps)
    return result


# ── Public API ──────────────────────────────────────────────────────

def browser_flow_execute(
    url: str,
    actions: List[Dict[str, Any]],
    stop_on_error: bool = True,
    verbose: bool = False,
) -> Dict[str, Any]:
    """
    执行浏览器操作流程（同步版本）

    Args:
        url: 起始 URL (必填)
        actions: 操作序列 (必填)
        stop_on_error: 遇到错误是否停止，默认 True
        verbose: 是否输出详细日志，默认 False

    Returns:
        {"success": bool, "url": str, "steps": list, "step_count": int, "error": str|None}
    """
    # Validate
    err = _validate(url, actions)
    if err:
        return err

    result = _init_result(url)
    steps: List[Dict[str, Any]] = []

    # 1. Initial navigation
    nav_result = browser_navigate(url)
    steps.append({
        "op": "navigate",
        "url": url,
        "status": "success" if nav_result["success"] else "failed",
        "result": nav_result,
    })
    if not nav_result["success"] and stop_on_error:
        result["error"] = f"Navigation failed: {nav_result.get('error')}"
        return _finalize(result, steps)

    # 2. Execute action sequence
    for i, action in enumerate(actions):
        try:
            step = _execute_step(action)

            # Handle wait (sync)
            if step.get("_needs_sleep"):
                time.sleep(action.get("seconds", 1))

            steps.append(step)

            # Check stop-on-error for navigate failures
            if step["op"] == "navigate" and step["status"] == "failed" and stop_on_error:
                result["error"] = f"Navigation failed: {step.get('result', {}).get('error')}"
                break

        except Exception as e:
            steps.append({"op": action.get("op", ""), "status": "error", "error": str(e)})
            if stop_on_error:
                result["error"] = f"Step {i} failed: {str(e)}"
                break

    return _finalize(result, steps)


async def browser_flow_execute_async(
    url: str,
    actions: List[Dict[str, Any]],
    stop_on_error: bool = True,
    verbose: bool = False,
) -> Dict[str, Any]:
    """
    异步执行浏览器操作流程

    与 sync 版本共享核心逻辑，仅 wait 使用 asyncio.sleep。
    """
    err = _validate(url, actions)
    if err:
        return err

    result = _init_result(url)
    steps: List[Dict[str, Any]] = []

    # 1. Initial navigation (browser_navigate is sync — runs in thread)
    nav_result = await asyncio.to_thread(browser_navigate, url)
    steps.append({
        "op": "navigate",
        "url": url,
        "status": "success" if nav_result["success"] else "failed",
        "result": nav_result,
    })
    if not nav_result["success"] and stop_on_error:
        result["error"] = f"Navigation failed: {nav_result.get('error')}"
        return _finalize(result, steps)

    # 2. Execute action sequence
    for i, action in enumerate(actions):
        try:
            step = await asyncio.to_thread(_execute_step, action)

            # Handle wait (async)
            if step.get("_needs_sleep"):
                await asyncio.sleep(action.get("seconds", 1))

            steps.append(step)

            if step["op"] == "navigate" and step["status"] == "failed" and stop_on_error:
                result["error"] = f"Navigation failed: {step.get('result', {}).get('error')}"
                break

        except Exception as e:
            steps.append({"op": action.get("op", ""), "status": "error", "error": str(e)})
            if stop_on_error:
                result["error"] = f"Step {i} failed: {str(e)}"
                break

    return _finalize(result, steps)


def browser_flow_snapshot_full(url: str) -> Dict[str, Any]:
    """获取页面完整快照（便捷函数）"""
    nav_result = browser_navigate(url)
    if not nav_result["success"]:
        return {
            "success": False,
            "error": nav_result.get("error", "Navigation failed"),
            "step": "navigate",
        }
    snapshot_result = browser_snapshot()
    snapshot_result["step"] = "snapshot"
    return snapshot_result
