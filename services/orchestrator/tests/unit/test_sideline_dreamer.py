"""Tests for DreamerAgent (memory consolidation)."""

import pytest
from unittest.mock import AsyncMock, MagicMock

from src.memory.sideline.dreamer import DreamerAgent


class TestDreamerAgent:
    """Test DreamerAgent."""

    @pytest.fixture
    def mock_memory_service(self):
        memory = MagicMock()
        memory.reflect = AsyncMock(return_value={"episodic_created": 2})
        memory.list_active_sessions = AsyncMock(return_value=["session-1", "session-2"])
        return memory

    @pytest.fixture
    def agent(self, mock_memory_service):
        return DreamerAgent(mock_memory_service, interval_seconds=3600)

    def test_init(self, agent, mock_memory_service):
        assert agent._memory is mock_memory_service
        assert agent._interval == 3600
        assert agent._last_run is None
        assert agent._total_runs == 0

    def test_init_custom_interval(self, mock_memory_service):
        agent = DreamerAgent(mock_memory_service, interval_seconds=1800)
        assert agent._interval == 1800

    @pytest.mark.asyncio
    async def test_run_processes_all_sessions(self, agent, mock_memory_service):
        """Test run() processes all active sessions."""
        result = await agent.run()

        assert result["sessions_processed"] == 2
        assert result["episodic_created"] == 4  # 2 sessions * 2 episodic each
        assert result["errors"] == 0
        assert result["total_runs"] == 1
        assert agent._last_run is not None

    @pytest.mark.asyncio
    async def test_run_handles_errors(self, agent):
        """Test run() handles errors gracefully."""
        mock_memory = MagicMock()
        mock_memory.list_active_sessions = AsyncMock(return_value=["session-1", "session-2", "session-3"])
        mock_memory.reflect = AsyncMock(side_effect=[{"episodic_created": 1}, Exception("boom"), {"episodic_created": 1}])
        agent._memory = mock_memory

        result = await agent.run()

        assert result["sessions_processed"] == 2  # One failed but continued
        assert result["errors"] == 1
        assert result["episodic_created"] == 2

    @pytest.mark.asyncio
    async def test_consolidate_session_calls_reflect(self, agent, mock_memory_service):
        """Test consolidate_session calls memory.reflect()."""
        count = await agent.consolidate_session("session-1")

        assert count == 2
        mock_memory_service.reflect.assert_called_once_with(
            agent_id="dreamer",
            trigger="dreamer",
            session_id="session-1"
        )

    @pytest.mark.asyncio
    async def test_consolidate_session_none_memory(self):
        """Test consolidate_session with None memory returns 0."""
        agent = DreamerAgent(None)
        count = await agent.consolidate_session("session-1")
        assert count == 0

    @pytest.mark.asyncio
    async def test_consolidate_session_no_result(self, mock_memory_service):
        """Test consolidate_session when reflect returns None."""
        mock_memory_service.reflect = AsyncMock(return_value=None)
        agent = DreamerAgent(mock_memory_service)

        count = await agent.consolidate_session("session-1")
        assert count == 0

    @pytest.mark.asyncio
    async def test_run_once_for(self, agent, mock_memory_service):
        """Test run_once_for processes single session."""
        result = await agent.run_once_for("session-1")

        assert result["session_id"] == "session-1"
        assert result["episodic_created"] == 2
        assert "timestamp" in result

    def test_get_stats(self, agent):
        """Test get_stats returns statistics."""
        agent._total_runs = 5
        agent._last_run = "2026-04-21T12:00:00Z"

        stats = agent.get_stats()
        assert stats["interval_seconds"] == 3600
        assert stats["total_runs"] == 5
        assert stats["last_run"] == "2026-04-21T12:00:00Z"


class TestDreamerAgentActiveSessions:
    """Test _get_active_sessions logic."""

    @pytest.fixture
    def agent(self):
        return DreamerAgent(None)

    @pytest.mark.asyncio
    async def test_get_active_sessions_none_memory(self, agent):
        """Test _get_active_sessions returns [] when memory is None."""
        sessions = await agent._get_active_sessions()
        assert sessions == []

    @pytest.mark.asyncio
    async def test_get_active_sessions_list_active(self):
        """Test uses list_active_sessions if available."""
        mock_memory = MagicMock()
        mock_memory.list_active_sessions = AsyncMock(return_value=["s1", "s2"])
        agent = DreamerAgent(mock_memory)

        sessions = await agent._get_active_sessions()
        assert sessions == ["s1", "s2"]

    @pytest.mark.asyncio
    async def test_get_active_sessions_list_sessions(self):
        """Test uses list_sessions if list_active_sessions not available."""
        mock_memory = MagicMock()
        mock_memory.list_sessions = AsyncMock(return_value=["s1", "s2"])
        # Remove list_active_sessions
        del mock_memory.list_active_sessions
        agent = DreamerAgent(mock_memory)

        sessions = await agent._get_active_sessions()
        assert sessions == ["s1", "s2"]

    @pytest.mark.asyncio
    async def test_get_active_sessions_empty_result(self):
        """Test returns empty list when session list is empty."""
        mock_memory = MagicMock()
        mock_memory.list_active_sessions = AsyncMock(return_value=[])
        agent = DreamerAgent(mock_memory)

        sessions = await agent._get_active_sessions()
        assert sessions == []


class TestDreamerAgentMultipleRuns:
    """Test multiple runs tracking."""

    @pytest.fixture
    def mock_memory(self):
        memory = MagicMock()
        memory.reflect = AsyncMock(return_value={"episodic_created": 1})
        memory.list_active_sessions = AsyncMock(return_value=["session-1"])
        return memory

    @pytest.fixture
    def agent(self, mock_memory):
        return DreamerAgent(mock_memory, interval_seconds=3600)

    @pytest.mark.asyncio
    async def test_total_runs_increments(self, agent):
        """Test total_runs increments each run."""
        await agent.run()
        assert agent._total_runs == 1

        await agent.run()
        assert agent._total_runs == 2

        await agent.run()
        assert agent._total_runs == 3