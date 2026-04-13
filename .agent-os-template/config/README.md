# Agent OS 配置模板

此目录用于放置用户配置和 Skill 配置。

## 目录结构

```
config/
├── global.json              # 全局配置
└── {skill-name}.json        # 各 Skill 的配置
```

## global.json 格式

```json
{
  "global": {
    "debug": false,
    "log_level": "info",
    "auto_load_skills": true,
    "skill_priority": ["user", "plugin", "builtin"]
  },
  "skill_catalog": {
    "builtin_path": "src/skills",
    "user_path": "~/.agent-os/skills",
    "plugin_path": "~/.agent-os/plugins",
    "config_path": "~/.agent-os/config"
  }
}
```

## Skill 配置格式

每个 Skill 可以有独立的配置文件：

```json
{
  "enabled": true,
  "options": {
    "option1": "value1",
    "option2": 10
  }
}
```

## 配置文件优先级

1. `~/.agent-os/config/` （用户配置）
2. 内置默认配置

## Skill 配置示例

如果安装了 `hello-world` Skill，可以创建 `hello-world.json`：

```json
{
  "enabled": true,
  "options": {
    "default_name": "Agent"
  }
}
```