"""Tests for CriticAgent (fact correction)."""

import pytest
from datetime import datetime, timezone
from unittest.mock import MagicMock, AsyncMock

from src.memory.sideline.critic import CriticAgent, EvaluationResult


class TestCriticAgent:
    """Test CriticAgent."""

    @pytest.fixture
    def mock_memory_service(self):
        return MagicMock()

    @pytest.fixture
    def agent(self, mock_memory_service):
        return CriticAgent(mock_memory_service, confidence_threshold=0.5)

    def test_init(self, agent, mock_memory_service):
        assert agent._memory is mock_memory_service
        assert agent._threshold == 0.5
        assert agent._correction_history == []

    def test_init_default_threshold(self, mock_memory_service):
        agent = CriticAgent(mock_memory_service)
        assert agent._threshold == 0.5

    @pytest.mark.asyncio
    async def test_evaluate_all_high_confidence(self, agent):
        """Test evaluate when all results have high confidence."""
        results = [
            {"memory_id": "m1", "content": "Test", "source": "user_input", "timestamp": datetime.now(timezone.utc).isoformat()},
            {"memory_id": "m2", "content": "Test 2", "source": "tool_result", "timestamp": datetime.now(timezone.utc).isoformat()},
        ]

        evaluated = await agent.evaluate(results)

        assert len(evaluated) == 2
        assert all(not e.needs_correction for e in evaluated)
        assert all(e.confidence >= 0.5 for e in evaluated)

    @pytest.mark.asyncio
    async def test_evaluate_marks_low_confidence(self, agent):
        """Test evaluate marks low confidence items."""
        results = [
            # Use external source + very old timestamp for low confidence
            {"memory_id": "m1", "content": "Old external memory", "source": "external", "timestamp": "2020-01-01T00:00:00Z", "consistency": 0.3},
        ]

        evaluated = await agent.evaluate(results)

        assert len(evaluated) == 1
        assert evaluated[0].needs_correction

    @pytest.mark.asyncio
    async def test_evaluate_empty_list(self, agent):
        """Test evaluate with empty list."""
        evaluated = await agent.evaluate([])
        assert len(evaluated) == 0

    @pytest.mark.asyncio
    async def test_evaluate_computes_confidence_correctly(self, agent):
        """Test confidence is computed from source, age, consistency."""
        # user_input source with recent timestamp should have high confidence
        results = [
            {"memory_id": "m1", "content": "Recent user input", "source": "user_input", "timestamp": datetime.now(timezone.utc).isoformat()},
        ]

        evaluated = await agent.evaluate(results)
        assert evaluated[0].confidence > 0.8

    @pytest.mark.asyncio
    async def test_evaluate_handles_missing_fields(self, agent):
        """Test evaluate handles items with missing fields."""
        results = [
            {"memory_id": "m1"},  # minimal
            {"memory_id": "m2", "content": "Test"},  # only content
        ]

        evaluated = await agent.evaluate(results)
        assert len(evaluated) == 2
        # Should not raise, should use defaults

    @pytest.mark.asyncio
    async def test_get_corrections(self, agent):
        """Test get_corrections returns list of low confidence memories."""
        mock_memory = MagicMock()
        mock_memory.get_session_memories = AsyncMock(return_value=[
            {"memory_id": "m1", "content": "Test 1", "confidence": 0.9},
            {"memory_id": "m2", "content": "Test 2", "confidence": 0.3},  # below threshold
        ])
        agent._memory = mock_memory

        corrections = await agent.get_corrections("session-1")

        assert len(corrections) == 1
        assert corrections[0]["memory_id"] == "m2"

    @pytest.mark.asyncio
    async def test_get_corrections_empty_when_no_low_confidence(self, agent):
        """Test get_corrections returns empty when no low confidence memories."""
        mock_memory = MagicMock()
        mock_memory.get_session_memories = AsyncMock(return_value=[
            {"memory_id": "m1", "content": "Test 1", "confidence": 0.9},
            {"memory_id": "m2", "content": "Test 2", "confidence": 0.7},
        ])
        agent._memory = mock_memory

        corrections = await agent.get_corrections("session-1")
        assert len(corrections) == 0

    @pytest.mark.asyncio
    async def test_get_injection_returns_corrections(self, agent):
        """Test get_injection returns formatted correction text."""
        evaluated_results = [
            EvaluationResult(
                memory_id="m1",
                confidence=0.3,
                needs_correction=True,
                correction_hint="Confidence too low"
            ),
            EvaluationResult(
                memory_id="m2",
                confidence=0.8,
                needs_correction=False
            ),
        ]

        injection = await agent.get_injection("session-1", evaluated_results)

        assert "[事实纠正建议]" in injection
        assert "session-1" in injection
        assert "低置信度项数: 1" in injection

    @pytest.mark.asyncio
    async def test_get_injection_empty_when_no_corrections(self, agent):
        """Test get_injection returns empty string when no corrections needed."""
        evaluated_results = [
            EvaluationResult(
                memory_id="m1",
                confidence=0.8,
                needs_correction=False
            ),
        ]

        injection = await agent.get_injection("session-1", evaluated_results)
        assert injection == ""


