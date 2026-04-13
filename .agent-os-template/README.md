# Agent OS 用户扩展目录模板

这是 Agent OS 的 `.agent-os` 用户扩展目录模板。

## 目录结构

```
.agent-os-template/
├── skills/                   # 用户自定义 Skill 包
│   ├── README.md            # 使用说明
│   └── hello-world/         # 示例 Skill
│       ├── skill.yaml
│       ├── __init__.py
│       ├── greet.py
│       └── farewell.py
│
├── plugins/                  # 第三方插件 Skill
│   └── README.md            # 使用说明
│
└── config/                   # 用户配置
    ├── README.md            # 配置说明
    └── global.json          # 全局配置模板
```

## 快速开始

### 1. 初始化用户目录

```bash
# 复制模板到用户目录
cp -r .agent-os-template ~/.agent-os

# 或者创建空目录
mkdir -p ~/.agent-os/{skills,plugins,config}
```

### 2. 使用内置 Skill

内置 Skill 在 `src/skills/` 目录下，由系统管理。
用户扩展 Skill 放在 `~/.agent-os/skills/` 下。

### 3. 创建自定义 Skill

```bash
cd ~/.agent-os/skills
mkdir my-skill
cd my-skill

# 创建 skill.yaml
cat > skill.yaml << 'EOF'
name: my-skill
version: 1.0.0
description: 我的自定义 Skill
author: my-name
tools:
  - my_tool
EOF

# 创建 __init__.py
cat > __init__.py << 'EOF'
from .my_tool import my_tool
__all__ = ["my_tool"]
EOF

# 创建工具实现
cat > my_tool.py << 'EOF'
def my_tool(param: str) -> dict:
    return {"success": True, "result": param}
EOF
```

### 4. 加载 Skill

启动 Agent OS 时，SkillLoader 会自动扫描并加载：

- 内置 Skill: `src/skills/`
- 用户 Skill: `~/.agent-os/skills/`
- 插件 Skill: `~/.agent-os/plugins/`

## Skill 加载优先级

```
用户 Skill > 插件 Skill > 内置 Skill
```

相同名称的 Skill，优先级高的会覆盖优先级低的。

## 目录说明

| 目录 | 用途 | 初始化方式 |
|------|------|-----------|
| `skills/` | 用户自定义 Skill | 复制模板或手动创建 |
| `plugins/` | 第三方/分享 Skill | 手动安装 |
| `config/` | 用户配置和 Skill 配置 | 复制模板 |

## 配置管理

### 全局配置

编辑 `config/global.json`：

```json
{
  "global": {
    "debug": true,
    "log_level": "debug"
  }
}
```

### Skill 配置

为特定 Skill 创建配置文件（如 `skills/hello-world.json`）。

## 了解更多

- 内置 Skill 源码: `services/orchestrator/src/skills/`
- Skill 管理器: `services/orchestrator/src/skill_catalog/`
- 架构文档: `docs/architecture.md`