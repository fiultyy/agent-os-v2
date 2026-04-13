"""
Hello World Skill - 示例用户 Skill

展示 Skill 包的标准结构：
- skill.yaml: 元数据
- __init__.py: 包入口
- greet.py: 问候工具
- farewell.py: 告别工具
"""

from .greet import greet
from .farewell import farewell

__all__ = ["greet", "farewell"]
__version__ = "1.0.0"