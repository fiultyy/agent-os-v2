#!/usr/bin/env python3
"""Test script to verify the compression engine fix.

Simulates 11 memory items and verifies that compression actually
reduces the item count and produces meaningful summaries.

Run from the orchestrator service directory:
    cd ~/projects/agent-os/services/orchestrator
    python scripts/test_compressor_fix.py
"""

import asyncio
import sys
import os

# Ensure src is importable
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from src.memory.types import MemoryItem, MemoryType, MemoryScope
from src.memory.compressor import (
    CompressionEngine,
    CompressionLevel,
    ContextMonitor,
    AsyncCompressor,
    SyncCompressor,
    _is_reasoning_chain,
)


def make_item(
    content: str,
    importance: float = 0.5,
    agent_id: str = "agent-1",
    session_id: str = "sess-1",
) -> MemoryItem:
    """Create a test MemoryItem."""
    return MemoryItem(
        id=f"mem-{abs(hash(content)) % 100000:05d}",
        agent_id=agent_id,
        session_id=session_id,
        memory_type=MemoryType.SESSION,
        scope=MemoryScope.AGENT,
        content=content,
        importance=importance,
    )


def test_is_reasoning_chain() -> None:
    """Test _is_reasoning_chain accuracy."""
    print("=" * 60)
    print("TEST 1: _is_reasoning_chain accuracy")
    print("=" * 60)

    cases = [
        # (content, expected_reasoning)
        ("Step 1: Analyze the problem", False),  # single step, short
        ("This is a regular conversation message about the weather", False),
        ("Because the user asked for help, we decided to fix the bug", False),
        (
            "Step 1: First we analyze the input data. "
            "Step 2: Then we process it through the pipeline. "
            "Step 3: Finally we output the results to the user.",
            True,  # multi-step reasoning in one item
        ),
        (
            "Firstly we need to gather requirements. "
            "Secondly we design the architecture. "
            "Finally we implement the solution.",
            True,  # ordered transition words
        ),
        (
            "假设用户的输入是正确的，因此我们可以直接处理。"
            "推理过程表明这个结论是可行的，我们可以继续执行下一步操作。",
            True,  # Chinese reasoning chain
        ),
        (
            "User asked about deploying the service. "
            "I responded with instructions on how to use docker.",
            False,  # normal conversation
        ),
    ]

    all_pass = True
    for content, expected in cases:
        result = _is_reasoning_chain(content)
        status = "✅" if result == expected else "❌"
        if result != expected:
            all_pass = False
        print(f"  {status} len={len(content):3d}  expected={expected}  got={result}")
        if len(content) < 80:
            print(f"      content: {content[:60]}...")

    print()
    return all_pass


def test_compress_11_items() -> None:
    """Test that 11 items get compressed (NOT 11→11)."""
    print("=" * 60)
    print("TEST 2: 11 items compression (the core fix)")
    print("=" * 60)

    engine = CompressionEngine()
    items = [
        make_item("User asked about deploying the service to production", importance=0.4),
        make_item("Assistant provided Docker deployment instructions", importance=0.3),
        make_item("User reported a bug in the authentication module", importance=0.7),
        make_item("Assistant identified the root cause as a JWT expiry issue", importance=0.6),
        make_item("User requested performance optimization for the API", importance=0.3),
        make_item("Assistant suggested adding Redis caching layer", importance=0.5),
        make_item("User confirmed the caching approach looks good", importance=0.2),
        make_item("Discussion about API rate limiting strategy", importance=0.35),
        make_item("Summary of Sprint 3: completed 12 story points", importance=0.25),
        make_item("User feedback on the new dashboard UI design", importance=0.4),
        make_item("Team decided to use React Flow for the canvas component", importance=0.45),
    ]

    print(f"\n  Input: {len(items)} items")
    for i, item in enumerate(items):
        print(f"    [{i}] importance={item.importance:.2f}  {item.content[:50]}...")

    retained, summaries = engine.compress_items(items, target_ratio=0.3)

    print(f"\n  Output: {len(retained)} retained, {len(summaries)} summaries")
    print(f"  Total items after compression: {len(retained) + len(summaries)}")

    print("\n  Retained items:")
    for item in retained:
        print(f"    importance={item.importance:.2f}  {item.content[:60]}...")

    print("\n  Summaries:")
    for s in summaries:
        content_preview = s.content[:80].replace("\n", " ")
        print(f"    [{s.metadata.get('compression_type', '?')}] {content_preview}...")

    # Assertions
    all_pass = True

    if len(retained) >= len(items):
        print(f"\n  ❌ FAIL: retained {len(retained)} >= original {len(items)} (空转!)")
        all_pass = False
    else:
        print(f"\n  ✅ retained ({len(retained)}) < original ({len(items)})")

    if len(summaries) == 0:
        print(f"  ❌ FAIL: 0 summaries generated (空转!)")
        all_pass = False
    else:
        print(f"  ✅ {len(summaries)} summaries generated")

    if len(retained) + len(summaries) > len(items):
        print(f"  ⚠️  WARNING: total ({len(retained) + len(summaries)}) > original ({len(items)})")
    else:
        print(f"  ✅ total compressed size <= original")

    # Check summaries are meaningful
    for s in summaries:
        if not s.content or s.content == "":
            print(f"  ❌ FAIL: empty summary content")
            all_pass = False
        elif s.content.startswith("[Summary]"):
            print(f"  ✅ Summary has [Summary] prefix")
        else:
            print(f"  ✅ Summary has content (len={len(s.content)})")

    print()
    return all_pass


