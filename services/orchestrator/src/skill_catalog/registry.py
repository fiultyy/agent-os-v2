"""
SkillRegistry - Skill 注册表

管理所有已注册的 Skill 包，提供：
- 注册/注销 Skill
- 按名称/类型查询
- 版本管理
- 依赖解析
"""

from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Set


@dataclass
class SkillMetadata:
    """Skill 包元数据"""
    name: str
    version: str
    description: str
    author: str = "builtin"
    tools: List[str] = field(default_factory=list)
    dependencies: List[str] = field(default_factory=list)
    path: str = ""
    skill_type: str = "builtin"  # builtin, user, plugin
    loaded_at: Optional[str] = None

    def to_dict(self) -> Dict:
        return {
            "name": self.name,
            "version": self.version,
            "description": self.description,
            "author": self.author,
            "tools": self.tools,
            "dependencies": self.dependencies,
            "path": self.path,
            "skill_type": self.skill_type,
            "loaded_at": self.loaded_at,
        }


class SkillRegistry:
    """
    Skill 注册表
    
    管理内置 Skill 和用户扩展 Skill：
    - 内置 Skill: skills/primitive, skills/browser, skills/code, skills/memory
    - 用户 Skill: ~/.agent-os/skills/
    - 插件 Skill: ~/.agent-os/plugins/
    """
    
    def __init__(self):
        self._skills: Dict[str, SkillMetadata] = {}  # name -> latest metadata
        self._versions: Dict[str, Dict[str, SkillMetadata]] = {}  # name -> version -> metadata
        self._categories: Dict[str, List[str]] = {}  # category -> skill names
    
    def register(self, metadata: SkillMetadata) -> None:
        """注册 Skill"""
        name = metadata.name
        version = metadata.version
        
        if name not in self._versions:
            self._versions[name] = {}
        
        self._versions[name][version] = metadata
        self._skills[name] = metadata
        
        # 更新分类
        category = self._get_category(name)
        if category not in self._categories:
            self._categories[category] = []
        if name not in self._categories[category]:
            self._categories[category].append(name)
        
        metadata.loaded_at = datetime.utcnow().isoformat() + "Z"
    
    def unregister(self, name: str) -> bool:
        """注销 Skill"""
        if name not in self._skills:
            return False
        
        del self._skills[name]
        if name in self._versions:
            del self._versions[name]
        
        for category in self._categories:
            if name in self._categories[category]:
                self._categories[category].remove(name)
        
        return True
    
    def get(self, name: str, version: Optional[str] = None) -> Optional[SkillMetadata]:
        """获取 Skill 元数据"""
        if name not in self._versions:
            return None
        if version is None:
            return self._skills.get(name)
        return self._versions[name].get(version)
    
    def list(self, category: Optional[str] = None) -> List[SkillMetadata]:
        """列出所有或指定分类的 Skill"""
        if category is None:
            return list(self._skills.values())
        names = self._categories.get(category, [])
        return [self._skills[n] for n in names if n in self._skills]
    
    def list_names(self, category: Optional[str] = None) -> List[str]:
        """列出 Skill 名称"""
        if category is None:
            return list(self._skills.keys())
        return self._categories.get(category, [])
    
    def list_by_type(self, skill_type: str) -> List[SkillMetadata]:
        """按类型列出 Skill (builtin/user/plugin)"""
        return [s for s in self._skills.values() if s.skill_type == skill_type]
    
    def get_dependencies(self, name: str) -> Set[str]:
        """获取 Skill 依赖（递归）"""
        metadata = self.get(name)
        if not metadata:
            return set()
        
        deps = set(metadata.dependencies)
        for dep in metadata.dependencies:
            deps.update(self.get_dependencies(dep))
        return deps
    
    def _get_category(self, name_or_metadata) -> str:
        """推断 Skill 分类

        支持传入 SkillMetadata 或名称字符串：
        - SkillMetadata: 优先使用显式字段，其次从 tools 推断，最后回退名称匹配
        - str: 纯名称推断（向后兼容）
        """
        # 如果传入的是 SkillMetadata，优先使用显式字段和 tools 推断
        if isinstance(name_or_metadata, SkillMetadata):
            metadata = name_or_metadata
            # 优先使用显式字段
            if hasattr(metadata, 'category') and getattr(metadata, 'category', None):
                return metadata.category
            # 从 tools 列表推断
            tools = metadata.tools or []
            if any('browser' in t.lower() for t in tools):
                return "browser"
            if any('code' in t.lower() for t in tools):
                return "code"
            if any('memory' in t.lower() for t in tools):
                return "memory"
            if any(t in ['http_get', 'http_post', 'file_read', 'db_query'] for t in tools):
                return "primitive"
            # 回退到名称匹配
            name_lower = metadata.name.lower()
        else:
            name_lower = str(name_or_metadata).lower()

        # 名称推断（原有逻辑）
        if "browser" in name_lower:
            return "browser"
        elif "code" in name_lower:
            return "code"
        elif "memory" in name_lower:
            return "memory"
        elif any(x in name_lower for x in ["http", "file", "db"]):
            return "primitive"
        return "misc"
    
    def clear(self) -> None:
        """清空注册表"""
        self._skills.clear()
        self._versions.clear()
        self._categories.clear()


# 全局注册表
_global_registry = SkillRegistry()


def get_registry() -> SkillRegistry:
    """获取全局注册表实例"""
    return _global_registry