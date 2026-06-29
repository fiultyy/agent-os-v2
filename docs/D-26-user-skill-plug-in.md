# D-26: User Skill 插拔方案

> ⚠️ **历史文档**(写于当时, 记录当时的架构设计/实现计划)。当前系统现状以 `docs/mvp-iteration-roadmap.md` + `agentos-mvp-roadmap` memory 为准; 正文可能含已变更 / 已 defer / 已重构 的内容, 仅供历史参考。

> 状态: **设计完成** | 实现状态: 待启动
> 设计日期: 2026-04-14
> 责任人: 项目专家-00

---

## 1. 背景与目标

### 1.1 现状

Agent OS 已有一套基础 Skill 系统，位于 `services/orchestrator/src/skill_catalog/`：

| 模块 | 职责 | 现状 |
|------|------|------|
| `SkillRegistry` | Skill 元数据注册与查询 | 仅有 `skill.yaml` 解析，无 `SKILL.md` 支持 |
| `SkillLoader` | 文件系统扫描与加载 | 支持内置/用户/插件三层，但未与 L3 工具层打通 |
| `SkillConfig` | 用户配置管理（JSON） | 独立于 Skill 内容，路径散乱 |

现有 Skill 包的 `skill.yaml` 仅声明元信息（name/version/tools），**缺少 Skill 使用说明（Markdown）和 YAML frontmatter 标准化格式**，无法支持 Agent 动态解析和 `available_skills` 注入。

### 1.2 目标

| 目标 | 说明 |
|------|------|
| **SKILL.md 标准化** | 以 `SKILL.md` 为唯一入口，YAML frontmatter + Markdown 正文，支持 `/命令` 触发 |
| **优先级目录结构** | 用户级 > 项目级 > 内置级，高优先级同名 Skill 覆盖低优先级 |
| **与 L3 工具层集成** | SkillLoader 输出对接 ToolRegistry，Skill 按需转换为 L3 工具 |
| **Prompt 层集成** | `available_skills` 列表通过 SkillCatalog 动态注入 Base Prompt |
| **配置持久化** | Skill 配置（可见性、用户可调用性）与 Skill 内容分离存储 |

---

## 2. 目录结构

### 2.1 三层优先级

```
~/.agent-os/skills/                     ← 用户级（最高优先级）
    <skill-name>/
        SKILL.md
        scripts/          (可选)
        references/       (可选)

<project>/.agent-os/skills/             ← 项目级（中等优先级）
    <skill-name>/
        SKILL.md

<pkg>/skills/                           ← 内置级（最低优先级）
    <skill-name>/
        SKILL.md         (现有 skill.yaml 迁移目标)
        __init__.py
        *.py
```

### 2.2 优先级合并规则

```
同名 Skill 冲突时：
1. 用户级覆盖项目级和内置级
2. 项目级覆盖内置级
3. 不同名 Skill 全部保留
```

### 2.3 每个 Skill 的目录结构

```
<skill-name>/
├── SKILL.md          ← 唯一必需文件（YAML frontmatter + Markdown 使用说明）
├── scripts/          ← 可选：辅助脚本（shell/python/其他）
│   ├── setup.sh
│   └── helper.py
└── references/       ← 可选：参考文档（架构图、API 文档、示例）
    ├── api.md
    └── examples.md
```

### 2.4 安全约束（参考 OpenClaw）

| 约束 | 值 | 说明 |
|------|-----|------|
| 单个 SKILL.md 最大大小 | 64 KB | 防止大文件攻击 |
| 每个 Skill 目录最大大小 | 1 MB | 防止目录炸弹 |
| 允许的脚本扩展名 | `.sh`, `.py`, `.js` | 禁止其他可执行文件 |
| 路径遍历检查 | `..` 禁止 | 防止路径穿越 |
| 最大嵌套深度 | 3 层 | `skill/scripts/refs/` |

---

## 3. SKILL.md 格式设计

### 3.1 完整示例

```yaml
---
name: code
description: Code manipulation skill (read, write, search)
version: 1.0.0
author: builtin
requires:
  tools:
    - code_read
    - code_write
    - code_search
  env: []
  python_packages: []
exposure:
  visible: true
  user_invocable: true
  priority: 100
  tags:
    - coding
    - file-operations
config_schema:
  default_encoding:
    type: string
    default: utf-8
    description: 默认文件编码
  max_file_size_kb:
    type: number
    default: 1024
    description: 最大读取文件大小(KB)
---

# Code Skill

代码操作技能，提供文件读取、写入和搜索功能。

## 工具列表

| 工具名 | 描述 | 参数 |
|--------|------|------|
| `code_read` | 读取代码文件 | `path: str`, `start_line?: int`, `end_line?: int` |
| `code_write` | 写入代码文件 | `path: str`, `content: str`, `encoding?: str` |
| `code_search` | 搜索代码内容 | `pattern: str`, `path?: str`, `regex?: bool` |

## 使用示例

### 读取文件

\`\`\`python
code_read(path="/path/to/main.py")
\`\`\`

### 写入文件

\`\`\`python
code_write(path="/path/to/main.py", content="# new content")
\`\`\`

## 注意事项

- 文件路径支持 `~` 展开
- 默认编码为 UTF-8
- 单次读取最大 1MB
```

### 3.2 YAML Frontmatter 字段定义

| 字段 | 类型 | 必需 | 说明 |
|------|------|------|------|
| `name` | string | ✅ | Skill 名称，唯一标识 |
| `description` | string | ✅ | 一句话描述 |
| `version` | string (semver) | ✅ | 版本号，如 `1.0.0` |
| `author` | string | ✅ | 作者，`builtin` / `user` / `plugin:<name>` |
| `requires.tools` | list[string] | ❌ | 依赖的 L3 工具列表 |
| `requires.env` | list[string] | ❌ | 所需环境变量（如 `OPENAI_API_KEY`） |
| `requires.python_packages` | list[string] | ❌ | 所需 Python 包（如 `pandas`） |
| `exposure.visible` | boolean | ✅ | 是否出现在 `available_skills` 列表 |
| `exposure.user_invocable` | boolean | ✅ | 是否可通过 `/命令` 调用 |
| `exposure.priority` | integer | ❌ | 同名 Skill 优先级（默认 0，越高越优先） |
| `exposure.tags` | list[string] | ❌ | 分类标签 |
| `config_schema` | object | ❌ | 用户可配置项（结构同 JSON Schema） |

