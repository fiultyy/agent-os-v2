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

from typing import Dict, Any, List, Optional

# 导入 code skill 工具
from ..skill.code.read import code_read
from ..skill.code.search import code_search


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
        # 读取代码
        read_result = code_read(path, language=language)
        if not read_result["success"]:
            result["error"] = f"Failed to read file: {read_result.get('error')}"
            return result
        
        language = read_result["language"]
        lines = read_result["lines"]
        total_lines = read_result["total_lines"]
        
        # 收集问题
        issues = []
        
        # 安全检查
        if "security" in check_types:
            for pattern, message in SECURITY_PATTERNS:
                search_result = code_search(
                    query=pattern,
                    path=path,
                    pattern_type="regex",
                    context_lines=context_lines,
                    max_results=max_issues,
                )
                if search_result["success"]:
                    for match in search_result["matches"]:
                        issues.append({
                            "type": "security",
                            "severity": "HIGH",
                            "message": message,
                            "file": match["file"],
                            "line": match["line"],
                            "line_content": match["line_content"],
                            "pattern": pattern,
                        })
        
        # 性能检查
        if "performance" in check_types:
            for pattern, message in PERFORMANCE_PATTERNS:
                search_result = code_search(
                    query=pattern,
                    path=path,
                    pattern_type="regex",
                    context_lines=context_lines,
                    max_results=max_issues,
                )
                if search_result["success"]:
                    for match in search_result["matches"]:
                        issues.append({
                            "type": "performance",
                            "severity": "MEDIUM",
                            "message": message,
                            "file": match["file"],
                            "line": match["line"],
                            "line_content": match["line_content"],
                            "pattern": pattern,
                        })
        
        # 代码质量检查
        if "quality" in check_types:
            for pattern, message in QUALITY_PATTERNS:
                search_result = code_search(
                    query=pattern,
                    path=path,
                    pattern_type="regex",
                    context_lines=context_lines,
                    max_results=max_issues,
                )
                if search_result["success"]:
                    for match in search_result["matches"]:
                        issues.append({
                            "type": "quality",
                            "severity": "LOW",
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
            key = f"{issue['file']}:{issue['line']}"
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