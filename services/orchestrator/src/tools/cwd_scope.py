"""Active-cwd scope primitives (设计 §7.1/§7.3).

极简 contextvar + session 级持久 dict + ``_resolve`` helper。零依赖 src 内其他模块
(纯 stdlib),便于单测。供 T6 文件 handler 改造(``_resolve``)与 T7 接线
(``set_session_active_cwd``/``get_session_active_cwd``)复用。

- ``_active_cwd``:contextvar,默认 ``None``(fallback 进程 cwd)。比 ``os.chdir`` 安全,
  无并发污染(设计 §7.1)。
- ``_resolve(ref)``:绝对路径 ``.resolve()`` 直通(向后兼容);相对路径从激活 cwd 解析,
  未 set 时退回 ``Path.cwd()``(决策 4 默认 workspace)。``..`` 遍历防护不在本 helper,
  沿用各 handler 的 ``_safe_path`` base 校验链。
- ``_SESSION_CWD``:模块级 dict,session 级持续(contextvar 跨 run 不保活,靠此 dict)。
  key 格式 'harness_type:session_id'(同 ``harness/flow.py`` 的 ``_key``)。
"""

from contextvars import ContextVar
from pathlib import Path

# 激活 cwd contextvar;未 set 时 _resolve fallback 进程 cwd(决策 4)。
_active_cwd: ContextVar[Path | None] = ContextVar("active_cwd", default=None)

# session 级持久:key = 'harness_type:session_id'(routes._key 格式)。
_SESSION_CWD: dict[str, str] = {}


def _resolve(ref: str) -> Path:
    """解析路径引用(设计 §7.2)。

    绝对路径直通 ``.resolve()``(现状不变);相对路径从激活 cwd 解析,未 set 时退回
    ``Path.cwd()``。``..`` 遍历防护不在本 helper。
    """
    p = Path(ref).expanduser()
    if p.is_absolute():
        return p.resolve()
    base = _active_cwd.get() or Path.cwd()
    return (base / ref).resolve()


def set_session_active_cwd(session_key: str, path_abs: str) -> None:
    """记录 session 级激活 cwd(跨 turn / 跨 run 持久,设计 §7.3)。

    写模块级 dict + 当前 contextvar。``path_abs`` 需为绝对路径(由调用方,T7/T8,保证)。
    """
    _SESSION_CWD[session_key] = path_abs
    _active_cwd.set(Path(path_abs))


def get_session_active_cwd(session_key: str) -> str | None:
    """读 session 级激活 cwd;未记录返 ``None``。"""
    return _SESSION_CWD.get(session_key)