### 3.3 与现有 `skill.yaml` 的迁移

现有 `skill.yaml` 格式：

```yaml
# 旧格式 (skill.yaml)
name: code
version: 1.0.0
description: Code manipulation skill
author: builtin
skill_type: builtin
tools:
  - code_read
  - code_write
dependencies: []
```

迁移到 `SKILL.md` 后，`skill.yaml` 字段映射关系：

| skill.yaml | SKILL.md frontmatter | 备注 |
|------------|---------------------|------|
| `name` | `name` | 直接迁移 |
| `version` | `version` | 直接迁移 |
| `description` | `description` | 直接迁移 |
| `author` | `author` | 直接迁移 |
| `tools` | `requires.tools` | 重命名 |
| `dependencies` | `requires.python_packages` | 重命名 |
| `skill_type` | → 删除 | 不再需要，`author` 已隐含 |
| (新增) | `exposure` | 新增可见性控制 |

---

## 4. 核心模块设计

### 4.1 模块概览

```
┌──────────────────────────────────────────────────────────────┐
│                    SkillManager (Facade)                       │
│  ┌────────────┐  ┌────────────┐  ┌────────────┐            │
│  │SkillLoader │  │SkillCatalog│  │SkillConfig │            │
│  │(发现/加载)  │  │(注册表/查询)│  │(配置管理)   │            │
│  └────────────┘  └────────────┘  └────────────┘            │
│         ↑               ↑               ↑                    │
│         └───────────────┴───────────────┘                    │
│                         ↓                                      │
│               ┌──────────────────┐                           │
│               │  SkillExecutor   │                           │
│               │  (按需加载/执行)  │                           │
│               └──────────────────┘                           │
│                         ↓                                      │
│          ┌─────────────────────────────┐                    │
│          │    L3 Tool Layer             │                    │
│          │  ToolRegistry + ToolExecutor │                    │
│          └─────────────────────────────┘                    │
└──────────────────────────────────────────────────────────────┘
```

### 4.2 SkillEntry 数据类

```python
@dataclass
class SkillEntry:
    """已解析的 Skill 条目"""
    name: str
    version: str
    description: str
    author: str
    priority: int                          # 优先级（用于同名覆盖）
    skill_type: Literal["builtin", "user", "project", "plugin"]

    # 可见性
    visible: bool
    user_invocable: bool
    tags: list[str]

    # 内容
    path: Path                             # SKILL.md 所在目录
    requires: SkillRequires                # 依赖（tools/env/packages）
    config_schema: dict[str, Any] | None   # 配置 schema

    # 解析后的使用说明（从 Markdown 正文提取）
    usage: str                             # Markdown 正文

    # 原始 frontmatter（供高级用途）
    raw_frontmatter: dict[str, Any]
```

### 4.3 SkillLoader

**职责**：目录扫描 + 优先级合并

