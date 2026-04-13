"""
code_review - 代码审查工作流

组合 code skill 工具实现完整的代码审查流程：
- code_read → code_search → 分析 → 输出报告

支持检查项：
- 安全性（SQL注入、XSS、密码硬编码）
- 性能（N+1查询、循环内IO）
- 代码质量（重复代码、过长函数）
- 最佳实践（错误处理、日志）
"""

import re
from pathlib import Path
from typing import Dict, Any, List, Optional


def _find_python_files(path: str) -> List[str]:
    """递归查找目录下所有 .py 文件"""
    p = Path(path)
    if p.is_file():
        return [str(p)]
    return [str(f) for f in p.rglob("*.py")]


def _build_file_contents(path: str) -> Dict[str, str]:
    """一次性读取所有文件内容到内存"""
    file_contents = {}
    for file_path in _find_python_files(path):
        try:
            with open(file_path, "r", encoding="utf-8") as f:
                file_contents[file_path] = f.read()
        except Exception:
            continue
    return file_contents


def _search_in_memory(
    file_contents: Dict[str, str],
    pattern: str,
    max_results: int = 50,
) -> List[Dict[str, Any]]:
    """在内存中的文件内容上执行正则搜索"""
    matches = []
    for file_path, content in file_contents.items():
        for match in re.finditer(pattern, content, re.MULTILINE):
            line_num = content[:match.start()].count("\n") + 1
            line_start = content.rfind("\n", 0, match.start()) + 1
            line_end = content.find("\n", match.end())
            line_content = content[line_start:line_end].strip()
            matches.append({
                "file": file_path,
                "line": line_num,
                "line_content": line_content,
                "match_start": match.start(),
            })
            if len(matches) >= max_results:
                break
        if len(matches) >= max_results:
            break
    return matches


# 安全检查模式
SECURITY_PATTERNS = [
    (r"password\s*=\s*['\"][^'\"]{1,}", "Password hardcoded"),
    (r"eval\s*\(", "Dangerous eval() usage"),
    (r"exec\s*\(", "Dangerous exec() usage"),
    (r"SELECT\s+\*\s+FROM", "SELECT * may cause performance issue"),
    (r"input\s*\.\s*value", "Potential XSS via input.value"),
    (r"innerHTML\s*=", "Potential XSS via innerHTML"),
    (r"\.format\s*\([^)]{100,}\)", "Format string too long"),
]

# 性能检查模式
PERFORMANCE_PATTERNS = [
    (r"for\s+.*\nin\s+.*\.query", "Potential N+1 query"),
    (r"for\s+.*\nin\s+.*\.all\(\)", "Loading all records in loop"),
    (r"sleep\s*\(\s*\)\s*#", "Suspicious sleep in loop"),
]

# 代码质量检查模式
QUALITY_PATTERNS = [
    (r"def\s+\w+\([^)]{200,}\)", "Function has too many parameters"),
    (r"if\s+.*:\s*$", "Incomplete if statement"),
    (r"except\s*:", "Bare except clause"),
    (r"print\s*\(", "Debug print statement"),
]


