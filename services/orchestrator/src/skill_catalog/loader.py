"""
SkillLoader - Skill 发现与加载器

从文件系统发现并加载 Skill 包：
- 内置 Skill: src/skills/
- 用户 Skill: ~/.agent-os/skills/
- 插件 Skill: ~/.agent-os/plugins/
"""

import importlib.util
import os
from pathlib import Path
from typing import Dict, List, Optional

from .registry import SkillMetadata, get_registry


class SkillLoader:
    """
    Skill 加载器
    
    负责：
    1. 发现 Skill 包（扫描目录）
    2. 加载 Skill 元数据
    3. 注册到 registry
    4. 支持热插拔（用户/插件）
    """
    
    # 内置 Skill 目录
    BUILTIN_SKILLS_PATH = Path(__file__).parent.parent / "skills"
    USER_SKILLS_PATH = Path.home() / ".agent-os" / "skills"
    PLUGIN_SKILLS_PATH = Path.home() / ".agent-os" / "plugins"
    
    def __init__(self, registry=None):
        self.registry = registry or get_registry()
        self._loaded: set = set()
    
    def load_builtin_skills(self) -> Dict[str, int]:
        """
        加载所有内置 Skill
        
        Returns:
            {category: count}
        """
        result = {}
        
        # 扫描 skills/ 下的各目录
        if not self.BUILTIN_SKILLS_PATH.exists():
            return result
        
        for skill_dir in self.BUILTIN_SKILLS_PATH.iterdir():
            if not skill_dir.is_dir():
                continue
            if skill_dir.name.startswith("."):
                continue
            
            metadata = self._discover_skill(skill_dir, skill_type="builtin")
            if metadata:
                self.registry.register(metadata)
                self._loaded.add(metadata.name)
                cat = metadata.name.split("_")[0] if "_" in metadata.name else "misc"
                result[cat] = result.get(cat, 0) + 1
        
        return result
    
    def load_user_skills(self) -> int:
        """
        加载用户 Skill (~/.agent-os/skills/)
        
        Returns:
            加载数量
        """
        if not self.USER_SKILLS_PATH.exists():
            return 0
        
        count = 0
        for skill_dir in self.USER_SKILLS_PATH.iterdir():
            if not skill_dir.is_dir():
                continue
            if skill_dir.name.startswith("."):
                continue
            
            metadata = self._discover_skill(skill_dir, skill_type="user")
            if metadata and metadata.name not in self._loaded:
                self.registry.register(metadata)
                self._loaded.add(metadata.name)
                count += 1
        
        return count
    
    def load_plugin_skills(self) -> int:
        """
        加载插件 Skill (~/.agent-os/plugins/)
        
        Returns:
            加载数量
        """
        if not self.PLUGIN_SKILLS_PATH.exists():
            return 0
        
        count = 0
        for plugin_dir in self.PLUGIN_SKILLS_PATH.iterdir():
            if not plugin_dir.is_dir():
                continue
            if plugin_dir.name.startswith("."):
                continue
            
            # 在插件目录下找 skill.yaml
            skill_yaml = plugin_dir / "skill.yaml"
            if skill_yaml.exists():
                metadata = self._load_yaml_metadata(skill_yaml, skill_type="plugin")
                if metadata and metadata.name not in self._loaded:
                    self.registry.register(metadata)
                    self._loaded.add(metadata.name)
                    count += 1
        
        return count
    
    def load_all(self) -> Dict[str, int]:
        """
        加载所有 Skill（内置 + 用户 + 插件）
        
        优先级：用户 > 插件 > 内置
        """
        return {
            "builtin": sum(self.load_builtin_skills().values()),
            "user": self.load_user_skills(),
            "plugin": self.load_plugin_skills(),
        }
    
    def unload(self, skill_name: str) -> bool:
        """卸载 Skill"""
        if skill_name in self._loaded:
            self._loaded.remove(skill_name)
        return self.registry.unregister(skill_name)
    
    def reload(self, skill_name: str) -> bool:
        """重新加载 Skill"""
        self.unload(skill_name)
        # 重新发现并注册
        for search_path in [self.USER_SKILLS_PATH, self.PLUGIN_SKILLS_PATH, self.BUILTIN_SKILLS_PATH]:
            skill_dir = search_path / skill_name
            if skill_dir.exists():
                skill_type = "user" if search_path == self.USER_SKILLS_PATH else "plugin" if search_path == self.PLUGIN_SKILLS_PATH else "builtin"
                metadata = self._discover_skill(skill_dir, skill_type)
                if metadata:
                    self.registry.register(metadata)
                    self._loaded.add(metadata.name)
                    return True
        return False
    
    def _discover_skill(self, skill_dir: Path, skill_type: str) -> Optional[SkillMetadata]:
        """
        发现 Skill 包
        
        搜索顺序：
        1. skill.yaml（元数据文件）
        2. __init__.py（Python 包）
        """
        # 检查 skill.yaml
        skill_yaml = skill_dir / "skill.yaml"
        if skill_yaml.exists():
            return self._load_yaml_metadata(skill_yaml, skill_type)
        
        # 检查 __init__.py
        init_file = skill_dir / "__init__.py"
        if init_file.exists():
            # 从目录名推断元数据
            return SkillMetadata(
                name=skill_dir.name,
                version="1.0.0",
                description=f"{skill_dir.name} skill package",
                author="builtin" if skill_type == "builtin" else "user",
                tools=self._discover_tools(skill_dir),
                path=str(skill_dir),
                skill_type=skill_type,
            )
        
        return None
    
    def _load_yaml_metadata(self, yaml_path: Path, skill_type: str) -> Optional[SkillMetadata]:
        """从 skill.yaml 加载元数据"""
        try:
            import yaml
            with open(yaml_path, "r", encoding="utf-8") as f:
                data = yaml.safe_load(f) or {}
            
            return SkillMetadata(
                name=data.get("name", yaml_path.parent.name),
                version=data.get("version", "1.0.0"),
                description=data.get("description", ""),
                author=data.get("author", "builtin" if skill_type == "builtin" else "user"),
                tools=data.get("tools", []),
                dependencies=data.get("dependencies", []),
                path=str(yaml_path.parent),
                skill_type=skill_type,
            )
        except Exception:
            return None
    
    def _discover_tools(self, skill_dir: Path) -> List[str]:
        """发现 Skill 包中的工具"""
        tools = []
        for py_file in skill_dir.glob("*.py"):
            if py_file.name.startswith("_"):
                continue
            if py_file.name == "__init__.py":
                continue
            # 从文件名推断工具名
            tool_name = py_file.stem
            tools.append(tool_name)
        return tools


# 默认加载器
_default_loader: Optional[SkillLoader] = None


def get_loader() -> SkillLoader:
    """获取默认加载器实例"""
    global _default_loader
    if _default_loader is None:
        _default_loader = SkillLoader()
    return _default_loader