```python
class SkillLoader:
    """
    Skill 发现与加载器

    扫描三层目录，按优先级合并同名 Skill：
    1. ~/.agent-os/skills/          (user,  priority=200)
    2. <project>/.agent-os/skills/  (project, priority=100)
    3. <pkg>/skills/                 (builtin, priority=0)

    同名 Skill：高 priority 覆盖低 priority
    """

    # 搜索路径（含优先级）
    SEARCH_PATHS: list[tuple[Path, str, int]] = [
        (Path.home() / ".agent-os" / "skills", "user", 200),
        # project path 由调用方通过 load_project_skills() 注入
        # builtin path 由 load_builtin_skills() 注入
    ]

    # 内置 Skill 目录（优先级最低）
    BUILTIN_SKILLS_PATH = Path(__file__).parent.parent / "skills"

    def __init__(self, config: SkillConfig | None = None):
        self._config = config or get_config()
        self._cache: dict[str, SkillEntry] = {}

    def load_skills(
        self,
        project_path: Path | None = None,
        force_reload: bool = False,
    ) -> list[SkillEntry]:
        """
        加载所有可用 Skill（去重后按优先级保留最高者）

        Args:
            project_path: 可选的项目路径，用于加载项目级 Skill
            force_reload: 是否强制重新扫描（绕过缓存）

        Returns:
            所有可用 Skill 列表（已去重）
        """
        if force_reload:
            self._cache.clear()

        all_entries: dict[str, list[SkillEntry]] = defaultdict(list)

        # 1. 扫描内置 Skill
        for entry in self._scan_directory(self.BUILTIN_SKILLS_PATH, "builtin"):
            all_entries[entry.name].append(entry)

        # 2. 扫描项目级 Skill（如果提供了 project_path）
        if project_path:
            project_skills = project_path / ".agent-os" / "skills"
            if project_skills.exists():
                for entry in self._scan_directory(project_skills, "project"):
                    all_entries[entry.name].append(entry)

        # 3. 扫描用户级 Skill
        user_skills = Path.home() / ".agent-os" / "skills"
        if user_skills.exists():
            for entry in self._scan_directory(user_skills, "user"):
                all_entries[entry.name].append(entry)

        # 4. 按优先级合并（每个 name 只保留最高优先级）
        result = []
        for name, entries in all_entries.items():
            entries.sort(key=lambda e: e.priority, reverse=True)
            result.append(entries[0])

        return result

    def _scan_directory(self, root: Path, skill_type: str) -> list[SkillEntry]:
        """扫描单个目录，查找所有 SKILL.md"""
        entries = []
        if not root.exists():
            return entries

        for skill_dir in root.iterdir():
            if not skill_dir.is_dir():
                continue
            if skill_dir.name.startswith("."):
                continue
            if not self._is_valid_skill_dir(skill_dir):
                continue

            entry = self._parse_skill_dir(skill_dir, skill_type)
            if entry:
                entries.append(entry)

        return entries

    def _parse_skill_dir(self, skill_dir: Path, skill_type: str) -> SkillEntry | None:
        """解析单个 Skill 目录"""
        skill_md = skill_dir / "SKILL.md"

        # 如果没有 SKILL.md，检查是否能从 __init__.py 推断（旧格式兼容）
        if not skill_md.exists():
            return self._infer_from_init(skill_dir, skill_type)

        # 路径安全检查
        if not self._is_safe_path(skill_dir):
            logger.warning(f"Unsafe skill path rejected: {skill_dir}")
            return None

        return self._parse_skill_md(skill_md, skill_type)

    def _parse_skill_md(self, md_path: Path, skill_type: str) -> SkillEntry | None:
        """解析 SKILL.md 文件"""
        try:
            content = md_path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            return None

        # 提取 YAML frontmatter
        if content.startswith("---"):
            parts = content.split("---", 2)
            if len(parts) >= 3:
                frontmatter_raw = parts[1]
                markdown_body = parts[2].strip()
            else:
                frontmatter_raw = ""
                markdown_body = content
        else:
            frontmatter_raw = ""
            markdown_body = content

        # 解析 YAML frontmatter
        try:
            import yaml
            fm = yaml.safe_load(frontmatter_raw) or {}
        except yaml.YAMLError:
            fm = {}

        # 文件大小安全检查
        if len(content.encode("utf-8")) > 64 * 1024:
            logger.warning(f"SKILL.md too large (>64KB): {md_path}")
            return None

        # 提取 usage（Markdown 正文）
        usage = self._extract_usage(markdown_body)

        return SkillEntry(
            name=fm.get("name", md_path.parent.name),
            version=fm.get("version", "1.0.0"),
            description=fm.get("description", ""),
            author=fm.get("author", "builtin" if skill_type == "builtin" else "user"),
            priority=fm.get("exposure", {}).get("priority", 0),
            skill_type=skill_type,
            visible=fm.get("exposure", {}).get("visible", True),
            user_invocable=fm.get("exposure", {}).get("user_invocable", True),
            tags=fm.get("exposure", {}).get("tags", []),
            path=md_path.parent,
            requires=SkillRequires(
                tools=fm.get("requires", {}).get("tools", []),
                env=fm.get("requires", {}).get("env", []),
                python_packages=fm.get("requires", {}).get("python_packages", []),
            ),
            config_schema=fm.get("config_schema"),
            usage=usage,
            raw_frontmatter=fm,
        )

    def _extract_usage(self, markdown_body: str) -> str:
        """从 Markdown 正文中提取简洁使用说明（用于 Prompt 注入）"""
        # 取前 512 字符作为 usage 摘要
        lines = markdown_body.split("\n")
        summary_lines = []
        char_count = 0
        for line in lines:
            if line.startswith("#"):
                continue  # 跳过标题
            char_count += len(line)
            summary_lines.append(line)
            if char_count > 512:
                break
        return "\n".join(summary_lines).strip()

    def _is_safe_path(self, path: Path) -> bool:
        """路径安全检查（防止路径遍历）"""
        try:
            resolved = path.resolve()
            # 检查是否包含 .. 路径遍历
            if ".." in str(path):
                return False
            # 禁止绝对路径指向系统目录
            forbidden = ["/etc", "/sys", "/proc", "/dev"]
            for fb in forbidden:
                if str(resolved).startswith(fb):
                    return False
            return True
        except Exception:
            return False

    def _is_valid_skill_dir(self, d: Path) -> bool:
        """验证是否为有效 Skill 目录"""
        if not d.is_dir():
            return False
        # 必须有 SKILL.md 或 __init__.py（兼容旧格式）
        return (d / "SKILL.md").exists() or (d / "__init__.py").exists()

    def _infer_from_init(self, skill_dir: Path, skill_type: str) -> SkillEntry | None:
        """从旧格式（__init__.py + 无 SKILL.md）推断 SkillEntry（向后兼容）"""
        init_file = skill_dir / "__init__.py"
        if not init_file.exists():
            return None

        # 查找同名 skill.yaml
        yaml_file = skill_dir / "skill.yaml"
        if yaml_file.exists():
            # 使用旧解析器
            return self._parse_yaml_metadata(yaml_file, skill_type)

        # 无任何元数据，仅从目录名推断
        return SkillEntry(
            name=skill_dir.name,
            version="1.0.0",
            description=f"{skill_dir.name} skill",
            author="builtin" if skill_type == "builtin" else "user",
            priority=0,
            skill_type=skill_type,
            visible=True,
            user_invocable=True,
            tags=[],
            path=skill_dir,
            requires=SkillRequires(tools=[], env=[], python_packages=[]),
            config_schema=None,
            usage="",
            raw_frontmatter={},
        )
```

### 4.4 SkillCatalog

**职责**：Skill 注册表 + 查询接口（复用并扩展现有 `SkillRegistry`）

