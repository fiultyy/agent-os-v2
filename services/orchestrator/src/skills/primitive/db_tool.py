"""
DB Tool - L3.3 Primitive Implementation
提供 4 个数据库工具：query/execute/transaction/schema

使用 Python sqlite3 标准库实现，支持：
- 单条和批量 SQL 执行
- 事务管理
- 表结构查询
- 错误处理
"""

import logging
import re
import sqlite3
import json
from typing import Dict, Any, List, Optional, Union
from contextlib import contextmanager

logger = logging.getLogger(__name__)


# 默认数据库路径（内存数据库）
DEFAULT_DB = ":memory:"


# 表名验证正则：只允许 ASCII 字母、下划线开头，后跟字母/数字/下划线
_TABLE_NAME_RE = re.compile(r"^[a-zA-Z_][a-zA-Z0-9_]*$")


def _validate_table_name(table: str) -> None:
    """
    验证表名是否安全，防止 SQL 注入。

    Args:
        table: 表名

    Raises:
        ValueError: 表名格式非法
    """
    if not _TABLE_NAME_RE.match(table):
        raise ValueError(
            f"Invalid table name '{table}': "
            "must match ^[a-zA-Z_][a-zA-Z0-9_]*$"
        )


@contextmanager
def _get_connection(db_path: str = DEFAULT_DB):
    """
    数据库连接上下文管理器
    
    Args:
        db_path: 数据库路径，默认 :memory:
    
    Yields:
        sqlite3.Connection
    """
    conn = None
    try:
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        yield conn
    except Exception as e:
        logger.error(f"Database connection error: {e}")
        raise
    finally:
        if conn:
            conn.close()


def db_query(
    sql: str,
    params: Optional[List[Any]] = None,
    db_path: str = DEFAULT_DB,
    fetch: str = "all",
    limit: int = 100,
) -> Dict[str, Any]:
    """
    执行 SELECT 查询
    
    Args:
        sql: SQL 查询语句 (必填)
        params: 查询参数 (可选)
        db_path: 数据库路径，默认 :memory:
        fetch: 取值模式，"all"(全部) / "one"(一条) / "many"(指定条数)
        limit: 当 fetch="many" 时的条数，默认 100
    
    Returns:
        {
            "success": bool,
            "sql": str,
            "row_count": int,
            "rows": List[dict],
            "error": str (if failed)
        }
    
    Example:
        >>> result = db_query(
        ...     "SELECT * FROM users WHERE age > ?",
        ...     params=[18],
        ...     fetch="all"
        ... )
        >>> if result["success"]:
        ...     for row in result["rows"]:
        ...         print(row["name"])
    """
    result = {
        "success": False,
        "sql": sql,
        "row_count": 0,
        "rows": [],
        "error": None,
    }

    if params is None:
        params = []

    # 只允许 SELECT 和 PRAGMA 语句
    sql_upper = sql.strip().upper()
    if not (sql_upper.startswith("SELECT") or sql_upper.startswith("PRAGMA")):
        result["error"] = "Only SELECT and PRAGMA statements are allowed via db_query(). Use db_execute() for other operations."
        return result

    try:
        with _get_connection(db_path) as conn:
            cursor = conn.cursor()
            cursor.execute(sql, params)
            
            if fetch == "one":
                row = cursor.fetchone()
                if row:
                    result["rows"] = [dict(row)]
                    result["row_count"] = 1
            elif fetch == "many":
                rows = cursor.fetchmany(limit)
                result["rows"] = [dict(r) for r in rows]
                result["row_count"] = len(result["rows"])
            else:  # "all"
                rows = cursor.fetchall()
                result["rows"] = [dict(r) for r in rows]
                result["row_count"] = len(result["rows"])
            
            result["success"] = True
            
    except sqlite3.Error as e:
        result["error"] = f"SQLite error: {e}"
    except Exception as e:
        result["error"] = f"Error: {str(e)}"
    
    return result


def db_execute(
    sql: str,
    params: Optional[List[Any]] = None,
    db_path: str = DEFAULT_DB,
    commit: bool = True,
) -> Dict[str, Any]:
    """
    执行 INSERT/UPDATE/DELETE 等写操作
    
    Args:
        sql: SQL 语句 (必填)
        params: 语句参数 (可选)
        db_path: 数据库路径，默认 :memory:
        commit: 是否立即提交，默认 True
    
    Returns:
        {
            "success": bool,
            "sql": str,
            "row_count": int,
            "last_row_id": int,
            "error": str (if failed)
        }
    
    Example:
        >>> result = db_execute(
        ...     "INSERT INTO users (name, age) VALUES (?, ?)",
        ...     params=["Alice", 25]
        ... )
        >>> if result["success"]:
        ...     print(f"Inserted row {result['last_row_id']}")
    """
    result = {
        "success": False,
        "sql": sql,
        "row_count": 0,
        "last_row_id": None,
        "error": None,
    }
    
    if params is None:
        params = []
    
    try:
        with _get_connection(db_path) as conn:
            cursor = conn.cursor()
            cursor.execute(sql, params)
            
            result["row_count"] = cursor.rowcount
            result["last_row_id"] = cursor.lastrowid
            
            if commit:
                conn.commit()
            
            result["success"] = True
            
    except sqlite3.Error as e:
        result["error"] = f"SQLite error: {e}"
    except Exception as e:
        result["error"] = f"Error: {str(e)}"
    
    return result


