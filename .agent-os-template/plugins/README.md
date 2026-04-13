# Agent OS 插件模板

此目录用于放置第三方或自定义插件 Skill 包。

## 与用户 Skill 的区别

| 维度 | 用户 Skill | 插件 Skill |
|------|-----------|-----------|
| 位置 | `~/.agent-os/skills/` | `~/.agent-os/plugins/` |
| 用途 | 个人使用 | 分享/分发 |
| 加载时机 | 启动时 | 按需加载 |

## 目录结构

```
plugins/
├── my-plugin/               # 插件包
│   ├── skill.yaml          # 必需：插件元数据
│   ├── __init__.py
│   ├── tools/
│   │   ├── tool1.py
│   │   └── tool2.py
│   └── assets/             # 插件资源（可选）
│       └── icon.png
└── another-plugin/
    └── ...
```

## skill.yaml 格式

```yaml
name: my-plugin
version: 1.0.0
description: 我的插件描述
author: plugin-author
skill_type: plugin
tools:
  - plugin_tool1
  - plugin_tool2
dependencies:
  - other-skill
config_schema:
  api_key:
    type: string
    description: API Key for external service
    required: true
```

## 插件发布流程

1. 在 `plugins/` 下创建插件目录
2. 编写 skill.yaml 和工具代码
3. 测试验证
4. 打包分发（可选）

## 插件加载机制

```python
# 插件发现
loader = SkillLoader()
loader.load_plugin_skills()  # 扫描 ~/.agent-os/plugins/

# 获取插件
plugin = registry.get("my-plugin")

# 调用工具
from src.skills.my_plugin.tools import plugin_tool1
result = plugin_tool1(param="value")
```