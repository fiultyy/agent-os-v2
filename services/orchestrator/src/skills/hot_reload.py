"""
SkillHotReloader — Skills 目录热更新监视器 (P3-A)

监视 skills 目录中的 SKILL.md 文件变化，自动触发 catalog reload。

依赖：
- watchdog（可选）：提供高效的文件系统事件监听
- 若 watchdog 不可用：降级为轮询模式（PollingReloader）

防抖策略：
- 文件变化事件后等待 500ms 再执行 reload（避免批量写入频繁触发）
- 同一时间段内多次变化只 reload 一次

忽略路径：
- .git, __pycache__, node_modules, .venv
"""

from __future__ import annotations

import logging
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from .skill_catalog import SkillCatalog

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

DEBOUNCE_SECONDS = 0.5  # 防抖延迟（秒）

IGNORED_DIRS = frozenset({".git", "__pycache__", "node_modules", ".venv"})

# ---------------------------------------------------------------------------
# Watchdog availability check
# ---------------------------------------------------------------------------

try:
    from watchdog.observers import Observer
    from watchdog.events import FileSystemEventHandler
    _WATCHDOG_AVAILABLE = True
except ImportError:
    _WATCHDOG_AVAILABLE = False
    logger.info(
        "watchdog not installed; SkillHotReloader will use polling fallback. "
        "Install with: pip install watchdog"
    )


# ---------------------------------------------------------------------------
# Watchdog-based handler
# ---------------------------------------------------------------------------

if _WATCHDOG_AVAILABLE:
    class _SkillEventHandler(FileSystemEventHandler):  # type: ignore[misc]
        """处理文件系统事件，触发 skill reload。"""

        def __init__(self, reloader: "SkillHotReloader") -> None:
            super().__init__()
            self._reloader = reloader

        def _should_ignore(self, path_str: str) -> bool:
            """检查路径是否应被忽略。"""
            parts = Path(path_str).parts
            return any(part in IGNORED_DIRS for part in parts)

        def _is_skill_file(self, path_str: str) -> bool:
            """判断是否是 SKILL.md 文件（大小写不敏感）。"""
            return Path(path_str).name.lower() == "skill.md"

        def on_modified(self, event: Any) -> None:
            if event.is_directory:
                return
            if self._should_ignore(event.src_path):
                return
            if self._is_skill_file(event.src_path):
                logger.debug("SKILL.md modified: %s", event.src_path)
                self._reloader._schedule_reload(reason="modified", path=event.src_path)

        def on_created(self, event: Any) -> None:
            if event.is_directory:
                return
            if self._should_ignore(event.src_path):
                return
            if self._is_skill_file(event.src_path):
                logger.debug("SKILL.md created: %s", event.src_path)
                self._reloader._schedule_reload(reason="created", path=event.src_path)

        def on_deleted(self, event: Any) -> None:
            if event.is_directory:
                return
            if self._should_ignore(event.src_path):
                return
            if self._is_skill_file(event.src_path):
                logger.debug("SKILL.md deleted: %s", event.src_path)
                self._reloader._schedule_reload(reason="deleted", path=event.src_path)

        def on_moved(self, event: Any) -> None:
            if event.is_directory:
                return
            src_is_skill = self._is_skill_file(event.src_path)
            dst_is_skill = self._is_skill_file(event.dest_path)
            if (src_is_skill or dst_is_skill) and not self._should_ignore(event.dest_path):
                logger.debug("SKILL.md moved: %s -> %s", event.src_path, event.dest_path)
                self._reloader._schedule_reload(reason="moved", path=event.dest_path)


# ---------------------------------------------------------------------------
# Polling fallback
# ---------------------------------------------------------------------------