def db_transaction(
    statements: List[Dict[str, Any]],
    db_path: str = DEFAULT_DB,
) -> Dict[str, Any]:
    """
    执行多条 SQL 语句的事务
    
    Args:
        statements: SQL 语句列表，每项包含 sql 和可选的 params
            例如: [
                {"sql": "INSERT INTO users (name) VALUES (?)", "params": ["Alice"]},
                {"sql": "UPDATE users SET age = ? WHERE name = ?", "params": [26, "Alice"]},
                {"sql": "DELETE FROM users WHERE name = ?", "params": ["Bob"]},
            ]
        db_path: 数据库路径，默认 :memory:
    
    Returns:
        {
            "success": bool,
            "executed_count": int,
            "results": List[dict],
            "error": str (if failed)
        }
    
    Example:
        >>> result = db_transaction([
        ...     {"sql": "CREATE TABLE IF NOT EXISTS test (id INTEGER PRIMARY KEY, name TEXT)"},
        ...     {"sql": "INSERT INTO test (name) VALUES (?)", "params": ["Alice"]},
        ...     {"sql": "SELECT * FROM test"},
        ... ])
    """
    result = {
        "success": False,
        "executed_count": 0,
        "results": [],
        "error": None,
    }
    
    try:
        with _get_connection(db_path) as conn:
            cursor = conn.cursor()
            
            for stmt in statements:
                sql = stmt.get("sql", "")
                params = stmt.get("params", [])
                
                if sql.strip().upper().startswith("SELECT"):
                    # 查询语句
                    cursor.execute(sql, params)
                    rows = cursor.fetchall()
                    result["results"].append({
                        "type": "select",
                        "row_count": len(rows),
                        "rows": [dict(r) for r in rows],
                    })
                else:
                    # 写操作
                    cursor.execute(sql, params)
                    result["results"].append({
                        "type": "write",
                        "row_count": cursor.rowcount,
                        "last_row_id": cursor.lastrowid,
                    })
                    result["executed_count"] += 1
            
            conn.commit()
            result["success"] = True
            
    except sqlite3.Error as e:
        result["error"] = f"SQLite error: {e}"
    except Exception as e:
        result["error"] = f"Error: {str(e)}"
    
    return result


def db_schema(
    table: str,
    db_path: str = DEFAULT_DB,
) -> Dict[str, Any]:
    """
    获取表结构信息
    
    Args:
        table: 表名 (必填)
        db_path: 数据库路径，默认 :memory:
    
    Returns:
        {
            "success": bool,
            "table": str,
            "columns": List[dict],
            "row_count": int,
            "indexes": List[dict],
            "error": str (if failed)
        }
    
    Example:
        >>> result = db_schema("users")
        >>> if result["success"]:
        ...     for col in result["columns"]:
        ...         print(f"{col['name']}: {col['type']}")
    """
    result = {
        "success": False,
        "table": table,
        "columns": [],
        "row_count": None,
        "indexes": [],
        "error": None,
    }
    
    try:
        # 表名安全校验
        _validate_table_name(table)

        with _get_connection(db_path) as conn:
            cursor = conn.cursor()

            # 获取列信息
            cursor.execute(f"PRAGMA table_info({table})")
            columns = cursor.fetchall()
            result["columns"] = [
                {
                    "cid": col[0],
                    "name": col[1],
                    "type": col[2],
                    "not_null": bool(col[3]),
                    "default_value": col[4],
                    "primary_key": bool(col[5]),
                }
                for col in columns
            ]
            
            # 获取行数
            # 表名已通过 _validate_table_name 校验，此处为安全调用
            cursor.execute(f"SELECT COUNT(*) FROM {table}")
            result["row_count"] = cursor.fetchone()[0]
            
            # 获取索引信息
            # 表名已通过 _validate_table_name 校验，此处为安全调用
            cursor.execute(f"PRAGMA index_list({table})")
            indexes = cursor.fetchall()
            result["indexes"] = [
                {
                    "seq": idx[0],
                    "name": idx[1],
                    "unique": bool(idx[2]),
                }
                for idx in indexes
            ]
            
            result["success"] = True
            
    except sqlite3.Error as e:
        result["error"] = f"SQLite error: {e}"
    except Exception as e:
        result["error"] = f"Error: {str(e)}"
    
    return result
