# Agent OS 用户扩展 Skill 模板

此目录用于放置用户自定义的 Skill 包。

## 目录结构

```
skills/
├── my-custom-skill/          # 你的 Skill 包
│   ├── skill.yaml           # 必需：Skill 元数据
│   ├── __init__.py          # 必需：包入口
│   ├── tool1.py             # 工具实现
│   └── tool2.py
└── another-skill/
    ├── skill.yaml
    └── ...
```

## skill.yaml 格式

```yaml
name: my-custom-skill
version: 1.0.0
description: 我的自定义 Skill
author: your-name
skill_type: user
tools:
  - my_tool1
  - my_tool2
dependencies: []
config_schema:
  option1:
    type: string
    default: "value"
  option2:
    type: number
    default: 10
```

## 工具函数签名

```python
def my_tool1(param1: str, param2: int = 10) -> dict:
    """
    工具描述
    
    Args:
        param1: 参数1描述
        param2: 参数2描述
    
    Returns:
        {"success": bool, "result": any, "error": str}
    """
    return {"success": True, "result": None, "error": None}
```

## 注册到 Agent OS

将 Skill 包放入此目录后，Agent OS 会自动：
1. 发现并加载 skill.yaml 元数据
2. 注册到 SkillRegistry
3. 可通过 SkillLoader 查询和使用

## 示例：创建简单的用户 Skill

```bash
mkdir -p skills/hello-world
cd skills/hello-world
```

创建 `skill.yaml`:
```yaml
name: hello-world
version: 1.0.0
description: Hello World 示例 Skill
author: user
tools:
  - greet
  - farewell
```

创建 `__init__.py`:
```python
"""Hello World Skill"""
from .greet import greet
from .farewell import farewell

__all__ = ["greet", "farewell"]
```

创建 `greet.py`:
```python
def greet(name: str = "World") -> dict:
    """打招呼"""
    return {"success": True, "message": f"Hello, {name}!"}
```