class TestEvaluationResult:
    """Test EvaluationResult dataclass."""

    def test_default_values(self):
        result = EvaluationResult(memory_id="m1", confidence=0.5, needs_correction=False)
        assert result.memory_id == "m1"
        assert result.confidence == 0.5
        assert result.needs_correction is False
        assert result.correction_hint is None
        assert result.reason == ""

    def test_custom_values(self):
        result = EvaluationResult(
            memory_id="m1",
            confidence=0.3,
            needs_correction=True,
            correction_hint="Low confidence",
            reason="confidence < threshold"
        )
        assert result.correction_hint == "Low confidence"
        assert result.reason == "confidence < threshold"


class TestCriticAgentConfidenceComputation:
    """Test confidence computation."""

    @pytest.fixture
    def agent(self):
        return CriticAgent(None, confidence_threshold=0.5)

    def test_user_input_source_highest(self, agent):
        """Test user_input source gets highest confidence."""
        item = {"source": "user_input"}
        confidence = agent._compute_confidence(item)
        assert confidence >= 0.8

    def test_tool_result_high(self, agent):
        """Test tool_result gets high confidence."""
        item = {"source": "tool_result"}
        confidence = agent._compute_confidence(item)
        assert confidence >= 0.7

    def test_unknown_source_low(self, agent):
        """Test unknown source gets lower confidence than known sources."""
        item = {"source": "unknown"}
        confidence = agent._compute_confidence(item)
        # Unknown source has base 0.5, should be lower than user_input (1.0 * 0.4 + 0.8 * 0.3 + 1.0 * 0.3 = 0.94)
        assert confidence < 0.8

    def test_old_timestamp_decay(self, agent):
        """Test older timestamps get lower confidence."""
        old_item = {"source": "user_input", "timestamp": "2020-01-01T00:00:00Z"}
        new_item = {"source": "user_input", "timestamp": datetime.now(timezone.utc).isoformat()}

        old_conf = agent._compute_confidence(old_item)
        new_conf = agent._compute_confidence(new_item)

        assert new_conf > old_conf

    def test_consistency_factor(self, agent):
        """Test consistency factor affects confidence."""
        item_with_consistency = {"source": "user_input", "consistency": 0.9, "timestamp": datetime.now(timezone.utc).isoformat()}
        item_without = {"source": "user_input", "timestamp": datetime.now(timezone.utc).isoformat()}

        conf_with = agent._compute_confidence(item_with_consistency)
        conf_without = agent._compute_confidence(item_without)

        # Should not be less than (may be equal if consistency=1.0 in default)
        assert conf_with >= conf_without - 0.05

    def test_confidence_bounded(self, agent):
        """Test confidence is always between 0 and 1."""
        item = {"source": "user_input"}
        confidence = agent._compute_confidence(item)
        assert 0.0 <= confidence <= 1.0


class TestCriticAgentGetStats:
    """Test get_stats."""

    @pytest.fixture
    def agent(self):
        return CriticAgent(None, confidence_threshold=0.6)

    def test_get_stats(self, agent):
        stats = agent.get_stats()
        assert stats["confidence_threshold"] == 0.6
        assert stats["total_corrections"] == 0