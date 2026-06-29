"""
File Tool - L3.3 Primitive Implementation
提供 6 个文件操作工具：read/write/delete/exists/list/mkdir

使用 Python os/shutil 标准库实现，支持：
- 路径规范化和安全性检查
- 递归目录操作
- 文件类型判断
"""

import logging
import os
import shutil
from pathlib import Path
from typing import Dict, Any, Optional

logger = logging.getLogger(__name__)


def _safe_path(path: str, base: Optional[str] = None) -> Path:
    """
    安全路径解析，防止路径遍历攻击

    Args:
        path: 输入路径
        base: 基准目录，默认 None 表示不限（但仍禁止 .. 遍历）

    Returns:
        解析后的 Path 对象

    Raises:
        ValueError: 如果路径不安全
    """
    # 先规范化但不复原 ..，用于检测 .. 遍历
    raw = Path(path).expanduser()

    # 显式禁止 .. 路径组件
    for part in raw.parts:
        if part == "..":
            raise ValueError(f"Path traversal '..' is not allowed in '{path}'")

    # 如果有 base，限制在 base 目录内
    if base:
        base_path = Path(base).expanduser().resolve()
        p = raw.resolve()
        try:
            p.relative_to(base_path)
        except ValueError:
            raise ValueError(f"Path '{path}' is outside base directory '{base}'")
    else:
        p = raw.resolve()

    return p


def file_read(
    path: str,
    encoding: str = "utf-8",
    limit: Optional[int] = None,
) -> Dict[str, Any]:
    """
    读取文件内容
    
    Args:
        path: 文件路径 (必填)
        encoding: 文件编码，默认 utf-8
        limit: 最大读取字节数，默认 None 表示全部
    
    Returns:
        {
            "success": bool,
            "path": str,
            "content": str,
            "size": int,
            "error": str (if failed)
        }
    
    Example:
        >>> result = file_read("/tmp/test.txt")
        >>> if result["success"]:
        ...     print(result["content"])
    """
    result = {
        "success": False,
        "path": path,
        "content": None,
        "size": None,
        "error": None,
    }
    
    try:
        p = _safe_path(path)
        
        if not p.exists():
            result["error"] = f"File not found: {path}"
            return result
        
        if not p.is_file():
            result["error"] = f"Not a file: {path}"
            return result
        
        with open(p, "r", encoding=encoding) as f:
            if limit:
                content = f.read(limit)
            else:
                content = f.read()
        
        result["content"] = content
        result["size"] = p.stat().st_size
        result["success"] = True
        
    except UnicodeDecodeError as e:
        result["error"] = f"Encoding error: {e}"
    except PermissionError:
        result["error"] = f"Permission denied: {path}"
    except Exception as e:
        result["error"] = f"Error: {str(e)}"
    
    return result


def file_write(
    path: str,
    content: str,
    encoding: str = "utf-8",
    mode: str = "w",
) -> Dict[str, Any]:
    """
    写入文件内容
    
    Args:
        path: 文件路径 (必填)
        content: 写入内容 (必填)
        encoding: 文件编码，默认 utf-8
        mode: 写入模式，默认 'w' (覆盖)，'a' 表示追加
    
    Returns:
        {
            "success": bool,
            "path": str,
            "bytes_written": int,
            "error": str (if failed)
        }
    
    Example:
        >>> result = file_write("/tmp/test.txt", "Hello, World!")
        >>> if result["success"]:
        ...     print(f"Wrote {result['bytes_written']} bytes")
    """
    result = {
        "success": False,
        "path": path,
        "bytes_written": None,
        "error": None,
    }
    
    try:
        p = _safe_path(path)
        
        # 确保父目录存在
        p.parent.mkdir(parents=True, exist_ok=True)
        
        with open(p, mode, encoding=encoding) as f:
            bytes_written = f.write(content)
        
        result["bytes_written"] = bytes_written
        result["success"] = True
        
    except PermissionError:
        result["error"] = f"Permission denied: {path}"
    except Exception as e:
        result["error"] = f"Error: {str(e)}"
    
    return result


def file_delete(path: str, recursive: bool = False) -> Dict[str, Any]:
    """
    删除文件或目录
    
    Args:
        path: 路径 (必填)
        recursive: 是否递归删除目录，默认 False
    
    Returns:
        {
            "success": bool,
            "path": str,
            "error": str (if failed)
        }
    
    Example:
        >>> result = file_delete("/tmp/test.txt")
        >>> result = file_delete("/tmp/dir", recursive=True)
    """
    result = {
        "success": False,
        "path": path,
        "error": None,
    }
    
    try:
        p = _safe_path(path)
        
        if not p.exists():
            result["error"] = f"Path not found: {path}"
            return result
        
        if p.is_dir():
            if recursive:
                shutil.rmtree(p)
            else:
                result["error"] = f"Is a directory (use recursive=True): {path}"
                return result
        else:
            p.unlink()
        
        result["success"] = True
        
    except PermissionError:
        result["error"] = f"Permission denied: {path}"
    except Exception as e:
        result["error"] = f"Error: {str(e)}"
    
    return result