```python
class SkillCatalog:
    """
    Skill 目录服务

    提供 Skill 的注册、查询、列表功能。
    内部组合 ToolRegistry，自动将 Skill 中的 tools 注册到 L3 工具层。

    与现有 SkillRegistry 的区别：
    - 使用 SkillEntry 替代 SkillMetadata
    - 增加 L3 工具层自动注册
    - 增加 available_skills 视图（用于 Prompt 注入）
    """

    def __init__(
        self,
        loader: SkillLoader | None = None,
        tool_registry: ToolRegistry | None = None,
    ):
        self._loader = loader or get_loader()
        self._tool_registry = tool_registry or get_tool_registry()
        self._entries: dict[str, SkillEntry] = {}
        self._initialized: bool = False

    def initialize(self, project_path: Path | None = None) -> None:
        """初始化：加载所有 Skill 并注册到 ToolRegistry"""
        if self._initialized:
            return

        entries = self._loader.load_skills(project_path=project_path)
        for entry in entries:
            self._entries[entry.name] = entry
            # 自动将 Skill 的 tools 注册到 L3 ToolRegistry
            self._register_tools_to_l3(entry)

        self._initialized = True

    def _register_tools_to_l3(self, entry: SkillEntry) -> None:
        """将 Skill 内的工具注册到 L3 ToolRegistry"""
        for tool_name in entry.requires.tools:
            # 构造 L3 工具名：<skill_name>_<tool_name>
            l3_tool_name = f"{entry.name}_{tool_name}"
            self._tool_registry.register(
                name=l3_tool_name,
                handler=self._create_tool_handler(entry, tool_name),
                description=f"[{entry.name}] {tool_name}",
                parameters={},  # TODO: 从 tool 实现推断参数 schema
            )

    def _create_tool_handler(self, entry: SkillEntry, tool_name: str):
        """为 Skill 工具创建可调用句柄"""
        # 延迟导入：避免循环依赖
        from importlib import import_module

        def handler(**kwargs):
            # 从 Skill 目录动态导入工具模块
            tool_module = self._import_tool_module(entry, tool_name)
            if tool_module is None:
                raise RuntimeError(f"Tool {tool_name} not found in skill {entry.name}")
            tool_func = getattr(tool_module, tool_name, None)
            if not callable(tool_func):
                raise RuntimeError(f"{tool_name} is not callable in skill {entry.name}")
            return tool_func(**kwargs)

        return handler

    def _import_tool_module(self, entry: SkillEntry, tool_name: str):
        """动态导入 Skill 的工具模块"""
        try:
            spec = importlib.util.spec_from_file_location(
                f"{entry.name}.{tool_name}",
                entry.path / f"{tool_name}.py",
            )
            if spec and spec.loader:
                module = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(module)
                return module
        except Exception:
            pass
        return None

    def get(self, name: str) -> SkillEntry | None:
        """按名称获取 Skill"""
        return self._entries.get(name)

    def list_all(self) -> list[SkillEntry]:
        """列出所有已注册的 Skill"""
        return list(self._entries.values())

    def list_visible(self) -> list[SkillEntry]:
        """列出所有 visible=true 的 Skill（用于 Prompt 注入）"""
        return [e for e in self._entries.values() if e.visible]

    def list_user_invocable(self) -> list[SkillEntry]:
        """列出所有 user_invocable=true 的 Skill（用于 /命令 列表）"""
        return [e for e in self._entries.values() if e.user_invocable]

    def list_by_tag(self, tag: str) -> list[SkillEntry]:
        """按标签筛选 Skill"""
        return [e for e in self._entries.values() if tag in e.tags]

    def list_by_type(self, skill_type: str) -> list[SkillEntry]:
        """按类型列出 Skill（builtin/user/project/plugin）"""
        return [e for e in self._entries.values() if e.skill_type == skill_type]

    def get_available_skills_for_prompt(self) -> list[dict[str, str]]:
        """
        生成 Skill Index 列表（Stage 1 注入，用于注入 Base Prompt）

        注意：此方法仅返回 Skill 的轻量索引信息（name + description + location），
        不含完整 SKILL.md 内容。完整内容在 LLM 匹配到 Skill 后按需加载（Stage 2）。

        Returns:
            [{"name": "code", "description": "代码操作技能", "location": "~/.agent-os/skills/code/SKILL.md"}]
        """
        visible = self.list_visible()
        return [
            {
                "name": e.name,
                "description": e.description,
                "location": str(e.path / "SKILL.md"),
            }
            for e in visible
        ]

    def get_skill_content(self, name: str) -> str | None:
        """
        按需加载 Skill 完整内容（Stage 2：Skill Content）

        读取 SKILL.md 完整内容，用于 LLM 执行 Skill 逻辑。
        结果按 skill 粒度缓存，文件 mtime 变化时失效。

        Returns:
            SKILL.md 完整内容（Markdown），未找到返回 None
        """
        entry = self._entries.get(name)
        if not entry:
            return None
        skill_md_path = entry.path / "SKILL.md"
        if not skill_md_path.exists():
            return None
        try:
            return skill_md_path.read_text(encoding="utf-8")
        except Exception:
            return None

    def reload(self, skill_name: str, project_path: Path | None = None) -> bool:
        """重新加载单个 Skill"""
        if skill_name in self._entries:
            old_entry = self._entries[skill_name]
            # 取消注册旧工具
            for tool_name in old_entry.requires.tools:
                self._tool_registry.unregister(f"{old_entry.name}_{tool_name}")

        # 重新扫描
        entries = self._loader.load_skills(project_path=project_path, force_reload=True)
        new_entry = next((e for e in entries if e.name == skill_name), None)
        if new_entry:
            self._entries[skill_name] = new_entry
            self._register_tools_to_l3(new_entry)
            return True
        else:
            # Skill 已删除
            if skill_name in self._entries:
                del self._entries[skill_name]
            return False
```

### 4.5 SkillExecutor

**职责**：按需加载 SKILL.md + 执行

