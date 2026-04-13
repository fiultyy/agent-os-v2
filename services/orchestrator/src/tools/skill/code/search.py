"""
code_search - 代码搜索

支持：
- 正则表达式搜索
- 文件类型过滤
- 上下文行返回
- 搜索结果高亮
"""

import re
from pathlib import Path
from typing import Dict, Any, List, Optional


def code_search(
    query: str,
    path: Optional[str] = None,
    pattern_type: str = "literal",
    file_filter: Optional[str] = None,
    case_sensitive: bool = False,
    context_lines: int = 0,
    max_results: int = 100,
    recursive: bool = True,
) -> Dict[str, Any]:
    """
    搜索代码文件
    
    Args:
        query: 搜索关键词 (必填)
        path: 搜索路径，默认 None 表示当前目录
        pattern_type: 匹配类型，"literal"(字面量)/"regex"(正则)/"word"(全词匹配)
        file_filter: 文件过滤器，如 "*.py", "*.js" (可选)
        case_sensitive: 是否区分大小写，默认 False
        context_lines: 上下文行数，默认 0
        max_results: 最大结果数，默认 100
        recursive: 是否递归搜索，默认 True
    
    Returns:
        {
            "success": bool,
            "query": str,
            "matches": List[dict],
            "total_matches": int,
            "files_searched": int,
            "error": str (if failed)
        }
    
    Example:
        >>> result = code_search("function main", path="/project/src", pattern_type="regex")
        >>> if result["success"]:
        ...     for match in result["matches"]:
        ...         print(f"{match['file']}:{match['line']}: {match['line_content']}")
        >>>
        >>> # 搜索 Python 文件
        >>> result = code_search("TODO", file_filter="*.py", context_lines=2)
    """
    result = {
        "success": False,
        "query": query,
        "matches": [],
        "total_matches": 0,
        "files_searched": 0,
        "error": None,
    }
    
    if not query:
        result["error"] = "Query is required"
        return result
    
    try:
        search_path = Path(path).expanduser().resolve() if path else Path.cwd()
        
        if not search_path.exists():
            result["error"] = f"Path not found: {search_path}"
            return result
        
        # 编译正则表达式
        flags = 0 if case_sensitive else re.IGNORECASE
        
        if pattern_type == "regex":
            try:
                regex = re.compile(query, flags)
            except re.error as e:
                result["error"] = f"Invalid regex: {e}"
                return result
        elif pattern_type == "word":
            regex = re.compile(r'\b' + re.escape(query) + r'\b', flags)
        else:  # literal
            regex = re.compile(re.escape(query), flags)
        
        matches = []
        files_searched = 0
        
        # 文件过滤器
        if file_filter:
            import fnmatch
            filter_patterns = [p.strip() for p in file_filter.split(",")]
        else:
            filter_patterns = None
        
        def should_include_file(file_path: Path) -> bool:
            if filter_patterns:
                name = file_path.name
                return any(fnmatch.fnmatch(name, p) for p in filter_patterns)
            return True
        
        # 搜索文件
        def search_file(file_path: Path):
            nonlocal files_searched
            files_searched += 1
            
            try:
                with open(file_path, "r", encoding="utf-8", errors="replace") as f:
                    lines = f.readlines()
                
                for line_num, line in enumerate(lines, start=1):
                    if regex.search(line):
                        match = {
                            "file": str(file_path),
                            "line": line_num,
                            "line_content": line.rstrip("\n\r"),
                        }
                        
                        # 上下文行
                        if context_lines > 0:
                            start = max(0, line_num - context_lines - 1)
                            end = min(len(lines), line_num + context_lines)
                            match["context"] = [
                                (i + 1, lines[i].rstrip("\n\r"))
                                for i in range(start, end)
                            ]
                        
                        matches.append(match)
                        
                        if len(matches) >= max_results:
                            return True  # 达到上限
                
                return False
                
            except (PermissionError, UnicodeDecodeError):
                return False  # 跳过无法读取的文件
        
        # 遍历文件
        if search_path.is_file():
            if should_include_file(search_path):
                search_file(search_path)
        else:
            for root, dirnames, filenames in os.walk(search_path):
                # 跳过隐藏目录和特定目录
                dirnames[:] = [d for d in dirnames if not d.startswith('.') and d not in ('__pycache__', 'node_modules', '.git')]
                
                for filename in filenames:
                    if filename.startswith('.'):
                        continue
                    
                    file_path = Path(root) / filename
                    
                    if not should_include_file(file_path):
                        continue
                    
                    if search_file(file_path):
                        break  # 达到上限
        
        result["matches"] = matches[:max_results]
        result["total_matches"] = len(matches)
        result["files_searched"] = files_searched
        result["success"] = True
        
    except Exception as e:
        result["error"] = f"Search error: {str(e)}"
    
    return result