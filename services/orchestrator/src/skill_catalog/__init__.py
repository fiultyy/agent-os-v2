"""
Skill Catalog - Skill 管理器
管理 skills/ 目录下的所有 Skill 包

与 skills/ 目录分离：
- skills/ - Skill 内容包（用户可编辑）
- skill_catalog/ - Skill 管理器代码（非用户可编辑区域）
"""

from .registry import SkillRegistry, SkillMetadata
from .loader import SkillLoader, get_loader
from .config import SkillConfig, get_config

__all__ = [
    "SkillRegistry",
    "SkillMetadata",
    "SkillLoader",
    "get_loader",
    "SkillConfig",
    "get_config",
]