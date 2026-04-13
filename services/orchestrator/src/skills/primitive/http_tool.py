"""
HTTP Tool - L3.3 Primitive Implementation
提供 5 个 HTTP 方法工具：GET/POST/PUT/DELETE/PATCH

使用 Python urllib 实现，支持：
- 请求头配置
- 超时控制
- JSON 响应解析
- 错误处理
"""

import urllib.request
import urllib.error
import json
from typing import Dict, Any, Optional


def _build_request(
    url: str,
    method: str,
    headers: Optional[Dict[str, str]] = None,
    body: Optional[str] = None,
    timeout: int = 30,
) -> Dict[str, Any]:
    """
    通用 HTTP 请求构建器
    
    Args:
        url: 目标 URL
        method: HTTP 方法
        headers: 请求头
        body: 请求体
        timeout: 超时秒数
    
    Returns:
        结果字典，包含 success, status, headers, body, error
    """
    if headers is None:
        headers = {}
    
    # 设置默认 Content-Type
    if body and "Content-Type" not in headers:
        headers["Content-Type"] = "application/json"
    
    result = {
        "success": False,
        "url": url,
        "method": method,
        "status": None,
        "headers": {},
        "body": None,
        "error": None,
    }
    
    try:
        req = urllib.request.Request(url, data=body.encode("utf-8") if body else None)
        req.get_method = lambda: method
        
        for key, value in headers.items():
            req.add_header(key, value)
        
        with urllib.request.urlopen(req, timeout=timeout) as response:
            result["status"] = response.status
            result["headers"] = dict(response.headers)
            result["body"] = response.read().decode("utf-8")
            result["success"] = True
            
            # 尝试解析 JSON
            try:
                result["json"] = json.loads(result["body"])
            except (json.JSONDecodeError, UnicodeDecodeError):
                pass
                
    except urllib.error.HTTPError as e:
        result["error"] = f"HTTPError: {e.code} {e.reason}"
        result["status"] = e.code
        try:
            result["body"] = e.read().decode("utf-8")
        except:
            result["body"] = None
    except urllib.error.URLError as e:
        result["error"] = f"URLError: {e.reason}"
    except Exception as e:
        result["error"] = f"Error: {str(e)}"
    
    return result


def http_get(
    url: str,
    headers: Optional[Dict[str, str]] = None,
    timeout: int = 30,
) -> Dict[str, Any]:
    """
    HTTP GET 请求
    
    Args:
        url: 目标 URL (必填)
        headers: 请求头字典 (可选)
        timeout: 超时秒数，默认 30
    
    Returns:
        {
            "success": bool,
            "status": int,
            "headers": dict,
            "body": str,
            "json": object (if parseable),
            "error": str (if failed)
        }
    
    Example:
        >>> result = http_get("https://api.example.com/data")
        >>> if result["success"]:
        ...     print(result["json"])
    """
    return _build_request(url, "GET", headers=headers, timeout=timeout)


def http_post(
    url: str,
    body: str = "",
    headers: Optional[Dict[str, str]] = None,
    timeout: int = 30,
) -> Dict[str, Any]:
    """
    HTTP POST 请求
    
    Args:
        url: 目标 URL (必填)
        body: 请求体，默认空字符串
        headers: 请求头字典 (可选)
        timeout: 超时秒数，默认 30
    
    Returns:
        {
            "success": bool,
            "status": int,
            "headers": dict,
            "body": str,
            "json": object (if parseable),
            "error": str (if failed)
        }
    
    Example:
        >>> result = http_post(
        ...     "https://api.example.com/data",
        ...     body=json.dumps({"key": "value"})
        ... )
    """
    return _build_request(url, "POST", headers=headers, body=body, timeout=timeout)


def http_put(
    url: str,
    body: str = "",
    headers: Optional[Dict[str, str]] = None,
    timeout: int = 30,
) -> Dict[str, Any]:
    """
    HTTP PUT 请求
    
    Args:
        url: 目标 URL (必填)
        body: 请求体，默认空字符串
        headers: 请求头字典 (可选)
        timeout: 超时秒数，默认 30
    
    Returns:
        {
            "success": bool,
            "status": int,
            "headers": dict,
            "body": str,
            "json": object (if parseable),
            "error": str (if failed)
        }
    
    Example:
        >>> result = http_put(
        ...     "https://api.example.com/data/1",
        ...     body=json.dumps({"key": "new_value"})
        ... )
    """
    return _build_request(url, "PUT", headers=headers, body=body, timeout=timeout)


def http_delete(
    url: str,
    headers: Optional[Dict[str, str]] = None,
    timeout: int = 30,
) -> Dict[str, Any]:
    """
    HTTP DELETE 请求
    
    Args:
        url: 目标 URL (必填)
        headers: 请求头字典 (可选)
        timeout: 超时秒数，默认 30
    
    Returns:
        {
            "success": bool,
            "status": int,
            "headers": dict,
            "body": str,
            "json": object (if parseable),
            "error": str (if failed)
        }
    
    Example:
        >>> result = http_delete("https://api.example.com/data/1")
    """
    return _build_request(url, "DELETE", headers=headers, timeout=timeout)


def http_patch(
    url: str,
    body: str = "",
    headers: Optional[Dict[str, str]] = None,
    timeout: int = 30,
) -> Dict[str, Any]:
    """
    HTTP PATCH 请求
    
    Args:
        url: 目标 URL (必填)
        body: 请求体，默认空字符串
        headers: 请求头字典 (可选)
        timeout: 超时秒数，默认 30
    
    Returns:
        {
            "success": bool,
            "status": int,
            "headers": dict,
            "body": str,
            "json": object (if parseable),
            "error": str (if failed)
        }
    
    Example:
        >>> result = http_patch(
        ...     "https://api.example.com/data/1",
        ...     body=json.dumps({"key": "patched_value"})
        ... )
    """
    return _build_request(url, "PATCH", headers=headers, body=body, timeout=timeout)
