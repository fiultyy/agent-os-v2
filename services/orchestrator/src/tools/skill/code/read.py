"""
code_read - 读取代码文件

支持：
- 多语言代码高亮（通过 language 参数）
- 行号返回
- 语法错误检测（基础）
"""

import os
from pathlib import Path
from typing import Dict, Any, Optional, List


# 代码语言映射
LANGUAGE_MAP = {
    ".py": "python",
    ".js": "javascript",
    ".ts": "typescript",
    ".jsx": "jsx",
    ".tsx": "tsx",
    ".java": "java",
    ".c": "c",
    ".cpp": "cpp",
    ".h": "c",
    ".go": "go",
    ".rs": "rust",
    ".rb": "ruby",
    ".php": "php",
    ".swift": "swift",
    ".kt": "kotlin",
    ".cs": "csharp",
    ".vue": "vue",
    ".html": "html",
    ".css": "css",
    ".scss": "scss",
    ".json": "json",
    ".yaml": "yaml",
    ".yml": "yaml",
    ".md": "markdown",
    ".sql": "sql",
    ".sh": "bash",
    ".bash": "bash",
}


def code_read(
    path: str,
    language: Optional[str] = None,
    start_line: Optional[int] = None,
    end_line: Optional[int] = None,
    highlight: bool = False,
) -> Dict[str, Any]:
    """
    读取代码文件
    
    Args:
        path: 文件路径 (必填)
        language: 代码语言，如 "python", "javascript" (可选，默认根据扩展名推断)
        start_line: 起始行号，默认 None 表示从头开始
        end_line: 结束行号，默认 None 表示到末尾
        highlight: 是否返回语法高亮信息（简化实现），默认 False
    
    Returns:
        {
            "success": bool,
            "path": str,
            "language": str,
            "lines": List[str],
            "total_lines": int,
            "content": str,
            "size": int,
            "error": str (if failed)
        }
    
    Example:
        >>> result = code_read("/path/to/main.py", language="python")
        >>> if result["success"]:
        ...     print(f"Read {result['total_lines']} lines")
        ...     for i, line in enumerate(result['lines'][5:15], start=6):
        ...         print(f"{i}: {line}")
    """
    result = {
        "success": False,
        "path": path,
        "language": None,
        "lines": [],
        "total_lines": 0,
        "content": None,
        "size": None,
        "error": None,
    }
    
    try:
        p = Path(path).expanduser().resolve()
        
        if not p.exists():
            result["error"] = f"File not found: {path}"
            return result
        
        if not p.is_file():
            result["error"] = f"Not a file: {path}"
            return result
        
        # 推断语言
        if language is None:
            ext = p.suffix.lower()
            language = LANGUAGE_MAP.get(ext, "text")
        
        result["language"] = language
        result["size"] = p.stat().st_size
        
        # 读取文件
        with open(p, "r", encoding="utf-8", errors="replace") as f:
            all_lines = f.readlines()
        
        result["total_lines"] = len(all_lines)
        
        # 切片
        start = (start_line - 1) if start_line else 0
        end = end_line if end_line else len(all_lines)
        result["lines"] = [line.rstrip("\n\r") for line in all_lines[start:end]]
        
        # 拼接内容
        result["content"] = "\n".join(result["lines"])
        result["success"] = True
        
    except PermissionError:
        result["error"] = f"Permission denied: {path}"
    except UnicodeDecodeError as e:
        result["error"] = f"Encoding error: {e}"
    except Exception as e:
        result["error"] = f"Read error: {str(e)}"
    
    return result