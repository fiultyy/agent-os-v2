"""
SkillConfig - Skill 配置管理

管理用户对 Skill 的自定义配置：
- 用户配置存储在 ~/.agent-os/config/
- 每个 Skill 有独立的 JSON 配置文件
- 与 Skill 内容分离（内容在 skills/，配置在 config/）
"""

import json
from pathlib import Path
from typing import Any, Dict, List, Optional


class SkillConfig:
    """
    Skill 配置管理器
    
    职责：
    - 读取用户 Skill 配置
    - 写入配置变更
    - 配置验证
    """
    
    DEFAULT_CONFIG_DIR = Path.home() / ".agent-os" / "config"
    
    def __init__(self, config_dir: Optional[Path] = None):
        self.config_dir = config_dir or self.DEFAULT_CONFIG_DIR
        self.config_dir.mkdir(parents=True, exist_ok=True)
        self._cache: Dict[str, Dict[str, Any]] = {}
    
    def get(self, skill_name: str, key: Optional[str] = None, default: Any = None) -> Any:
        """
        获取 Skill 配置
        
        Args:
            skill_name: Skill 名称
            key: 配置键，None 返回全部
            default: 默认值
        
        Returns:
            配置值
        """
        config = self._load_config(skill_name)
        if key is None:
            return config
        return config.get(key, default)
    
    def set(self, skill_name: str, key: str, value: Any) -> bool:
        """设置 Skill 配置"""
        config = self._load_config(skill_name)
        config[key] = value
        return self._save_config(skill_name, config)
    
    def update(self, skill_name: str, updates: Dict[str, Any]) -> bool:
        """批量更新配置"""
        config = self._load_config(skill_name)
        config.update(updates)
        return self._save_config(skill_name, config)
    
    def delete(self, skill_name: str, key: Optional[str] = None) -> bool:
        """删除配置"""
        if key is None:
            # 删除全部
            config_file = self._get_config_file(skill_name)
            if config_file.exists():
                config_file.unlink()
            if skill_name in self._cache:
                del self._cache[skill_name]
            return True
        
        config = self._load_config(skill_name)
        if key in config:
            del config[key]
            return self._save_config(skill_name, config)
        return True
    
    def list_skills(self) -> List[str]:
        """列出已配置的 Skill"""
        return [f.stem for f in self.config_dir.glob("*.json") if f.stem != "global"]
    
    def validate_schema(self, schema: Dict[str, Any], value: Any) -> tuple[bool, Optional[str]]:
        """验证配置值是否符合 schema"""
        expected_type = schema.get("type")
        
        type_map = {
            "string": str,
            "number": (int, float),
            "boolean": bool,
            "array": list,
            "object": dict,
        }
        
        if expected_type:
            expected = type_map.get(expected_type)
            if expected and not isinstance(value, expected):
                return False, f"Expected {expected_type}, got {type(value).__name__}"
        
        # 枚举检查
        if "enum" in schema and value not in schema["enum"]:
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