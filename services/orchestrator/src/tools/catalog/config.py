"""
SkillConfig - Skill 配置管理

管理 skill 的用户配置：
- 读取/写入用户配置
- 配置验证
- 默认值管理
"""

import json
from pathlib import Path
from typing import Dict, Any, List, Optional
from datetime import datetime


class SkillConfig:
    """
    Skill 配置管理器
    
    负责：
    - 读取 skill 用户配置
    - 写入配置变更
    - 配置验证
    - 默认值管理
    """
    
    DEFAULT_CONFIG_DIR = Path.home() / ".agent-os" / "config"
    
    def __init__(self, config_dir: Optional[Path] = None):
        self.config_dir = config_dir or self.DEFAULT_CONFIG_DIR
        self.config_dir.mkdir(parents=True, exist_ok=True)
        self._cache: Dict[str, Dict[str, Any]] = {}
    
    def get(self, skill_name: str, key: Optional[str] = None, default: Any = None) -> Any:
        """
        获取 skill 配置
        
        Args:
            skill_name: Skill 名称
            key: 配置键，默认返回全部配置
            default: 默认值
        
        Returns:
            配置值或默认
        """
        config = self._load_config(skill_name)
        
        if key is None:
            return config
        
        return config.get(key, default)
    
    def set(self, skill_name: str, key: str, value: Any) -> bool:
        """
        设置 skill 配置
        
        Args:
            skill_name: Skill 名称
            key: 配置键
            value: 配置值
        
        Returns:
            是否成功
        """
        config = self._load_config(skill_name)
        config[key] = value
        return self._save_config(skill_name, config)
    
    def update(self, skill_name: str, updates: Dict[str, Any]) -> bool:
        """
        批量更新 skill 配置
        
        Args:
            skill_name: Skill 名称
            updates: 配置更新
        
        Returns:
            是否成功
        """
        config = self._load_config(skill_name)
        config.update(updates)
        return self._save_config(skill_name, config)
    
    def delete(self, skill_name: str, key: Optional[str] = None) -> bool:
        """
        删除 skill 配置
        
        Args:
            skill_name: Skill 名称
            key: 配置键，默认删除全部
        
        Returns:
            是否成功
        """
        if key is None:
            # 删除全部配置
            config_file = self._get_config_file(skill_name)
            if config_file.exists():
                config_file.unlink()
                if skill_name in self._cache:
                    del self._cache[skill_name]
                return True
            return False
        else:
            config = self._load_config(skill_name)
            if key in config:
                del config[key]
                return self._save_config(skill_name, config)
            return True
    
    def list_skills(self) -> List[str]:
        """
        列出已配置过的 skill
        
        Returns:
            Skill 名称列表
        """
        skill_configs = []
        for f in self.config_dir.glob("*.json"):
            if f.stem != "global":  # 排除全局配置
                skill_configs.append(f.stem)
        return sorted(skill_configs)
    
    def validate(self, skill_name: str, schema: Dict[str, Any], value: Any) -> tuple[bool, Optional[str]]:
        """
        验证配置值
        
        Args:
            skill_name: Skill 名称
            schema: 配置 schema
            value: 配置值
        
        Returns:
            (是否有效, 错误信息)
        """
        # 简化验证：支持 type 检查
        expected_type = schema.get("type")
        
        if expected_type == "string" and not isinstance(value, str):
            return False, f"Expected string, got {type(value).__name__}"
        elif expected_type == "number" and not isinstance(value, (int, float)):
            return False, f"Expected number, got {type(value).__name__}"
        elif expected_type == "boolean" and not isinstance(value, bool):
            return False, f"Expected boolean, got {type(value).__name__}"
        elif expected_type == "array" and not isinstance(value, list):
            return False, f"Expected array, got {type(value).__name__}"
        elif expected_type == "object" and not isinstance(value, dict):
            return False, f"Expected object, got {type(value).__name__}"
        
        # 枚举检查
        if "enum" in schema:
            if value not in schema["enum"]:
                return False, f"Value must be one of {schema['enum']}"
        
        # 范围检查
        if expected_type == "number":
            if "min" in schema and value < schema["min"]:
                return False, f"Value must be >= {schema['min']}"
            if "max" in schema and value > schema["max"]:
                return False, f"Value must be <= {schema['max']}"
        
        return True, None
    
    def _load_config(self, skill_name: str) -> Dict[str, Any]:
        """加载配置到缓存"""
        if skill_name in self._cache:
            return self._cache[skill_name]
        
        config_file = self._get_config_file(skill_name)
        if config_file.exists():
            try:
                with open(config_file, "r", encoding="utf-8") as f:
                    config = json.load(f)
                self._cache[skill_name] = config
                return config
            except (json.JSONDecodeError, IOError):
                pass
        
        return {}
    
    def _save_config(self, skill_name: str, config: Dict[str, Any]) -> bool:
        """保存配置"""
        config_file = self._get_config_file(skill_name)
        try:
            with open(config_file, "w", encoding="utf-8") as f:
                json.dump(config, f, indent=2, ensure_ascii=False)
            self._cache[skill_name] = config
            return True
        except IOError:
            return False
    
    def _get_config_file(self, skill_name: str) -> Path:
        """获取配置文件路径"""
        return self.config_dir / f"{skill_name}.json"


# 全局配置实例
_global_config: Optional[SkillConfig] = None


def get_config() -> SkillConfig:
    """获取全局配置实例"""
    global _global_config
    if _global_config is None:
        _global_config = SkillConfig()
    return _global_config