def file_exists(path: str) -> Dict[str, Any]:
    """
    检查文件/目录是否存在
    
    Args:
        path: 路径 (必填)
    
    Returns:
        {
            "exists": bool,
            "path": str,
            "is_file": bool,
            "is_dir": bool,
            "size": int (if file),
            "error": str (if failed)
        }
    
    Example:
        >>> result = file_exists("/tmp/test.txt")
        >>> if result["exists"] and result["is_file"]:
        ...     print(f"Size: {result['size']}")
    """
    result = {
        "exists": False,
        "path": path,
        "is_file": False,
        "is_dir": False,
        "size": None,
        "error": None,
    }
    
    try:
        p = _safe_path(path)
        result["exists"] = p.exists()
        
        if result["exists"]:
            result["is_file"] = p.is_file()
            result["is_dir"] = p.is_dir()
            if result["is_file"]:
                result["size"] = p.stat().st_size
                
    except Exception as e:
        result["error"] = f"Error: {str(e)}"
    
    return result


def file_list(
    path: str,
    pattern: str = "*",
    recursive: bool = False,
    max_depth: int = 10,
) -> Dict[str, Any]:
    """
    列出目录内容
    
    Args:
        path: 目录路径 (必填)
        pattern: 文件名匹配模式，默认 "*" 表示全部
        recursive: 是否递归，默认 False
        max_depth: 最大递归深度，默认 10（仅 recursive=True 时生效）
    
    Returns:
        {
            "success": bool,
            "path": str,
            "count": int,
            "files": List[str],
            "dirs": List[str],
            "error": str (if failed)
        }
    
    Example:
        >>> result = file_list("/tmp", pattern="*.txt")
        >>> if result["success"]:
        ...     print(f"Found {result['count']} files")
    """
    result = {
        "success": False,
        "path": path,
        "count": 0,
        "files": [],
        "dirs": [],
        "error": None,
    }
    
    try:
        p = _safe_path(path)
        
        if not p.exists():
            result["error"] = f"Path not found: {path}"
            return result
        
        if not p.is_dir():
            result["error"] = f"Not a directory: {path}"
            return result
        
        files = []
        dirs = []
        
        if recursive:
            for root, dirnames, filenames in os.walk(p):
                # Calculate current depth relative to starting path
                try:
                    depth = len(Path(root).relative_to(p).parts)
                except ValueError:
                    depth = 0
                if depth >= max_depth:
                    dirnames.clear()  # Don't recurse deeper
                    continue
                root_path = Path(root)
                for name in filenames:
                    if name_match := _match_pattern(name, pattern):
                        files.append(str(root_path / name))
                for name in dirnames:
                    if name_match := _match_pattern(name, pattern):
                        dirs.append(str(root_path / name))
        else:
            for item in p.iterdir():
                if name_matches := _match_pattern(item.name, pattern):
                    if item.is_file():
                        files.append(str(item))
                    elif item.is_dir():
                        dirs.append(str(item))
        
        result["files"] = sorted(files)
        result["dirs"] = sorted(dirs)
        result["count"] = len(result["files"]) + len(result["dirs"])
        result["success"] = True
        
    except PermissionError:
        result["error"] = f"Permission denied: {path}"
    except Exception as e:
        result["error"] = f"Error: {str(e)}"
    
    return result


def _match_pattern(name: str, pattern: str) -> bool:
    """简单的文件名匹配（支持 * 和 ?）"""
    import fnmatch
    return fnmatch.fnmatch(name, pattern)


def file_mkdir(
    path: str,
    parents: bool = True,
    exist_ok: bool = True,
) -> Dict[str, Any]:
    """
    创建目录
    
    Args:
        path: 目录路径 (必填)
        parents: 是否创建父目录，默认 True
        exist_ok: 目录已存在是否报错，默认 True 不报错
    
    Returns:
        {
            "success": bool,
            "path": str,
            "created": bool,
            "error": str (if failed)
        }
    
    Example:
        >>> result = file_mkdir("/tmp/nested/dir")
    """
    result = {
        "success": False,
        "path": path,
        "created": False,
        "error": None,
    }
    
    try:
        p = _safe_path(path)
        
        if p.exists():
            if p.is_dir():
                result["created"] = False
                result["success"] = True
            else:
                result["error"] = f"Path exists but is not a directory: {path}"
        else:
            p.mkdir(parents=parents, exist_ok=exist_ok)
            result["created"] = True
            result["success"] = True
        
    except PermissionError:
        result["error"] = f"Permission denied: {path}"
    except Exception as e:
        result["error"] = f"Error: {str(e)}"
    
    return result
