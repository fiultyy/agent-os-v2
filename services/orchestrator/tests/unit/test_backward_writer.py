"""Tests for BackwardWriter (write-back to Main Agent)."""

import pytest
from unittest.mock import AsyncMock, MagicMock

from src.memory.sideline.backward_writer import (
    BackwardWriter,
    WriteBackChannel,
    WriteBackResult,
)
from src.memory.types import MemoryType, MemoryScope


class MockMemoryRef:
    def __init__(self, id_="mock-id"):
        self.id = id_


class TestWriteBackChannel:
    """Test WriteBackChannel enum."""

    def test_channel_values(self):
        assert WriteBackChannel.SLOW.value == "slow"
        assert WriteBackChannel.MEDIUM.value == "medium"
        assert WriteBackChannel.FAST.value == "fast"


class TestWriteBackResult:
    """Test WriteBackResult dataclass."""

    def test_result_fields(self):
        result = WriteBackResult(
            channel=WriteBackChannel.FAST,
            written=True,
            content="test content",
            confidence=0.9,
            memory_id="mem-123",
        )
        assert result.channel == WriteBackChannel.FAST
        assert result.written is True
        assert result.content == "test content"
        assert result.confidence == 0.9
        assert result.memory_id == "mem-123"

    def test_result_default_memory_id(self):
        result = WriteBackResult(
            channel=WriteBackChannel.MEDIUM,
            written=False,
            content="test",
            confidence=0.6,
        )
        assert result.memory_id == ""


