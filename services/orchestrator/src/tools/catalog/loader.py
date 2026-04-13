"""
SkillLoader - Skill 动态加载器

从文件系统加载 skill：
1. 扫描 skill 目录
2. 读取 skill.yaml 元数据
3. 验证依赖
4. 注册到 registry
"""

import os
import yaml
from pathlib import Path
from typing import Dict, Any, List, Optional

from .registry import SkillMetadata, get_registry


class SkillLoader:
    """
    Skill 动态加载器
    
    负责从文件系统加载 skill，支持：
    - 内置 skill 目录
    - 用户 skill 目录
    - 插件 skill 目录
    - 优先级：用户 > 插件 > 内置
    """
    
    def __init__(self, registry=None):
        self.registry = registry or get_registry()
        self._loaded_paths: set = set()  # 避免重复加载
    
    def scan_directory(self, path: Path, category: str = "user") -> List[SkillMetadata]:
        """
        扫描目录下的所有 skill
        
        Args:
            path: 目录路径
            category: 分类标识（user/plugin/builtin）
        
        Returns:
            加载的 skill 元数据列表
        """
        if not path.exists() or not path.is_dir():
            return []
        
        skills = []
        
        for item in path.iterdir():
            if not item.is_dir():
                continue
            if item.name.startswith(".") or item.name.startswith("_"):
                continue
            
            skill_path = item / "skill.yaml"
            if skill_path.exists():
                metadata = self._load_from_yaml(skill_path, category)
                if metadata:
                    metadata.path = str(skill_path)
                    skills.append(metadata)
            
            # 也支持 skill.yaml 在 skill 目录下
            alt_skill_yaml = item / "skill.yaml"
            if alt_skill_yaml.exists():
                metadata = self._load_from_yaml(alt_skill_yaml, category)
                if metadata:
                    metadata.path = str(alt_skill_yaml)
                    skills.append(metadata)
        
        return skills
    
    def load(self, skill_path: str, category: str = "user") -> Optional[SkillMetadata]:
        """
        加载单个 skill
        
        Args:
            skill_path: skill.yaml 路径或 skill 目录路径
            category: 分类标识
        
        Returns:
            Skill 元数据或 None
        """
        path = Path(skill_path).expanduser().resolve()
        
        if path.is_file() and path.name == "skill.yaml":
            return self._load_from_yaml(path, category)
        elif path.is_dir():
            skill_yaml = path / "skill.yaml"
            if skill_yaml.exists():
                return self._load_from_yaml(skill_yaml, category)
            else:
                # 尝试在目录内搜索
                for item in path.iterdir():
                    if item.is_dir() and (item / "skill.yaml").exists():
                        return self._load_from_yaml(item / "skill.yaml", category)
        
        return None
    
    def load_builtin_skills(self) -> int:
        """
        加载所有内置 skill
        
        Returns:
            加载数量
        """
        builtin_path = Path(__file__).parent.parent / "skill"
        skills = self.scan_directory(builtin_path, category="builtin")
        
        count = 0
        for metadata in skills:
            if metadata.name not in self._loaded_paths:
                self.registry.register(metadata)
                self._loaded_paths.add(metadata.name)
                count += 1
        
        return count
    
    def load_user_skills(self) -> int:
        """
        加载用户 skill 目录 (~/.agent-os/skills/)
        
        Returns:
            加载数量
        """
        user_path = Path.home() / ".agent-os" / "skills"
        skills = self.scan_directory(user_path, category="user")
        
        count = 0
        for metadata in skills:
            if metadata.name not in self._loaded_paths:
                self.registry.register(metadata)
                self._loaded_paths.add(metadata.name)
                count += 1
        
        return count
    
    def load_plugin_skills(self) -> int:
        """
        加载插件 skill 目录 (~/.agent-os/plugins/)
        
        Returns:
            加载数量
        """
        plugin_path = Path.home() / ".agent-os" / "plugins"
        if not plugin_path.exists():
            return 0
        
        count = 0
        for item in plugin_path.iterdir():
            if not item.is_dir():
                continue
            if item.name.startswith("."):
                continue
            
            skill_yaml = item / "skill.yaml"
            if skill_yaml.exists():
                metadata = self._load_from_yaml(skill_yaml, category="plugin")
                if metadata and metadata.name not in self._loaded_paths:
                    self.registry.register(metadata)
                    self._loaded_paths.add(metadata.name)
                    count += 1
        
        return count
    
    def load_all(self) -> Dict[str, int]:
        """
        加载所有 skill（内置 + 用户 + 插件）
        
        Returns:
            各类别加载数量
        """
        result = {
            "builtin": self.load_builtin_skills(),
            "user": self.load_user_skills(),
            "plugin": self.load_plugin_skills(),
        }
        return result
    
    def unload(self, skill_name: str) -> bool:
        """
        卸载 skill
        
        Args:
            skill_name: Skill 名称
        
        Returns:
            是否成功
        """
        if skill_name in self._loaded_paths:
            self._loaded_paths.remove(skill_name)
        return self.registry.unregister(skill_name)
    
    def _load_from_yaml(self, yaml_path: Path, category: str) -> Optional[SkillMetadata]:
        """
        从 YAML 文件加载 skill 元数据
        
        Args:
            yaml_path: skill.yaml 路径
            category: 分类标识
        
        Returns:
            Skill 元数据或 None
        """
        try:
            with open(yaml_path, "r", encoding="utf-8") as f:
                data = yaml.safe_load(f)
            
            if not data:
                return None
            
            # 解析元数据
            metadata = SkillMetadata(
                name=data.get("name", yaml_path.parent.name),
                version=data.get("version", "1.0.0"),
                description=data.get("description", ""),
                author=data.get("author", "unknown"),
                tools=data.get("tools", []),
                dependencies=data.get("dependencies", []),
                config_schema=data.get("config_schema"),
            )
            
            return metadata
            
        except (yaml.YAMLError, IOError) as e:
            print(f"Failed to load skill from {yaml_path}: {e}")
            return None
    
    def get_skill_tools(self, skill_name: str) -> List[str]:
        """
        获取 skill 提供的工具列表
        
        Args:
            skill_name: Skill 名称
        
        Returns:
            工具名称列表
        """
        metadata = self.registry.get(skill_name)
        if metadata:
            return metadata.tools
        return []


# 默认加载器实例
_default_loader = None


def get_loader() -> SkillLoader:
    """获取默认加载器实例"""
    global _default_loader
    if _default_loader is None:
        _default_loader = SkillLoader()
    return _default_loader