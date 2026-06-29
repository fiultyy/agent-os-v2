"""
code_write - 写入代码文件

支持：
- 自动创建目录
- 备份现有文件
- 原子写入（先写临时文件再重命名）
"""

import shutil
from pathlib import Path
from typing import Dict, Any, Optional
from datetime import datetime


def code_write(
    path: str,
    content: str,
    language: Optional[str] = None,
    backup: bool = False,
    atomic: bool = True,
    encoding: str = "utf-8",
) -> Dict[str, Any]:
    """
    写入代码文件
    
    Args:
        path: 文件路径 (必填)
        content: 代码内容 (必填)
        language: 代码语言（用于扩展名推断，可选）
        backup: 是否备份现有文件，默认 False
        atomic: 是否使用原子写入（先写临时文件再重命名），默认 True
        encoding: 文件编码，默认 utf-8
    
    Returns:
        {
            "success": bool,
            "path": str,
            "bytes_written": int,
            "backup_path": str (if backup=True and file existed),
            "error": str (if failed)
        }
    
    Example:
        >>> result = code_write("/path/to/new_file.py", "print('hello')", language="python")
        >>> if result["success"]:
        ...     print(f"Wrote {result['bytes_written']} bytes")
        >>>
        >>> # 带备份
        >>> result = code_write("/path/to/existing.py", new_content, backup=True)
        >>> if result["backup_path"]:
        ...     print(f"Backup: {result['backup_path']}")
    """
    result = {
        "success": False,
        "path": path,
        "bytes_written": None,
        "backup_path": None,
        "error": None,
    }
    
    try:
        p = Path(path).expanduser().resolve()
        
        # 确保父目录存在
        p.parent.mkdir(parents=True, exist_ok=True)
        
        # 备份现有文件
        if backup and p.exists():
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            backup_path = p.parent / f"{p.stem}_backup_{timestamp}{p.suffix}"
            shutil.copy2(p, backup_path)
            result["backup_path"] = str(backup_path)
        
        # 写入文件
        if atomic:
            # 原子写入：先写临时文件再重命名
            temp_path = p.with_suffix(p.suffix + ".tmp")
            try:
                with open(temp_path, "w", encoding=encoding) as f:
                    bytes_written = f.write(content)
                temp_path.rename(p)
            finally:
                if temp_path.exists():
                    temp_path.unlink()
        else:
            # 直接写入
            with open(p, "w", encoding=encoding) as f:
                bytes_written = f.write(content)
        
        result["bytes_written"] = bytes_written
        result["success"] = True
        
    except PermissionError:
        result["error"] = f"Permission denied: {path}"
    except OSError as e:
        result["error"] = f"IO error: {str(e)}"
    except Exception as e:
        result["error"] = f"Write error: {str(e)}"
    
    return result