def test_reasoning_items_get_boost() -> None:
    """Test that reasoning items get an importance boost but can still be compressed."""
    print("=" * 60)
    print("TEST 3: Reasoning items get boost but NOT total exemption")
    print("=" * 60)

    engine = CompressionEngine(reasoning_boost=0.15)

    # Create a mix where some reasoning items have LOW importance
    items = [
        # 3 reasoning-chain items with low importance (but will get boost)
        make_item(
            "Step 1: First we analyze the input data carefully. "
            "Step 2: Then we process it through the pipeline step by step. "
            "Step 3: Finally we output the results to the user for review.",
            importance=0.2,
        ),
        make_item(
            "Firstly we need to gather all requirements from stakeholders. "
            "Secondly we design the system architecture carefully. "
            "Finally we implement the complete solution.",
            importance=0.25,
        ),
        # 8 regular items with higher importance
        make_item("Critical bug fix for authentication", importance=0.9),
        make_item("Deploy service to production cluster", importance=0.8),
        make_item("Performance optimization results", importance=0.7),
        make_item("Database migration completed", importance=0.65),
        make_item("API rate limiting configuration", importance=0.6),
        make_item("User feedback on new feature", importance=0.55),
        make_item("Monitoring dashboard setup", importance=0.5),
        make_item("Sprint retrospective notes", importance=0.45),
    ]

    print(f"\n  Input: {len(items)} items (2 low-importance reasoning, 8 high-importance regular)")
    retained, summaries = engine.compress_items(items, target_ratio=0.3)

    print(f"  Output: {len(retained)} retained, {len(summaries)} summaries")

    all_pass = True

    if len(retained) >= len(items):
        print(f"  ❌ FAIL: retained {len(retained)} >= original {len(items)}")
        all_pass = False
    else:
        print(f"  ✅ retained ({len(retained)}) < original ({len(items)})")

    if len(summaries) == 0:
        print(f"  ❌ FAIL: 0 summaries (空转!)")
        all_pass = False
    else:
        print(f"  ✅ {len(summaries)} summaries generated")

    # Check that low-importance reasoning items were NOT automatically retained
    # (they get a 0.15 boost, so effective importance is 0.35/0.40, which should
    # still be below the top items)
    reasoning_retained = sum(
        1 for r in retained if _is_reasoning_chain(r.content)
    )
    print(f"  Reasoning items retained: {reasoning_retained}/2")
    if reasoning_retained == 2:
        print("  ⚠️  Both low-importance reasoning items retained (boost may be too high)")
    else:
        print(f"  ✅ Not all reasoning items auto-retained")

    print()
    return all_pass


