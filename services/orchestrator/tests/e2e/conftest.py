"""E2E conftest — 加载 root conftest(pysqlite3 注入)+ e2e marker 注册。

e2e 测试默认跳过(需 ``-m e2e`` 显式开启),因为它们真调 GLM(耗 token / 慢 /
需网络)。unit 套件 ``pytest tests/unit/`` 不经此目录,零污染。
"""
import sys
from pathlib import Path

# 复用 root conftest 的 pysqlite3 注入(单一份,DRY)。
_root_conftest = Path(__file__).resolve().parent.parent / "conftest.py"
if str(_root_conftest.parent) not in sys.path:
    sys.path.insert(0, str(_root_conftest.parent))


def pytest_configure(config):
    config.addinivalue_line(
        "markers", "e2e: real GLM end-to-end (slow, costs tokens, needs network)"
    )