class TestBackwardWriter:
    """Test BackwardWriter."""

    @pytest.fixture
    def mock_memory_service(self):
        memory = MagicMock()
        memory.store = AsyncMock(return_value=MockMemoryRef(id_="mem-abc"))
        return memory

    @pytest.fixture
    def mock_llm_client(self):
        llm = MagicMock()
        llm.agenerate = AsyncMock(return_value="This is a concise summary.")
        return llm

    @pytest.fixture
    def writer(self, mock_memory_service):
        return BackwardWriter(mock_memory_service)

    @pytest.fixture
    def writer_with_llm(self, mock_memory_service, mock_llm_client):
        return BackwardWriter(mock_memory_service, llm_client=mock_llm_client)

    # ── Channel selection ────────────────────────────────────────

    def test_choose_channel_fast(self, writer):
        assert writer.choose_channel(0.8) == WriteBackChannel.FAST
        assert writer.choose_channel(0.95) == WriteBackChannel.FAST
        assert writer.choose_channel(1.0) == WriteBackChannel.FAST

    def test_choose_channel_medium(self, writer):
        assert writer.choose_channel(0.5) == WriteBackChannel.MEDIUM
        assert writer.choose_channel(0.65) == WriteBackChannel.MEDIUM
        assert writer.choose_channel(0.79) == WriteBackChannel.MEDIUM

    def test_choose_channel_slow(self, writer):
        assert writer.choose_channel(0.0) == WriteBackChannel.SLOW
        assert writer.choose_channel(0.3) == WriteBackChannel.SLOW
        assert writer.choose_channel(0.49) == WriteBackChannel.SLOW

    # ── Fast channel ──────────────────────────────────────────────

    @pytest.mark.asyncio
    async def test_write_fast_stores_working_memory(self, writer, mock_memory_service):
        result = await writer.write(
            content="Remember to check the API first",
            confidence=0.9,
            target="session-42",
            agent_id="agent-1",
        )

        assert result.written is True
        assert result.channel == WriteBackChannel.FAST
        assert result.confidence == 0.9
        assert result.memory_id == "mem-abc"
        assert "[INJECT]" in result.content

        mock_memory_service.store.assert_called_once()
        call_kwargs = mock_memory_service.store.call_args.kwargs
        assert call_kwargs["memory_type"] == MemoryType.WORKING
        assert call_kwargs["scope"] == MemoryScope.AGENT
        assert call_kwargs["agent_id"] == "agent-1"
        assert call_kwargs["session_id"] == "session-42"
        assert call_kwargs["importance"] == 0.9

    @pytest.mark.asyncio
    async def test_write_fast_handles_store_failure(self, writer, mock_memory_service):
        mock_memory_service.store = AsyncMock(side_effect=RuntimeError("store failed"))

        result = await writer.write(
            content="Some content",
            confidence=0.95,
            target="session-1",
        )

        assert result.written is False
        assert result.channel == WriteBackChannel.FAST
        assert result.memory_id == ""

    # ── Medium channel ───────────────────────────────────────────

    @pytest.mark.asyncio
    async def test_write_medium_stores_semantic_memory(self, writer, mock_memory_service):
        result = await writer.write(
            content="The user prefers dark mode interface",
            confidence=0.65,
            target="session-42",
            agent_id="agent-1",
        )

        assert result.written is True
        assert result.channel == WriteBackChannel.MEDIUM
        assert result.confidence == 0.6
        assert result.memory_id == "mem-abc"
        assert "[KEYWORDS]" in result.content
        assert "[CONTENT]" in result.content

        call_kwargs = mock_memory_service.store.call_args.kwargs
        assert call_kwargs["memory_type"] == MemoryType.SEMANTIC
        assert call_kwargs["scope"] == MemoryScope.SESSION

    @pytest.mark.asyncio
    async def test_write_medium_extracts_keywords(self, writer, mock_memory_service):
        await writer._write_medium(
            content="Python programming language is great for AI development",
            target="session-1",
            agent_id="agent-1",
        )

        call_kwargs = mock_memory_service.store.call_args.kwargs
        keywords = call_kwargs["metadata"]["keywords"]
        assert "python" in keywords
        assert "language" in keywords
        assert "ai" in keywords or "development" in keywords

    def test_extract_keywords_filters_stop_words(self, writer):
        keywords = writer._extract_keywords(
            "The quick brown fox jumps over the lazy dog"
        )
        # "the", "over", "dog" should be filtered; "quick", "brown", "fox", "jumps", "lazy" remain
        assert "the" not in keywords
        assert "quick" in keywords

    def test_extract_keywords_returns_top_10(self, writer):
        content = " ".join(["word"] * 15)
        keywords = writer._extract_keywords(content)
        assert len(keywords) <= 10

    # ── Slow channel ─────────────────────────────────────────────

    @pytest.mark.asyncio
    async def test_write_slow_calls_llm_and_stores_episodic(
        self, writer_with_llm, mock_memory_service, mock_llm_client
    ):
        result = await writer_with_llm.write(
            content="Long conversation about system architecture and design patterns",
            confidence=0.3,
            target="session-42",
            agent_id="agent-1",
        )

        assert result.written is True
        assert result.channel == WriteBackChannel.SLOW
        assert result.confidence == 0.3
        assert result.memory_id == "mem-abc"

        # Verify LLM was called
        mock_llm_client.agenerate.assert_called_once()
        call_kwargs = mock_memory_service.store.call_args.kwargs
        assert call_kwargs["memory_type"] == MemoryType.EPISODIC
        assert call_kwargs["scope"] == MemoryScope.SESSION

    @pytest.mark.asyncio
    async def test_write_slow_without_llm_skips(self, writer, mock_memory_service):
        result = await writer.write(
            content="Some content requiring summarization",
            confidence=0.3,
            target="session-42",
        )

        assert result.written is False
        assert result.channel == WriteBackChannel.SLOW
        mock_memory_service.store.assert_not_called()

    @pytest.mark.asyncio
    async def test_write_slow_llm_failure_fallback(
        self, writer_with_llm, mock_memory_service, mock_llm_client
    ):
        mock_llm_client.agenerate = AsyncMock(side_effect=RuntimeError("LLM down"))

        result = await writer_with_llm.write(
            content="This is a very long content piece that needs summarization",
            confidence=0.2,
            target="session-1",
        )

        # Should fallback to first 100 chars
        assert result.written is True
        assert result.content == "This is a very long content piece that needs summarization"[:100]

    # ── Full write() routing ─────────────────────────────────────

    @pytest.mark.asyncio
    async def test_write_routes_to_fast(self, writer, mock_memory_service):
        await writer.write(content="high confidence info", confidence=0.85, target="s1")
        call_kwargs = mock_memory_service.store.call_args.kwargs
        assert call_kwargs["memory_type"] == MemoryType.WORKING

    @pytest.mark.asyncio
    async def test_write_routes_to_medium(self, writer, mock_memory_service):
        await writer.write(content="medium confidence info", confidence=0.6, target="s1")
        call_kwargs = mock_memory_service.store.call_args.kwargs
        assert call_kwargs["memory_type"] == MemoryType.SEMANTIC

    @pytest.mark.asyncio
    async def test_write_routes_to_slow(self, writer_with_llm, mock_memory_service):
        await writer_with_llm.write(content="low confidence info", confidence=0.3, target="s1")
        call_kwargs = mock_memory_service.store.call_args.kwargs
        assert call_kwargs["memory_type"] == MemoryType.EPISODIC