def code_review_run(
    path: str,
    check_types: Optional[List[str]] = None,
    language: Optional[str] = None,
    context_lines: int = 3,
    max_issues: int = 50,
) -> Dict[str, Any]:
    """
    执行代码审查
    
    Args:
        path: 代码文件或目录路径 (必填)
        check_types: 检查类型列表，默认 ["security", "performance", "quality"]
            - "security": 安全检查
            - "performance": 性能检查
            - "quality": 代码质量检查
            - "all": 全部检查
        language: 代码语言（可选，自动推断）
        context_lines: 上下文行数，默认 3
        max_issues: 最大报告问题数，默认 50
    
    Returns:
        {
            "success": bool,
            "path": str,
            "language": str,
            "issues": List[dict],
            "issue_count": int,
            "summary": dict,
            "error": str (if failed)
        }
    
    Example:
        >>> result = code_review_run("/project/src/main.py")
        >>> if result["success"]:
        ...     for issue in result["issues"]:
        ...         print(f"[{issue['severity']}] {issue['message']} @ {issue['file']}:{issue['line']}")
        ...     print(f"Summary: {result['summary']}")
    """
    result = {
        "success": False,
        "path": path,
        "language": None,
        "issues": [],
        "issue_count": 0,
        "summary": {},
        "error": None,
    }
    
    if not path:
        result["error"] = "Path is required"
        return result
    
    # 默认检查类型
    if check_types is None:
        check_types = ["security", "performance", "quality"]
    elif "all" in check_types:
        check_types = ["security", "performance", "quality"]
    
    try:
        # 收集问题
        issues = []

        # 一次性读取所有文件内容
        file_contents = _build_file_contents(path)
        if not file_contents:
            result["error"] = "No Python files found"
            return result

        # 统计总行数
        total_lines = sum(content.count('\n') for content in file_contents.values())
        detected_language = "python"

        # 合并所有 pattern，统一在内存中搜索
        all_patterns = []
        if "security" in check_types:
            for pattern, message in SECURITY_PATTERNS:
                all_patterns.append((pattern, message, "security", "HIGH"))
        if "performance" in check_types:
            for pattern, message in PERFORMANCE_PATTERNS:
                all_patterns.append((pattern, message, "performance", "MEDIUM"))
        if "quality" in check_types:
            for pattern, message in QUALITY_PATTERNS:
                all_patterns.append((pattern, message, "quality", "LOW"))

        # 内存中执行所有 pattern 匹配
        for pattern, message, issue_type, severity in all_patterns:
            matches = _search_in_memory(file_contents, pattern, max_results=max_issues)
            for match in matches:
                issues.append({
                    "type": issue_type,
                    "severity": severity,
                    "message": message,
                    "file": match["file"],
                    "line": match["line"],
                    "line_content": match["line_content"],
                    "pattern": pattern,
                })

        # 去重（根据 file:line 组合）
        seen = set()
        unique_issues = []
        for issue in issues:
            key = f"{issue['file']}:{issue['line']}:{issue.get('pattern', '')}"
            if key not in seen:
                seen.add(key)
                unique_issues.append(issue)

        # 限制数量
        unique_issues = unique_issues[:max_issues]

        # 统计摘要
        summary = {
            "total_lines": total_lines,
            "total_issues": len(unique_issues),
            "by_type": {
                "security": len([i for i in unique_issues if i["type"] == "security"]),
                "performance": len([i for i in unique_issues if i["type"] == "performance"]),
                "quality": len([i for i in unique_issues if i["type"] == "quality"]),
            },
            "by_severity": {
                "HIGH": len([i for i in unique_issues if i["severity"] == "HIGH"]),
                "MEDIUM": len([i for i in unique_issues if i["severity"] == "MEDIUM"]),
                "LOW": len([i for i in unique_issues if i["severity"] == "LOW"]),
            },
        }

        result["language"] = detected_language
        result["issues"] = unique_issues
        result["issue_count"] = len(unique_issues)
        result["summary"] = summary
        result["success"] = True
        
    except Exception as e:
        result["error"] = f"Review error: {str(e)}"
    
    return result


def code_review_summary(issues: List[Dict[str, Any]]) -> str:
    """
    生成审查报告摘要文本
    
    Args:
        issues: code_review_run 返回的 issues 列表
    
    Returns:
        格式化的摘要字符串
    """
    if not issues:
        return "✅ No issues found"
    
    by_type = {}
    by_severity = {}
    
    for issue in issues:
        t = issue["type"]
        s = issue["severity"]
        by_type[t] = by_type.get(t, 0) + 1
        by_severity[s] = by_severity.get(s, 0) + 1
    
    lines = ["📋 Code Review Summary", "=" * 40]
    lines.append(f"Total issues: {len(issues)}")
    lines.append("")
    
    lines.append("By Type:")
    for t, count in sorted(by_type.items()):
        lines.append(f"  • {t}: {count}")
    
    lines.append("")
    lines.append("By Severity:")
    for s in ["HIGH", "MEDIUM", "LOW"]:
        if s in by_severity:
            lines.append(f"  • {s}: {by_severity[s]}")
    
    return "\n".join(lines)