class _PollingReloader:
    """
    watchdog 不可用时的降级实现。
    每隔 poll_interval 秒扫描目录，检测 SKILL.md 修改时间变化。
    """

    def __init__(self, reloader: "SkillHotReloader", poll_interval: float = 3.0) -> None:
        self._reloader = reloader
        self._poll_interval = poll_interval
        self._thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()
        self._dirs: List[Path] = []
        self._mtimes: Dict[str, float] = {}

    def start(self, dirs: List[Path]) -> None:
        self._dirs = dirs
        self._stop_event.clear()
        self._mtimes = self._scan_mtimes()
        self._thread = threading.Thread(target=self._poll_loop, daemon=True, name="skill-poll-reloader")
        self._thread.start()
        logger.info("SkillHotReloader: polling mode started (interval=%.1fs)", self._poll_interval)

    def stop(self) -> None:
        self._stop_event.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=5.0)
        logger.info("SkillHotReloader: polling mode stopped")

    def _scan_mtimes(self) -> Dict[str, float]:
        """扫描所有目录中的 SKILL.md 修改时间。"""
        mtimes: Dict[str, float] = {}
        for d in self._dirs:
            if not d.is_dir():
                continue
            for skill_md in d.rglob("SKILL.md"):
                # Check none of the parts are in IGNORED_DIRS
                if any(part in IGNORED_DIRS for part in skill_md.parts):
                    continue
                try:
                    mtimes[str(skill_md)] = skill_md.stat().st_mtime
                except OSError:
                    pass
        return mtimes

    def _poll_loop(self) -> None:
        while not self._stop_event.wait(self._poll_interval):
            try:
                new_mtimes = self._scan_mtimes()
            except Exception as e:
                logger.warning(
                    "SkillHotReloader: polling scan failed: %s. Skipping this cycle.", e
                )
                continue
            if new_mtimes != self._mtimes:
                logger.debug("SkillHotReloader: change detected via polling")
                self._mtimes = new_mtimes
                self._reloader._schedule_reload(reason="polling")


# ---------------------------------------------------------------------------
# Main SkillHotReloader
# ---------------------------------------------------------------------------

