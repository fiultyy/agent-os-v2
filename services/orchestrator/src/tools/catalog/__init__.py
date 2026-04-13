"""
Catalog - L3.4 Implementation
Skill 管理器：发现、注册、版本管理、配置

Catalog 负责：
1. Skill 发现 - 扫描内置/用户/插件 skill 目录
2. Skill 注册 - 登记到 registry
3. Skill 版本 - 多版本共存
4. Skill 依赖 - 解析并加载依赖
5. Skill 配置 - 管理 user-configurable 参数
"""

from .registry import SkillRegistry
from .loader import SkillLoader
from .config import SkillConfig

__all__ = [
    "SkillRegistry",
    "SkillLoader",
    "SkillConfig",
]