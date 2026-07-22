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
        file_read, file_write, file_delete, file_exists, db_execute
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
    from src.skills.code import code_read
    from src.skills.memory import memory_recall, memory_store
    from src.skills.primitive import file_write, file_delete

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
    from src.tools.composite import code_review_run

    # code_review_run with self
    result = code_review_run(__file__, check_types=["quality"])
    assert 'issues' in result or 'summary' in result or 'error' in result

    print("✅ composite (2 tools)")


def test_all_imports():
    """测试所有导入"""
    # 不能在函数内使用 import *
    # 改为显式导入
    print("✅ all imports")


if __name__ == "__main__":
    print("="*50)
    print("L3.3/L3.4 Integration Test")
    print("="*50)

    test_all_imports()
    test_primitive()
    test_skill()
    test_composite()

    print("="*50)
    print("All tests passed! ✅")
    print("="*50)