```python
class SkillExecutor:
    """
    Skill 执行器

    负责：
    1. 按需加载单个 Skill（延迟加载）
    2. 执行 Skill 内的工具
    3. 处理执行结果
    """

    def __init__(
        self,
        catalog: SkillCatalog | None = None,
        tool_executor: ToolExecutor | None = None,
    ):
        self._catalog = catalog
        self._tool_executor = tool_executor or get_tool_executor()
        self._loaded_handlers: dict[str, dict[str, Callable]] = {}
        # Stage 2 内容缓存（session 隔离，mtime 失效）
        self._loaded_contents: dict[str, dict[str, Any]] = {}

    async def execute(
        self,
        skill_name: str,
        tool_name: str,
        arguments: dict[str, Any],
        context: dict[str, Any] | None = None,
        timeout: float = 30.0,
    ) -> dict[str, Any]:
        """
        执行 Skill 内的工具（渐进式加载）

        Stage 1: SkillCatalog.get(name) → 获取 entry（仅索引，无完整内容）
        Stage 2: 按需 read SKILL.md → 验证 requires.tools / requires.env
        Stage 3: 调用 L3 ToolExecutor 执行（自动经过 Guardrail）
        Stage 4: 返回结果

        Args:
            skill_name: Skill 名称
            tool_name: 工具名称（不带前缀）
            arguments: 工具参数
            context: 可选执行上下文（用于 skill 间共享）
            timeout: 超时秒数

        Returns:
            ToolResult 格式的字典
        """
        catalog = self._catalog or get_catalog()
        entry = catalog.get(skill_name)
        if not entry:
            return {
                "tool": f"{skill_name}.{tool_name}",
                "status": "error",
                "output": None,
                "error": f"Skill {skill_name!r} not found",
            }

        if tool_name not in entry.requires.tools:
            return {
                "tool": f"{skill_name}.{tool_name}",
                "status": "error",
                "output": None,
                "error": f"Tool {tool_name!r} not found in skill {skill_name!r}",
            }

        # Stage 2: 按需加载 SKILL.md 内容（用于后续 skill 逻辑执行）
        # 缓存在 _loaded_contents 中，mtime 变化时自动失效
        skill_content = self._load_skill_content(skill_name, entry)

        # Stage 3: 调用 L3 ToolExecutor 执行（自动经过 Guardrail）
        l3_tool_name = f"{skill_name}_{tool_name}"
        return await self._tool_executor.execute(
            tool_name=l3_tool_name,
            arguments=arguments,
            context={**(context or {}), "skill_content": skill_content},
            timeout=timeout,
        )

    def _load_skill_content(self, skill_name: str, entry: SkillEntry) -> str | None:
        """
        按 skill 粒度加载 SKILL.md 内容（带 mtime 缓存）

        缓存失效条件：文件 mtime 变化
        session 隔离：每个 SkillExecutor 实例独立缓存
        """
        skill_md_path = entry.path / "SKILL.md"
        if not skill_md_path.exists():
            return None

        current_mtime = skill_md_path.stat().st_mtime
        cached = self._loaded_contents.get(skill_name)

        if cached and cached["mtime"] == current_mtime:
            return cached["content"]

        try:
            content = skill_md_path.read_text(encoding="utf-8")
            self._loaded_contents[skill_name] = {
                "content": content,
                "mtime": current_mtime,
            }
            return content
        except Exception:
            return None

    def get_skill_usage(self, skill_name: str) -> str:
        """获取 Skill 的使用说明（用于 help 命令）"""
        entry = (self._catalog or get_catalog()).get(skill_name)
        if not entry:
            return f"Skill {skill_name} not found"
        return entry.usage or entry.description
```

### 4.6 SkillConfig

**职责**：Skill 配置管理（扩展现有 `SkillConfig`）

```python
@dataclass
class SkillRequires:
    """Skill 依赖声明"""
    tools: list[str]
    env: list[str]
    python_packages: list[str]


class SkillConfig:
    """
    Skill 用户配置管理器（扩展版）

    职责：
    - 管理 Skill 的用户级配置（与 SKILL.md 内容分离）
    - 存储可见性、用户可调用性等运行时状态
    - 验证配置值是否符合 SKILL.md 中的 config_schema
    """

    CONFIG_DIR = Path.home() / ".agent-os" / "config"
    SKILL_CONFIG_DIR = Path.home() / ".agent-os" / "skill_configs"

    def __init__(self, config_dir: Path | None = None):
        self.config_dir = config_dir or self.SKILL_CONFIG_DIR
        self.config_dir.mkdir(parents=True, exist_ok=True)
        self._cache: dict[str, dict[str, Any]] = {}

    def get(self, skill_name: str, key: str | None = None, default: Any = None) -> Any:
        """获取 Skill 配置"""
        config = self._load_config(skill_name)
        if key is None:
            return config
        return config.get(key, default)

    def set(self, skill_name: str, key: str, value: Any, validate: bool = True) -> bool:
        """设置 Skill 配置（可选验证 schema）"""
        config = self._load_config(skill_name)

        # 如果 Skill 有 config_schema，进行验证
        if validate:
            catalog = get_catalog()
            entry = catalog.get(skill_name) if catalog else None
            if entry and entry.config_schema and key in entry.config_schema:
                valid, error = self._validate(value, entry.config_schema[key])
                if not valid:
                    logger.warning("Config validation failed: %s", error)
                    return False

        config[key] = value
        return self._save_config(skill_name, config)

    def set_visibility(self, skill_name: str, visible: bool) -> bool:
        """设置 Skill 的可见性"""
        return self.set(skill_name, "visible", visible, validate=False)

    def set_user_invocable(self, skill_name: str, invocable: bool) -> bool:
        """设置 Skill 的用户可调用性"""
        return self.set(skill_name, "user_invocable", invocable, validate=False)

    def delete(self, skill_name: str) -> bool:
        """删除 Skill 的全部配置"""
        config_file = self._get_config_file(skill_name)
        if config_file.exists():
            config_file.unlink()
        self._cache.pop(skill_name, None)
        return True

    def _load_config(self, skill_name: str) -> dict[str, Any]:
        """加载配置（带缓存）"""
        if skill_name in self._cache:
            return self._cache[skill_name]
        config_file = self._get_config_file(skill_name)
        if config_file.exists():
            try:
                config = json.loads(config_file.read_text())
            except json.JSONDecodeError:
                config = {}
        else:
            config = {}
        self._cache[skill_name] = config
        return config

    def _save_config(self, skill_name: str, config: dict[str, Any]) -> bool:
        """保存配置"""
        try:
            self._get_config_file(skill_name).write_text(
                json.dumps(config, indent=2, ensure_ascii=False)
            )
            self._cache[skill_name] = config
            return True
        except IOError:
            return False

    def _get_config_file(self, skill_name: str) -> Path:
        """获取配置文件路径"""
        return self.config_dir / f"{skill_name}.json"

    def _validate(self, value: Any, schema: dict[str, Any]) -> tuple[bool, str | None]:
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
        if "enum" in schema and value not in schema["enum"]:
            return False, f"Value must be one of {schema['enum']}"
        if expected_type == "number":
            if "min" in schema and value < schema["min"]:
                return False, f"Value must be >= {schema['min']}"
            if "max" in schema and value > schema["max"]:
                return False, f"Value must be <= {schema['max']}"
        return True, None
```



