"""Memory management module."""

from src.memory.service import MemoryService
from src.memory.store import InMemoryStore
from src.memory.sqlitestore import SQLiteStore
from src.memory.types import (
    MemoryItem,
    MemoryRef,
    MemoryBlock,
    MemoryFilter,
    MemoryType,
    MemoryScope,
    MemoryOrigin,
    RecallMode,
)
from src.memory.permissions import (
    PermissionManager,
    PermissionLevel,
    AccessGrant,
    AccessLogEntry,
)
from src.memory.event_bus import EventType, MemoryEventBus
from src.memory.hooks import (
    HookPriority,
    MemoryHook,
    SessionContext,
    TurnContext,
    CompressContext,
    CompressResult,
    DelegateContext,
)
from src.memory.default_hook import DefaultMemoryHook
from src.memory.kg_query_interface import KGQueryInterface
from src.memory.sideline.transcriber import SidelineTranscriber
from src.memory.experience_kg import ExperienceKG
from src.memory.tools.kg_memory_tool import KGMemoryTool
from src.memory.tools.experience_tool import ExperienceTool

__all__ = [
    "MemoryService",
    "InMemoryStore",
    "SQLiteStore",
    "MemoryItem",
    "MemoryRef",
    "MemoryBlock",
    "MemoryFilter",
    "MemoryType",
    "MemoryScope",
    "MemoryOrigin",
    "RecallMode",
    "PermissionManager",
    "PermissionLevel",
    "AccessGrant",
    "AccessLogEntry",
    # P1 event bus + hooks
    "EventType",
    "MemoryEventBus",
    "HookPriority",
    "MemoryHook",
    "SessionContext",
    "TurnContext",
    "CompressContext",
    "CompressResult",
    "DelegateContext",
    "DefaultMemoryHook",
    "KGQueryInterface",
    "SidelineTranscriber",
    "KGMemoryTool",
    "ExperienceKG",
    "ExperienceTool",
]
