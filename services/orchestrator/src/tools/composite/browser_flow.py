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
"""

from typing import Dict, Any, List, Optional

# 导入 browser skill 工具
from ...skills.browser.navigate import browser_navigate
from ...skills.browser.snapshot import browser_snapshot
from ...skills.browser.click import browser_click
from ...skills.browser.type import browser_type


def browser_flow_execute(
    url: str,
    actions: List[Dict[str, Any]],
    stop_on_error: bool = True,
    verbose: bool = False,
) -> Dict[str, Any]:
    """
    执行浏览器操作流程
    
    Args:
        url: 起始 URL (必填)
        actions: 操作序列 (必填)
            支持操作类型:
            - {op: 'navigate', url: '...'}
            - {op: 'snapshot', selector: '...', include_html: bool}
            - {op: 'click', selector: '...', button: 'left', click_count: 1}
            - {op: 'type', selector: '...', text: '...', clear_first: bool}
            - {op: 'wait', seconds: 1}
        stop_on_error: 遇到错误是否停止，默认 True
        verbose: 是否输出详细日志，默认 False
    
    Returns:
        {
            "success": bool,
            "url": str,
            "steps": List[dict],
            "step_count": int,
            "error": str (if failed)
        }
    
    Example:
        >>> result = browser_flow_execute(
        ...     url="https://example.com",
        ...     actions=[
        ...         {"op": "snapshot"},
        ...         {"op": "click", "selector": "#login-btn"},
        ...         {"op": "wait", "seconds": 1},
        ...         {"op": "type", "selector": "#username", "text": "admin"},
        ...     ]
        ... )
        >>> if result["success"]:
        ...     for step in result["steps"]:
        ...         print(f"{step['op']}: {step.get('status')}")
    """
    result = {
        "success": False,
        "url": url,
        "steps": [],
        "step_count": 0,
        "error": None,
    }
    
    if not url:
        result["error"] = "URL is required"
        return result
    
    if not actions:
        result["error"] = "Actions list is required"
        return result
    
    steps = []
    
    # 1. 起始导航
    nav_result = browser_navigate(url)
    steps.append({
        "op": "navigate",
        "url": url,
        "status": "success" if nav_result["success"] else "failed",
        "result": nav_result,
    })
    
    if not nav_result["success"] and stop_on_error:
        result["error"] = f"Navigation failed: {nav_result.get('error')}"
        result["steps"] = steps
        return result
    
    # 2. 执行操作序列
    for i, action in enumerate(actions):
        op = action.get("op", "")
        
        try:
            if op == "snapshot":
                step_result = browser_snapshot(
                    selector=action.get("selector"),
                    include_html=action.get("include_html", False),
                )
                steps.append({
                    "op": "snapshot",
                    "selector": action.get("selector"),
                    "status": "success" if step_result["success"] else "failed",
                    "count": step_result.get("count", 0),
                    "result": step_result,
                })
                
            elif op == "click":
                step_result = browser_click(
                    selector=action.get("selector"),
                    button=action.get("button", "left"),
                    click_count=action.get("click_count", 1),
                )
                steps.append({
                    "op": "click",
                    "selector": action.get("selector"),
                    "status": "success" if step_result["success"] else "failed",
                    "result": step_result,
                })
                
            elif op == "type":
                step_result = browser_type(
                    selector=action.get("selector"),
                    text=action.get("text", ""),
                    clear_first=action.get("clear_first", True),
                )
                steps.append({
                    "op": "type",
                    "selector": action.get("selector"),
                    "text_length": len(action.get("text", "")),
                    "status": "success" if step_result["success"] else "failed",
                    "result": step_result,
                })
                
            elif op == "wait":
                import time
                seconds = action.get("seconds", 1)
                time.sleep(seconds)
                steps.append({
                    "op": "wait",
                    "seconds": seconds,
                    "status": "success",
                })
                
            elif op == "navigate":
                # 导航到新 URL
                step_result = browser_navigate(action.get("url", ""))
                steps.append({
                    "op": "navigate",
                    "url": action.get("url"),
                    "status": "success" if step_result["success"] else "failed",
                    "result": step_result,
                })
                
                if not step_result["success"] and stop_on_error:
                    result["error"] = f"Navigation failed: {step_result.get('error')}"
                    break
            else:
                steps.append({
                    "op": op,
                    "status": "skipped",
                    "error": f"Unknown operation: {op}",
                })
                
        except Exception as e:
            steps.append({
                "op": op,
                "status": "error",
                "error": str(e),
            })
            if stop_on_error:
                result["error"] = f"Step {i} failed: {str(e)}"
                break
    
    result["steps"] = steps
    result["step_count"] = len(steps)
    result["success"] = all(s.get("status") == "success" for s in steps)
    
    return result


def browser_flow_snapshot_full(url: str) -> Dict[str, Any]:
    """
    获取页面完整快照（便捷函数）
    
    相当于 browser_flow_execute(url, [{"op": "snapshot"}])
    """
    nav_result = browser_navigate(url)
    if not nav_result["success"]:
        return nav_result
    
    return browser_snapshot()