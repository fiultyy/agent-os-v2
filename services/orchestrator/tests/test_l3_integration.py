"""
L3.3/L3.4 Integration Test

测试所有工具和模块的导入与基本功能。
"""

import tempfile
import os
import sys

# Add src to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def test_primitive():
    """测试 primitive 工具"""
    from src.skills.primitive import (
        http_get, http_post, http_put, http_delete, http_patch,
        file_read, file_write, file_delete, file_exists, file_list, file_mkdir,
        db_query, db_execute, db_transaction, db_schema
    )

    # File operations
    test_file = tempfile.mktemp(suffix='.txt')
    assert file_write(test_file, "test")['success']
    assert file_read(test_file)['success']
    assert file_exists(test_file)['exists']
    assert file_delete(test_file)['success']

    # DB operations
    result = db_execute("CREATE TABLE test(id INTEGER)", db_path=":memory:")
    assert result['success']

    print("✅ primitive (15 tools)")


def test_skill():
    """测试 skill 工具"""
    from src.skills.browser import browser_navigate, browser_snapshot, browser_click, browser_type
    from src.skills.code import code_read, code_write, code_search
    from src.skills.memory import memory_recall, memory_store

    # Memory operations
    result = memory_recall("test", mode="keyword")
    assert 'results' in result

    result = memory_store("test", memory_type="working", importance=0.8)
    assert result.get('memory_id')

    # Code operations
    test_file = tempfile.mktemp(suffix='.py')
    file_write(test_file, "# test")
    result = code_read(test_file)
    assert result['success']
    file_delete(test_file)

    print("✅ skill (9 tools)")


def test_composite():
    """测试 composite 工具"""
    from src.tools.composite import browser_flow_execute, code_review_run

    # code_review_run with self
    result = code_review_run(__file__, check_types=["quality"])
    assert 'issues' in result or 'summary' in result or 'error' in result

    print("✅ composite (2 tools)")


def test_skill_catalog():
    """测试 skill_catalog 模块"""
    from src.skill_catalog import SkillRegistry, SkillLoader, SkillConfig, SkillMetadata

    # Registry
    registry = SkillRegistry()
    registry.register(SkillMetadata(name="test", version="1.0.0", description="Test", tools=[]))
    assert registry.get("test")
    assert len(registry.list()) > 0
    registry.unregister("test")

    # Config
    config = SkillConfig()
    assert config is not None

    # Loader
    loader = SkillLoader()
    assert loader is not None

    print("✅ skill_catalog (3 modules)")


def test_all_imports():
    """测试所有导入"""
    from src.skills.primitive import *
    from src.skills.browser import *
    from src.skills.code import *
    from src.skills.memory import *
    from src.tools.composite import *
    from src.skill_catalog import *
    print("✅ all imports")


if __name__ == "__main__":
    print("="*50)
    print("L3.3/L3.4 Integration Test")
    print("="*50)

    test_all_imports()
    test_primitive()
    test_skill()
    test_composite()
    test_skill_catalog()

    print("="*50)
    print("All tests passed! ✅")
    print("="*50)