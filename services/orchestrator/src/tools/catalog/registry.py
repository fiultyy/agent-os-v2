"""
SkillRegistry - Skill 注册表

管理所有已注册的 skill，提供：
- 注册/注销 skill
- 按名称/类型查询 skill
- 版本管理
- 依赖解析
"""

import os
from pathlib import Path
from typing import Dict, Any, List, Optional, Set
from dataclasses import dataclass, field
from datetime import datetime


@dataclass
class SkillMetadata:
    """Skill 元数据"""
    name: str
    version: str
    description: str
    author: str = "unknown"
    tools: List[str] = field(default_factory=list)
    dependencies: List[str] = field(default_factory=list)
    config_schema: Optional[Dict[str, Any]] = None
    path: str = ""
    loaded_at: Optional[str] = None
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "version": self.version,
            "description": self.description,
            "author": self.author,
            "tools": self.tools,
            "dependencies": self.dependencies,
            "config_schema": self.config_schema,
            "path": self.path,
            "loaded_at": self.loaded_at,
        }


class SkillRegistry:
    """
    Skill 注册表
    
    管理所有已注册的 skill，支持：
    - 内置 skill (src/tools/skill/)
    - 用户 skill (~/.agent-os/skills/)
    - 插件 skill (~/.agent-os/plugins/)
    """
    
    # 默认 skill 目录
    DEFAULT_BUILTIN_PATH = Path(__file__).parent.parent / "skill"
    DEFAULT_USER_PATH = Path.home() / ".agent-os" / "skills"
    DEFAULT_PLUGIN_PATH = Path.home() / ".agent-os" / "plugins"
    
    def __init__(self):
        self._skills: Dict[str, SkillMetadata] = {}
        self._versions: Dict[str, Dict[str, SkillMetadata]] = {}  # name -> version -> metadata
        self._categories: Dict[str, List[str]] = {}  # category -> skill names
    
    def register(self, metadata: SkillMetadata) -> None:
        """
        注册 skill
        
        Args:
            metadata: Skill 元数据
        """
        name = metadata.name
        version = metadata.version
        
        if name not in self._versions:
            self._versions[name] = {}
        
        self._versions[name][version] = metadata
        self._skills[name] = metadata  # Latest version
        
        # 更新分类
        category = self._get_category(name)
        if category not in self._categories:
            self._categories[category] = []
        if name not in self._categories[category]:
            self._categories[category].append(name)
        
        metadata.loaded_at = datetime.utcnow().isoformat() + "Z"
    
    def unregister(self, name: str, version: Optional[str] = None) -> bool:
        """
        注销 skill
        
        Args:
            name: Skill 名称
            version: 指定版本，默认移除所有版本
        
        Returns:
            是否成功移除
        """
        if name not in self._versions:
            return False
        
        if version is None:
            # 移除所有版本
            del self._versions[name]
            if name in self._skills:
                del self._skills[name]
        else:
            if version in self._versions[name]:
                del self._versions[name][version]
                if self._skills.get(name) == self._versions[name].get(version):
                    # 如果移除的是当前版本，更新为最新
                    if self._versions[name]:
                        latest = max(self._versions[name].keys())
                        self._skills[name] = self._versions[name][latest]
                    else:
                        del self._skills[name]
        
        return True
    
    def get(self, name: str, version: Optional[str] = None) -> Optional[SkillMetadata]:
        """
        获取 skill
        
        Args:
            name: Skill 名称
            version: 指定版本，默认返回最新
        
        Returns:
            Skill 元数据或 None
        """
        if name not in self._versions:
            return None
        
        if version is None:
            return self._skills.get(name)
        else:
            return self._versions[name].get(version)
    
    def list(self, category: Optional[str] = None) -> List[SkillMetadata]:
        """
        列出 skill
        
        Args:
            category: 分类筛选，默认返回全部
        
        Returns:
            Skill 列表
        """
        if category is None:
            return list(self._skills.values())
        else:
            names = self._categories.get(category, [])
            return [self._skills[name] for name in names if name in self._skills]
    
    def list_names(self, category: Optional[str] = None) -> List[str]:
        """
        列出 skill 名称
        
        Args:
            category: 分类筛选
        
        Returns:
            Skill 名称列表
        """
        if category is None:
            return list(self._skills.keys())
        else:
            return self._categories.get(category, [])
    
    def list_versions(self, name: str) -> List[str]:
        """
        列出 skill 所有版本
        
        Args:
            name: Skill 名称
        
        Returns:
            版本列表
        """
        if name not in self._versions:
            return []
        return list(self._versions[name].keys())
    
    def get_dependencies(self, name: str, version: Optional[str] = None) -> Set[str]:
        """
        获取 skill 的依赖（递归）
        
        Args:
            name: Skill 名称
            version: 指定版本
        
        Returns:
            依赖的 skill 名称集合
        """
        metadata = self.get(name, version)
        if not metadata:
            return set()
        
        deps = set(metadata.dependencies)
        for dep in metadata.dependencies:
            deps.update(self.get_dependencies(dep))
        
        return deps
    
    def _get_category(self, name: str) -> str:
        """根据 skill 名称推断分类"""
        if "browser" in name.lower():
            return "browser"
        elif "code" in name.lower():
            return "code"
        elif "memory" in name.lower():
            return "memory"
        elif "http" in name.lower() or "file" in name.lower() or "db" in name.lower():
            return "primitive"
        else:
            return "misc"
    
    def clear(self) -> None:
        """清空注册表"""
        self._skills.clear()
        self._versions.clear()
        self._categories.clear()


# 全局注册表实例
_global_registry = SkillRegistry()


def get_registry() -> SkillRegistry:
    """获取全局注册表实例"""
    return _global_registry