### 4.7 缓存与版本控制

渐进式注入依赖三层缓存机制，实现低延迟与高命中率：

```python
class SkillCacheManager:
    """
    Skill 缓存管理器（支持 mtime 失效 + version bump 失效）

    三层缓存：
    1. SkillIndex 缓存（内存）→ version bump 时全量失效
    2. SKILL.md 内容缓存 → 按 skill 粒度，文件 mtime 变化时失效
    3. session 隔离 → 每个 SkillExecutor 实例独立缓存空间
    """

    def __init__(self):
        self._index_cache: dict[str, Any] | None = None
        self._index_version: int = 0
        self._content_cache: dict[str, dict[str, Any]] = {}  # skill_name → {content, mtime}

    def get_skill_content(self, skill_name: str, path: Path) -> str | None:
        """
        按 skill 粒度获取 SKILL.md 内容（带 mtime 失效）
        """
        skill_md = path / "SKILL.md"
        if not skill_md.exists():
            return None

        current_mtime = skill_md.stat().st_mtime
        cached = self._content_cache.get(skill_name)

        if cached and cached["mtime"] == current_mtime:
            return cached["content"]

        try:
            content = skill_md.read_text(encoding="utf-8")
            self._content_cache[skill_name] = {"content": content, "mtime": current_mtime}
            return content
        except Exception:
            return None

    def invalidate_index(self) -> None:
        """version bump → SkillIndex 全量失效，下次调用重新扫描"""
        self._index_version += 1
        self._index_cache = None

    def invalidate_content(self, skill_name: str) -> None:
        """单 skill mtime 变化 → 该 skill 内容缓存失效"""
        self._content_cache.pop(skill_name, None)

    @property
    def session_id(self) -> str:
        """session 隔离标识（每个 SkillExecutor 实例唯一）"""
        return id(self)
```

| 缓存层 | 失效条件 | 作用范围 | 典型延迟 |
|--------|----------|----------|----------|
| SkillIndex（内存） | version bump / `force_reload` | 全量 skill index | <1ms |
| SKILL.md 内容（内存） | 文件 mtime 变化 | 单个 skill | <5ms（命中）/ 磁盘读取（未命中） |
| session 隔离 | SkillExecutor 实例销毁 | 单 session | — |

**mtime 缓存实现要点**：

```python
# SkillExecutor._load_skill_content() 中的缓存检查
skill_md_path = entry.path / "SKILL.md"
current_mtime = skill_md_path.stat().st_mtime  # 文件修改时间
cached = self._loaded_contents.get(skill_name)

if cached and cached["mtime"] == current_mtime:
    return cached["content"]   # 缓存命中

# 缓存未命中或 mtime 变化 → 重新读取
content = skill_md_path.read_text(encoding="utf-8")
self._loaded_contents[skill_name] = {"content": content, "mtime": current_mtime}
return content
```


---

## 5. API 设计

### 5.1 SkillLoader

```python
class SkillLoader:
    # 实例方法
    def load_skills(
        self,
        project_path: Path | None = None,
        force_reload: bool = False,
    ) -> list[SkillEntry]: ...

    def load_builtin_skills(self) -> list[SkillEntry]: ...
    def load_user_skills(self) -> list[SkillEntry]: ...
    def reload(self, skill_name: str) -> bool: ...

    # 扫描结果
    def scan_for_updates(self) -> list[str]:  # 返回有变化的 skill names
```

### 5.2 SkillCatalog

```python
class SkillCatalog:
    def initialize(self, project_path: Path | None = None) -> None: ...
    def get(self, name: str) -> SkillEntry | None: ...
    def list_all(self) -> list[SkillEntry]: ...
    def list_visible(self) -> list[SkillEntry]: ...
    def list_user_invocable(self) -> list[SkillEntry]: ...
    def list_by_tag(self, tag: str) -> list[SkillEntry]: ...
    def list_by_type(self, skill_type: str) -> list[SkillEntry]: ...
    def reload(self, skill_name: str, project_path: Path | None = None) -> bool: ...
    def get_available_skills_for_prompt(self) -> list[dict[str, str]]: ...
```

### 5.3 SkillExecutor

```python
class SkillExecutor:
    def __init__(
        self,
        catalog: SkillCatalog | None = None,
        tool_executor: ToolExecutor | None = None,
    ): ...

    async def execute(
        self,
        skill_name: str,
        tool_name: str,
        arguments: dict[str, Any],
        context: dict[str, Any] | None = None,
        timeout: float = 30.0,
    ) -> dict[str, Any]: ...  # 返回 ToolResult 格式（Stage 2 按需加载 SKILL.md）

    def get_skill_usage(self, skill_name: str) -> str: ...
    def get_skill_content(self, skill_name: str) -> str | None: ...  # Stage 2 内容读取
```

### 5.4 SkillConfig

```python
class SkillConfig:
    def get(self, skill_name: str, key: str | None = None, default: Any = None) -> Any: ...
    def set(self, skill_name: str, key: str, value: Any, validate: bool = True) -> bool: ...
    def set_visibility(self, skill_name: str, visible: bool) -> bool: ...
    def set_user_invocable(self, skill_name: str, invocable: bool) -> bool: ...
    def delete(self, skill_name: str) -> bool: ...
    def list_skills(self) -> list[str]: ...
```

---

## 6. 集成方案

### 6.1 与 L3 工具层集成

```
SkillCatalog._register_tools_to_l3()
        ↓
ToolRegistry.register(name=<skill>_<tool>, handler, description)
        ↓
ToolExecutor.execute() → Guardrail.check() → handler()
        ↓
SkillExecutor.execute() ← ToolResult 返回
```