class SkillHotReloader:
    """
    Skills 目录热更新监视器。

    Usage::

        catalog = SkillCatalog()
        catalog.reload()

        reloader = SkillHotReloader(catalog)
        reloader.start([Path("~/.agent-os/skills").expanduser()])

        # ... app runs ...

        reloader.stop()

    当 watchdog 可用时使用文件系统事件；否则降级为轮询模式。
    """

    def __init__(
        self,
        catalog: "SkillCatalog",
        bridge: Any = None,
        debounce: float = DEBOUNCE_SECONDS,
    ) -> None:
        self._catalog = catalog
        self._bridge = bridge  # ToolRegistry bridge (optional)
        self._debounce = debounce

        # Watchdog observers: path_str → Observer instance
        self._watchers: Dict[str, Any] = {}

        # Polling fallback
        self._polling_reloader: Optional[_PollingReloader] = None

        # Debounce state
        self._pending_reload: Optional[threading.Timer] = None
        self._lock = threading.Lock()

        # Stats
        self._reload_count = 0
        self._last_reload_time: Optional[float] = None

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def start(self, dirs: List[Path]) -> None:
        """
        启动对指定目录列表的监视。

        Args:
            dirs: 要监视的目录列表（e.g. user/project/builtin skill 目录）
        """
        if not dirs:
            logger.warning("SkillHotReloader.start() called with empty dirs list")
            return

        if _WATCHDOG_AVAILABLE:
            self._start_watchdog(dirs)
        else:
            self._polling_reloader = _PollingReloader(self, poll_interval=3.0)
            self._polling_reloader.start(dirs)

    def stop(self) -> None:
        """停止所有监视器，取消未完成的 reload。"""
        # Cancel pending debounce timer
        with self._lock:
            if self._pending_reload is not None:
                self._pending_reload.cancel()
                self._pending_reload = None

        # Stop watchdog observers
        for path_str, observer in list(self._watchers.items()):
            try:
                observer.stop()
                observer.join(timeout=5.0)
                logger.debug("SkillHotReloader: stopped watcher for %s", path_str)
            except Exception as e:
                logger.warning("Error stopping watcher for %s: %s", path_str, e)
        self._watchers.clear()

        # Stop polling fallback
        if self._polling_reloader is not None:
            self._polling_reloader.stop()
            self._polling_reloader = None

        logger.info(
            "SkillHotReloader stopped (total reloads: %d)", self._reload_count
        )

    @property
    def is_running(self) -> bool:
        """是否正在监视中。"""
        return bool(self._watchers) or self._polling_reloader is not None

    @property
    def reload_count(self) -> int:
        """累计 reload 次数。"""
        return self._reload_count

    @property
    def last_reload_time(self) -> Optional[float]:
        """最近一次 reload 的时间戳（Unix epoch）。"""
        return self._last_reload_time

    # ------------------------------------------------------------------
    # Internal: watchdog startup
    # ------------------------------------------------------------------

    def _start_watchdog(self, dirs: List[Path]) -> None:
        """为每个目录创建独立的 watchdog Observer。"""
        handler = _SkillEventHandler(self)

        for d in dirs:
            d_expanded = d.expanduser().resolve()
            if not d_expanded.is_dir():
                logger.debug("SkillHotReloader: skip non-existent dir %s", d_expanded)
                continue

            path_str = str(d_expanded)
            if path_str in self._watchers:
                logger.debug("SkillHotReloader: already watching %s", path_str)
                continue

            try:
                observer = Observer()
                observer.schedule(handler, path=path_str, recursive=True)
                observer.start()
                self._watchers[path_str] = observer
                logger.info("SkillHotReloader: watching %s (watchdog)", path_str)
            except Exception as e:
                logger.warning("Failed to start watchdog observer for %s: %s", path_str, e)

    # ------------------------------------------------------------------
    # Internal: debounce + reload
    # ------------------------------------------------------------------

    def _schedule_reload(self, reason: str = "unknown", path: str = "") -> None:
        """
        调度一次防抖 reload。

        多次调用时取消旧 timer，重新计时，确保最后一次变化后 debounce 秒执行。
        """
        with self._lock:
            if self._pending_reload is not None:
                self._pending_reload.cancel()

            self._pending_reload = threading.Timer(
                self._debounce,
                self._do_reload,
                kwargs={"reason": reason, "path": path},
            )
            self._pending_reload.daemon = True
            self._pending_reload.start()
            logger.debug(
                "SkillHotReloader: reload scheduled in %.1fs (reason=%s, path=%s)",
                self._debounce, reason, path,
            )

    def _on_change(self, event: Any) -> None:
        """
        文件变化回调（公开接口，供外部测试使用）。

        内部实际处理由 watchdog handler 直接调用 _schedule_reload。
        """
        src = getattr(event, "src_path", "")
        self._schedule_reload(reason="event", path=src)

    def _reload(self) -> None:
        """执行 catalog reload（公开接口，供测试直接调用）。"""
        self._do_reload(reason="manual")

    def _do_reload(self, reason: str = "unknown", path: str = "") -> None:
        """实际执行 reload 逻辑。"""
        with self._lock:
            self._pending_reload = None

        try:
            logger.info(
                "SkillHotReloader: reloading catalog (reason=%s, path=%s)",
                reason, path,
            )
            self._catalog.reload()
            self._reload_count += 1
            self._last_reload_time = time.time()

            # Notify bridge if available (e.g. re-register tools)
            if self._bridge is not None and hasattr(self._bridge, "sync"):
                try:
                    self._bridge.sync(self._catalog)
                except Exception as e:
                    logger.warning("SkillHotReloader: bridge.sync() failed: %s", e)

            logger.info(
                "SkillHotReloader: catalog reloaded successfully "
                "(version=%d, count=%d, reload_count=%d)",
                self._catalog.version,
                len(self._catalog._entries),
                self._reload_count,
            )
        except Exception as e:
            logger.error("SkillHotReloader: reload failed: %s", e, exc_info=True)