async def test_async_compressor() -> None:
    """Test async compressor returns actual results."""
    print("=" * 60)
    print("TEST 4: Async compressor captures results via callback")
    print("=" * 60)

    engine = CompressionEngine()
    monitor = ContextMonitor(max_context_tokens=1000)
    compressor = AsyncCompressor(engine=engine, monitor=monitor)

    items = [make_item(f"Regular item {i} about system monitoring", importance=0.3 + i * 0.05)
             for i in range(11)]

    print(f"\n  Input: {len(items)} items")

    callback_called = False
    callback_retained = []
    callback_summaries = []

    async def on_compressed(retained, summaries):
        nonlocal callback_called, callback_retained, callback_summaries
        callback_called = True
        callback_retained = retained
        callback_summaries = summaries

    result = await compressor.trigger(items, on_compressed=on_compressed)
    print(f"  Immediate result: original={result.original_count}, compressed={result.compressed_count}")

    # Wait for background task
    final = await compressor.wait(timeout=5.0)

    if final is not None:
        print(f"  Final result: original={final.original_count}, "
              f"retained={len(final.retained)}, summaries={len(final.summaries)}")

    all_pass = True
    if not callback_called:
        print(f"  ❌ FAIL: callback was not called")
        all_pass = False
    else:
        print(f"  ✅ Callback was called with {len(callback_retained)} retained, {len(callback_summaries)} summaries")

    if callback_summaries and len(callback_summaries) > 0:
        print(f"  ✅ {len(callback_summaries)} summaries generated in background")
    elif final and len(final.summaries) > 0:
        print(f"  ✅ {len(final.summaries)} summaries in final result")
    else:
        print(f"  ❌ No summaries generated")
        all_pass = False

    print()
    return all_pass


async def test_sync_compressor() -> None:
    """Test sync compressor with new result fields."""
    print("=" * 60)
    print("TEST 5: Sync compressor returns retained + summaries")
    print("=" * 60)

    engine = CompressionEngine()
    monitor = ContextMonitor(max_context_tokens=1000)
    compressor = SyncCompressor(engine=engine, monitor=monitor)

    items = [make_item(f"Item {i}: system observation data", importance=0.3 + i * 0.04)
             for i in range(11)]

    print(f"\n  Input: {len(items)} items")

    result = await compressor.compress(items)
    print(f"  Result: original={result.original_count}, "
          f"retained={len(result.retained)}, summaries={len(result.summaries)}")

    all_pass = True
    if len(result.retained) >= result.original_count:
        print(f"  ❌ FAIL: retained >= original (空转!)")
        all_pass = False
    else:
        print(f"  ✅ retained ({len(result.retained)}) < original ({result.original_count})")

    if len(result.summaries) == 0:
        print(f"  ❌ FAIL: 0 summaries (空转!)")
        all_pass = False
    else:
        print(f"  ✅ {len(result.summaries)} summaries generated")

    print()
    return all_pass


def test_target_ratio_enforcement() -> None:
    """Test that target_ratio is actually enforced."""
    print("=" * 60)
    print("TEST 6: target_ratio enforcement")
    print("=" * 60)

    engine = CompressionEngine()
    all_pass = True

    for ratio in [0.2, 0.3, 0.5]:
        items = [make_item(f"Item {i}", importance=0.5) for i in range(20)]
        retained, summaries = engine.compress_items(items, target_ratio=ratio)

        expected_max = max(1, int(20 * ratio))
        print(f"\n  ratio={ratio}: expected_max={expected_max}, "
              f"retained={len(retained)}, summaries={len(summaries)}")

        if len(retained) > expected_max:
            print(f"    ❌ FAIL: retained {len(retained)} > expected_max {expected_max}")
            all_pass = False
        else:
            print(f"    ✅ retained <= expected_max")

        if len(summaries) == 0:
            print(f"    ❌ FAIL: 0 summaries")
            all_pass = False
        else:
            print(f"    ✅ {len(summaries)} summaries")

    print()
    return all_pass


async def main() -> None:
    """Run all tests."""
    print("\n" + "=" * 60)
    print("  COMPRESSION ENGINE FIX VERIFICATION")
    print("=" * 60 + "\n")

    results = []

    results.append(("reasoning_chain_accuracy", test_is_reasoning_chain()))
    results.append(("compress_11_items", test_compress_11_items()))
    results.append(("reasoning_boost_not_exemption", test_reasoning_items_get_boost()))
    results.append(("async_compressor", await test_async_compressor()))
    results.append(("sync_compressor", await test_sync_compressor()))
    results.append(("target_ratio_enforcement", test_target_ratio_enforcement()))

    print("=" * 60)
    print("  SUMMARY")
    print("=" * 60)

    all_pass = True
    for name, passed in results:
        status = "✅ PASS" if passed else "❌ FAIL"
        print(f"  {status}  {name}")
        if not passed:
            all_pass = False

    print()
    if all_pass:
        print("  🎉 All tests passed!")
    else:
        print("  ⚠️  Some tests failed — see details above")
    print()

    return 0 if all_pass else 1


if __name__ == "__main__":
    exit(asyncio.run(main()))