**集成点说明**：

| 集成点 | 说明 |
|--------|------|
| `SkillCatalog._register_tools_to_l3()` | Skill 初始化时，将 skill 内的 tools 注册到 `ToolRegistry` |
| `ToolExecutor` 复用 | 已有 `ToolExecutor.execute()` 的 Guardrail + 超时机制无需改动 |
| 工具命名 | `<skill_name>_<tool_name>`，如 `code_read` → `code_code_read`（避免与内置工具冲突） |
| ToolCatalog | `SkillCatalog.get_available_skills_for_prompt()` 可直接作为 ToolCatalog 的数据源 |

**后向兼容**：

现有 `skill_catalog/registry.py` 的 `SkillMetadata` 仍然保留，担任"过渡层"：
`SKILL.md` → `SkillEntry` → `SkillMetadata`（兼容旧代码）。

### 6.2 与 Memory System 集成

```
SkillConfig.set_visibility(skill_name, visible)
        ↓
Skill 配置持久化到 ~/.agent-os/skill_configs/<skill_name>.json
        ↓
SkillCatalog.list_visible() 读取配置（可见性=frontmatter && config）
        ↓
get_available_skills_for_prompt() 反映最新可见性
```

| 存储位置 | 内容 | 说明 |
|----------|------|------|
| `~/.agent-os/skill_configs/<name>.json` | 运行时配置（可见性、用户调用性） | 用户修改后自动持久化 |
| `SKILL.md` frontmatter | 声明式配置（默认可见性、schema） | Skill 作者声明 |

**配置优先级**：用户配置 > Skill 声明默认值
（即 `SkillConfig.get()` 覆盖 `SkillEntry` 中的 `visible`/`user_invocable`）

### 6.3 与 Prompt 管理集成（渐进式两阶段注入）

```
┌──────────────────────────────────────────────────────────────┐
│                      Prompt Engine                            │
│                                                               │
│  Stage 1: SkillLoader.scan() → SkillCatalog.index            │
│    → 生成 <available_skills> XML（name + description + location）│
│    → 注入 system prompt                                       │
│    ↓                                                          │
│  Stage 2: LLM 匹配 skill → read SKILL.md → SkillExecutor    │
└──────────────────────────────────────────────────────────────┘
```

**Stage 1: Skill Index（始终注入，低成本 ~100-200 bytes/skill）**

```python
# Prompt 注入格式
AVAILABLE_SKILLS_TEMPLATE = """<available_skills>
{skills_entries}
</available_skills>

## Skills (mandatory)
Before replying: scan <available_skills> <description> entries.
- If exactly one skill clearly applies: read its SKILL.md at <location> with `read`, then follow it.
- If multiple could apply: choose the most specific one, then read/follow it.
- If none clearly apply: do not read any SKILL.md.
Constraints: never read more than one skill up front; only read after selecting."""

# 每个 entry 格式（轻量索引）
SKILL_ENTRY_TEMPLATE = """  <skill>
    <name>{name}</name>
    <description>{description}</description>
    <location>{base_dir}/{skill_name}/SKILL.md</location>
  </skill>"""
```

**Stage 2: Skill Content（按需加载，仅当 LLM 匹配到 Skill 时）**

```python
# LLM matched skill → read SKILL.md → SkillExecutor.execute()
SkillExecutor.execute(skill_name, tool_name, arguments):
  1. SkillCatalog.get(name) → 获取 entry（仅 index）
  2. if not cached content → read SKILL.md → 解析完整内容
  3. 验证 requires.tools / requires.env
  4. 执行 skill 逻辑
  5. 返回结果
```

**注入格式（Stage 1，注入 System Prompt）**：

```
<available_skills>
  <skill>
    <name>code</name>
    <description>代码操作技能</description>
    <location>~/.agent-os/skills/code/SKILL.md</location>
  </skill>
  <skill>
    <name>browser</name>
    <description>浏览器自动化</description>
    <location>~/.agent-os/skills/browser/SKILL.md</location>
  </skill>
</available_skills>

## Skills (mandatory)
Before replying: scan <available_skills> <description> entries.
- If exactly one skill clearly applies: read its SKILL.md at <location> with `read`, then follow it.
- If multiple could apply: choose the most specific one, then read/follow it.
- If none clearly apply: do not read any SKILL.md.
Constraints: never read more than one skill up front; only read after selecting.
```

**与原全量注入的区别**：

| 维度 | 原全量注入 | 渐进式注入 |
|------|-----------|-----------|
| 注入内容 | 完整 SKILL.md + tools list | 仅 name + description + location |
| token 消耗 | O(n × SKILL.md 大小) | O(n × 100-200 bytes) |
| 完整内容 | 全部预加载 | LLM 匹配后按需加载 |
| 适用场景 | Skill 数量少（<10） | Skill 数量多（可扩展至百级）|

### 6.4 与现有 skill_catalog/ 模块的关系

| 现有模块 | 新模块 | 关系 |
|----------|--------|------|
| `skill_catalog/registry.SkillMetadata` | `skill_catalog/loader.SkillEntry` | SkillEntry 替代 SkillMetadata（扩展字段） |
| `skill_catalog/loader.SkillLoader` | `skill_catalog/loader.SkillLoader` | 重写（支持 SKILL.md + 优先级合并） |
| `skill_catalog/config.SkillConfig` | `skill_catalog/loader.SkillConfig` | 扩展（增加 set_visibility/schema 验证） |
| `skill_catalog/registry.SkillRegistry` | `skill_catalog/loader.SkillCatalog` | SkillCatalog 替代 SkillRegistry（组合 ToolRegistry） |

**迁移策略**：保留旧类作为别名，向后兼容：

```python
# 向后兼容别名
from src.skill_catalog.loader import SkillEntry, SkillLoader, SkillCatalog, SkillConfig

# 旧路径仍然可用（deprecated）
from src.skill_catalog.registry import SkillMetadata  # alias to SkillEntry
```

