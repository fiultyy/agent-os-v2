"""Core Memory Block operations."""

from src.memory.store import InMemoryStore
from src.memory.types import MemoryBlock


class BlockOperations:
    """Manages agent core memory blocks (persona, user_profile, etc.)."""

    def __init__(self, store: InMemoryStore) -> None:
        self._store = store

    async def init_agent_blocks(self, agent_id: str) -> None:
        """Initialize the default memory blocks for an agent.

        Creates ``persona`` and ``user_profile`` blocks with
        2000 character limits each.
        """
        await self._store.create_block(
            agent_id, "persona", char_limit=2000, initial_content=""
        )
        await self._store.create_block(
            agent_id, "user_profile", char_limit=2000, initial_content=""
        )

    async def get_block(self, agent_id: str, label: str) -> MemoryBlock | None:
        """Read a memory block."""
        return await self._store.get_block(agent_id, label)

    async def update_block(self, agent_id: str, label: str, content: str) -> MemoryBlock | None:
        """Update a memory block's content.

        Raises:
            ValueError: If content exceeds the block's char_limit.
        """
        return await self._store.update_block(agent_id, label, content)

    async def list_blocks(self, agent_id: str) -> list[MemoryBlock]:
        """List all blocks for an agent."""
        return await self._store.list_blocks(agent_id)
