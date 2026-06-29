"""Tests for KairosAgent (LIF timing injection)."""

import pytest

from src.memory.sideline.kairos import KairosAgent, LIFState


class MockMemoryService:
    """Mock memory service for testing."""
    pass


class TestLIFState:
    """Test LIF state dataclass."""

    def test_default_values(self):
        state = LIFState()
        assert state.potential == 0.0
        assert state.threshold == 0.8
        assert state.decay_rate == 0.05

    def test_custom_values(self):
        state = LIFState(potential=0.5, threshold=0.7, decay_rate=0.1)
        assert state.potential == 0.5
        assert state.threshold == 0.7
        assert state.decay_rate == 0.1


class TestKairosAgent:
    """Test KairosAgent."""

    @pytest.fixture
    def memory_service(self):
        return MockMemoryService()

    @pytest.fixture
    def agent(self, memory_service):
        return KairosAgent(memory_service, threshold=0.8)

    def test_update_potential_basic(self, agent):
        """Test basic potential update."""
        result = agent.update_potential("session-1", importance=0.5, is_decision_point=False)
        assert result == 0.5
        assert agent.should_inject("session-1") is False

    def test_update_potential_decision_point_bonus(self, agent):
        """Test decision point bonus (1.2x multiplier)."""
        result = agent.update_potential("session-1", importance=0.5, is_decision_point=True)
        assert result == 0.6  # 0.5 * 1.2

    def test_update_potential_accumulates(self, agent):
        """Test potential accumulates across updates with decay."""
        # After first: 0 * 0.95 + 0.3 = 0.3
        # After second: 0.3 * 0.95 + 0.3 = 0.285 + 0.3 = 0.585
        agent.update_potential("session-1", importance=0.3, is_decision_point=False)
        result = agent.update_potential("session-1", importance=0.3, is_decision_point=False)
        assert result == 0.585
        # After third: 0.585 * 0.95 + 0.3 = 0.55575 + 0.3 = 0.85575
        agent.update_potential("session-1", importance=0.3, is_decision_point=False)
        assert agent.should_inject("session-1") is True

    def test_should_inject_below_threshold(self, agent):
        """Test should_inject returns False when below threshold."""
        agent.update_potential("session-1", importance=0.3, is_decision_point=False)
        assert agent.should_inject("session-1") is False

    def test_should_inject_above_threshold(self, agent):
        """Test should_inject returns True when above threshold."""
        agent.update_potential("session-1", importance=0.8, is_decision_point=False)
        assert agent.should_inject("session-1") is True

    def test_should_inject_nonexistent_session(self, agent):
        """Test should_inject returns False for nonexistent session."""
        assert agent.should_inject("nonexistent") is False

    def test_get_potential(self, agent):
        """Test get_potential returns current value."""
        agent.update_potential("session-1", importance=0.5, is_decision_point=False)
        assert agent.get_potential("session-1") == 0.5
        assert agent.get_potential("nonexistent") == 0.0

    @pytest.mark.asyncio
    async def test_get_injection_below_threshold(self, agent):
        """Test get_injection returns None when below threshold."""
        result = await agent.get_injection("session-1", {})
        assert result is None

    @pytest.mark.asyncio
    async def test_get_injection_above_threshold(self, agent):
        """Test get_injection returns injection text when above threshold."""
        agent.update_potential("session-1", importance=0.9, is_decision_point=False)
        result = await agent.get_injection("session-1", {"session_summary": "test session"})
        assert result is not None
        assert "[时机建议]" in result

    @pytest.mark.asyncio
    async def test_get_injection_resets_potential(self, agent):
        """Test get_injection resets potential after firing."""
        agent.update_potential("session-1", importance=0.9, is_decision_point=False)
        assert agent.should_inject("session-1") is True

        await agent.get_injection("session-1", {})
        # After reset, potential should be ~10% of original
        assert agent.should_inject("session-1") is False

    def test_reset_session(self, agent):
        """Test reset_session clears LIF state."""
        agent.update_potential("session-1", importance=0.9, is_decision_point=False)
        assert agent.should_inject("session-1") is True

        agent.reset_session("session-1")
        assert agent.should_inject("session-1") is False

    def test_get_stats(self, agent):
        """Test get_stats returns statistics."""
        agent.update_potential("session-1", importance=0.5, is_decision_point=False)
        agent.update_potential("session-2", importance=0.9, is_decision_point=False)

        stats = agent.get_stats()
        assert stats["total_sessions"] == 2
        assert stats["firing_sessions"] == 1
        assert stats["avg_potential"] > 0


class TestKairosAgentDecay:
    """Test LIF decay mechanism."""

    @pytest.fixture
    def agent(self):
        return KairosAgent(MockMemoryService(), threshold=0.5, decay_rate=0.1)

    def test_decay_applied_each_update(self, agent):
        """Test decay is applied on each update."""
        agent.update_potential("session-1", importance=0.5, is_decision_point=False)
        initial = agent.get_potential("session-1")

        # Second update should have decay applied
        agent.update_potential("session-1", importance=0.0, is_decision_point=False)  # 0 importance to isolate decay
        second = agent.get_potential("session-1")

        # With 0.1 decay rate and 0 importance, potential should decrease
        assert second < initial

    def test_potential_capped_at_1(self, agent):
        """Test potential is capped at 1.0."""
        agent.update_potential("session-1", importance=1.0, is_decision_point=True)
        agent.update_potential("session-1", importance=1.0, is_decision_point=True)
        assert agent.get_potential("session-1") == 1.0


class TestKairosAgentSummarize:
    """Test _summarize_recent helper."""

    @pytest.fixture
    def agent(self):
        return KairosAgent(MockMemoryService())

    def test_summarize_empty(self, agent):
        result = agent._summarize_recent([])
        assert result == ""

    def test_summarize_multiple(self, agent):
        memories = [
            {"content": "First memory"},
            {"text": "Second memory"},
            {"content": "Third memory"},
        ]
        result = agent._summarize_recent(memories, max_chars=100)
        assert "First memory" in result
        assert "Second memory" in result

    def test_summarize_truncation(self, agent):
        memories = [{"content": "This is a very long memory content that should be truncated"}]
        result = agent._summarize_recent(memories, max_chars=20)
        assert len(result) <= 23  # 20 + "..."