---

## 7. 架构图

```
┌──────────────────────────────────────────────────────────────────────┐
│                         Agent OS Runtime                              │
├──────────────────────────────────────────────────────────────────────┤
│                                                                       │
│  ┌─────────────┐    ┌──────────────────┐    ┌──────────────────┐   │
│  │  SKILL.md   │    │   SkillLoader    │    │   SkillCatalog   │   │
│  │  (文件)      │───▶│  _scan_directory │───▶│  register tools  │   │
│  │             │    │  _parse_skill_md │    │  to L3 Registry  │   │
│  └─────────────┘    └──────────────────┘    └────────┬─────────┘   │
│                                                      │               │
│  ┌──────────────────────────────────────────────────┴─────────────┐  │
│  │                    SkillConfig (持久化)                        │  │
│  │  ~/.agent-os/skill_configs/<name>.json                       │  │
│  └──────────────────────────────────────────────────────────────┘  │
│                                                                      │
│  ┌──────────────────────────────────────────────────────────────┐  │
│  │               L3 Tool Layer (ToolRegistry + ToolExecutor)      │  │
│  │  ┌──────────────┐    ┌──────────────┐    ┌──────────────┐      │  │
│  │  │ ToolRegistry │◀───│SkillCatalog │    │   Guardrail  │      │  │
│  │  │ name→handler │    │ (register)  │    │  (check)     │      │  │
│  │  └──────────────┘    └──────────────┘    └──────────────┘      │  │
│  │         │                                              ▲        │  │
│  │         ▼                                              │        │  │
│  │  ┌──────────────┐                             ┌──────────────┐ │  │
│  │  │ ToolExecutor │────────────────────────────▶│   Handler    │ │  │
│  │  │  (execute)   │                             │ (tool impl)  │ │  │
│  │  └──────────────┘                             └──────────────┘ │  │
│  └──────────────────────────────────────────────────────────────┘  │
│                                                                      │
│  ┌──────────────────────────────────────────────────────────────┐  │
│  │              Prompt Layer — 渐进式两阶段注入                    │  │
│  │                                                               │  │
│  │  Stage 1: SkillCatalog.get_available_skills_for_prompt()      │  │
│  │    → <available_skills> XML（name + desc + location）          │  │
│  │    → 始终注入 system prompt（低成本 ~100-200 bytes/skill）     │  │
│  │                                                               │  │
│  │  Stage 2: LLM 匹配 skill 后                                   │  │
│  │    → read SKILL.md → SkillExecutor.execute()                  │  │
│  │    → 按需加载完整内容（mtime 缓存失效）                          │  │
│  └──────────────────────────────────────────────────────────────┘  │
│                                                                      │
│  目录优先级：                                                         │
│  ~/.agent-os/skills/         (user,   priority=200) 最高            │
│  <project>/.agent-os/skills/ (project, priority=100)               │
│  <pkg>/skills/               (builtin, priority=0)   最低           │
└──────────────────────────────────────────────────────────────────────┘
```

---

## 8. 实现计划

### 8.1 阶段划分

| 阶段 | 内容 | 优先级 |
|------|------|--------|
| **Phase A** | `SKILL.md` 格式定义 + 解析器（`SkillLoader._parse_skill_md`） | P0 |
| **Phase B** | 优先级目录扫描 + 合并逻辑（`SkillLoader.load_skills`） | P0 |
| **Phase C** | `SkillCatalog` 与 `ToolRegistry` 自动注册集成 | P0 |
| **Phase D** | `available_skills` Prompt 注入（`get_available_skills_for_prompt`） | P1 |
| **Phase E** | `SkillConfig` 可见性持久化 + schema 验证 | P1 |
| **Phase F** | 内置 Skill 迁移（`primitive/browser/code/memory` → `SKILL.md`） | P2 |
| **Phase G** | 用户模板更新（`.agent-os-template/skills/` → `SKILL.md`） | P2 |
| **Phase H** | `/命令` 用户调用通道（`SkillExecutor.execute`） | P2 |

### 8.2 迁移旧格式

现有 `skill.yaml` 通过 `_infer_from_init()` 自动兼容，无需强制迁移。
建议 Phase F 中批量生成各内置 Skill 的 `SKILL.md`：

```bash
# 迁移脚本伪代码
for skill_dir in services/orchestrator/src/skills/*/:
    if not skill_dir / "SKILL.md":
        generate SKILL.md from skill_dir / "skill.yaml"
```

---

## 9. 设计决策

| 编号 | 决策 | 依据 |
|------|------|------|
| **D-26-1** | `SKILL.md` 为唯一入口，`skill.yaml` 仅作向后兼容 | OpenClaw 验证：`available_skills` 需要 Markdown 正文供 Agent 解析 |
| **D-26-2** | 目录优先级：user > project > builtin | OpenClaw 三层路径模式：用户自定义优先于项目，项目优先于内置 |
| **D-26-3** | 工具命名 `<skill>_<tool>` 注册到 L3 | 避免与内置工具名冲突，与 ToolRegistry 命名一致 |
| **D-26-4** | 可见性配置：frontmatter 默认值 + `SkillConfig` 用户覆盖 | 用户有最终决定权，但 Skill 作者提供合理默认值 |
| **D-26-5** | 安全约束：64KB 文件大小、路径遍历检查、禁止扩展名白名单 | 复用 OpenClaw 验证过的安全模型 |
| **D-26-6** | `SkillCatalog` 组合 `ToolRegistry`，不继承 | 关注点分离：Skill 管理 vs 工具执行 |

---

## 10. 参考

- OpenClaw skill 管理机制（`available_skills` 注入、SKILL.md YAML frontmatter）
- Agent OS 现有 `skill_catalog/` 模块（`loader.py`, `registry.py`, `config.py`）
- Agent OS `architecture.md` — L3 Tool Layer 设计（D-09 工具安全期）
- Agent OS L3 工具层（`services/orchestrator/src/tools/`）

---

_文档版本: 1.0.0 | 更新: 2026-04-14_
