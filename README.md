# Run Agent

Run Agent 是一个用于学习 AI Agent 基本工作方式的轻量项目。程序把 Anthropic 模型与一组本地工具连接起来：模型可以读取、编辑和管理工作区文件，也可以执行 Shell 命令、维护 TODO 任务清单、按需加载工作区中的 skills，并跨轮次复用持久记忆；程序会控制超长工具输出和消息历史规模，把每次工具执行结果返回给模型，直到模型不再请求工具。

当前系统提示词将它定位为文件管理学习助手，适合列出目录、查找文件、查看文件信息，以及读取、解释和管理工作区中的内容。

> [!WARNING]
> 本项目会执行模型生成的工具调用和 Shell 命令。专用文件工具会校验路径必须位于工作区内，权限 hook 也会禁止或要求确认部分危险命令，但这些检查基于简单的字符串匹配，不是真正的安全沙箱，也不能保证 Shell 命令不会访问工作区之外。请勿在含有重要文件或敏感数据的目录中直接运行；仅需查看文件时，建议通过 Docker 只读挂载工作区。

## 功能

- 通过 Anthropic Messages API 与模型交互
- 支持 Bash、读取、编辑、写入、删除、复制、移动、TODO 管理和技能加载共 9 个工具
- 通过工具处理器映射统一分发模型的工具调用
- 支持用 `todo_write` 整体更新内存任务清单，并在连续三个工具调用轮次未更新时提醒模型维护 TODO
- 将工具 Schema/处理器映射和 hooks 事件注册表分别放在 `tools_config.py`、`hooks_config.py` 中，避免通用配置模块承担运行时注册职责
- 每轮用户对话扫描 `resources/skills/*/SKILL.md`，把技能名称和简介加入系统提示词，并通过 `load_skill` 按需加载完整说明
- 在 `resources/memory` 中持久化用户偏好、反馈、项目事实和外部引用，只向主请求注入当前对话相关的记忆
- 每轮 CLI 对话结束后自动提取新记忆，达到 10 条时去重合并；模型不可用或输出无效时保留已有文件
- 支持 `UserPromptSubmit`、`PreToolUse`、`PostToolUse` 和 `Stop` 生命周期 hooks
- 禁止执行 `DENY_LIST` 中的 Shell 命令，并在执行 `DESTRUCTIVE` 命令前请求用户确认
- 在终端显示工具调用预览、大输出告警、当前工作目录和会话工具调用统计
- 专用文件工具会拒绝访问工作区之外的路径
- 复制和移动不会覆盖已经存在的目标文件
- Shell 命令最长执行 120 秒
- Shell 命令单次最多返回 50,000 个字符
- 自动裁剪过长消息列表、压缩较早的工具结果，并保护相邻的工具调用与工具结果消息不被拆开
- 单条工具输出超过 30,000 个字符时保存到 `.task_outputs/tool-results`，消息历史只保留安全文件名、路径和预览
- 消息历史超过估算阈值时保存 JSONL 转录并生成摘要；接口报告上下文溢出时会响应式压缩并重试一次
- 支持 `.env` 环境变量和自定义 Anthropic API 地址
- 提供非 root 用户运行的 Docker 环境
- 使用 pytest 提供相互隔离的单元测试和 Agent 工具流集成测试

## 工具说明

| 工具 | 说明 |
| --- | --- |
| `bash` | 在工作区中执行 Shell 命令，合并返回标准输出和标准错误；最长运行 120 秒，最多返回 50,000 个字符 |
| `read_file` | 读取 UTF-8 文本文件，可通过 `limit` 限制返回的行数 |
| `edit_file` | 将文件中第一次出现的指定文本精确替换为新文本 |
| `write_file` | 创建或覆盖 UTF-8 文本文件，并按需创建父目录 |
| `delete_file` | 删除单个文件，不会删除目录 |
| `copy_file` | 复制文件并按需创建目标父目录；目标已经存在时拒绝执行 |
| `move_file` | 移动或重命名文件并按需创建目标父目录；目标已经存在时拒绝执行 |
| `todo_write` | 创建或整体替换当前 TODO 清单，在终端显示任务内容和状态 |
| `load_skill` | 按系统提示词中展示的精确技能名称读取完整 `SKILL.md` 说明 |

文件工具接受工作区相对路径或绝对路径，但路径解析后的结果必须仍在工作区内。所有工具都会返回字符串结果，执行错误也会以 `Error:` 开头的字符串反馈给模型。超长工具结果成功写入磁盘后，模型收到的字符串会包含完整结果文件路径和前 2,000 个字符的预览；工作区不可写时保留原始结果，不会因持久化失败中断主流程。

## Skills 扩展

每个 skill 使用工作区 `resources/skills` 下的一个直接子目录，并在子目录中提供 UTF-8 编码的 `SKILL.md`：

```text
resources/
└── skills/
    └── code-review/
        └── SKILL.md
```

推荐在文件开头使用 YAML frontmatter 声明技能名称和简介：

```markdown
---
name: code-review
description: 审查代码中的正确性、安全性和可维护性问题
---

# 使用说明

先检查改动范围，再按风险级别报告发现的问题。
```

frontmatter 必须由文件开头和结尾处各一行独立的 `---` 包围。`name` 和 `description` 均可省略：名称默认使用子目录名，简介默认取正文中的第一个非空标题或文本。供模型调用的技能名称应保持唯一；重名时按目录名排序后扫描，后扫描到的技能会覆盖同名条目。

程序为每轮用户对话构建系统提示词时会扫描技能目录，只将名称和简介组成的简短目录注入上下文。模型判断某个技能与当前任务相关后，再调用 `load_skill` 获取原始 `SKILL.md` 全文。这样可以避免在每次请求中预先携带所有技能内容。运行期间新增、删除或修改技能后，下一轮用户对话即可刷新目录。

扫描会忽略缺少 `SKILL.md` 的目录、符号链接和无法读取的文件。缺失、未闭合或无效的 frontmatter 不会阻止其他技能加载：名称会回退到目录名，能够识别正文时简介会取正文首个非空标题或文本，否则使用缺省说明。技能目录不存在时程序仍可正常运行，系统提示词会显示当前没有可用技能。

仓库提供了两个不会被扫描器直接加载的示例：`resources/skills/code-review/SKILL.md.example` 和 `resources/skills/test-runner/SKILL.md.example`。复制内容并将目标文件命名为 `SKILL.md` 后即可启用，例如：

```powershell
Copy-Item resources/skills/code-review/SKILL.md.example resources/skills/code-review/SKILL.md
```

## Memory 持久化

CLI 会把适合跨轮次复用的信息保存在工作区的 `resources/memory` 目录。每条记忆是一个 UTF-8 Markdown 文件，frontmatter 包含 `name`、`description` 和 `type`，正文保存完整内容；`type` 只接受 `user`、`feedback`、`project` 和 `reference`。程序同时维护 `MEMORY.md` 简短索引：

```text
resources/
└── memory/
    ├── MEMORY.md
    └── response-language-2bf92da143.md
```

一轮对话的处理方式如下：

1. 新一轮主请求前读取简短索引，并根据最近三条用户文本从全部记忆中选择最多 5 条相关内容。相关性选择优先调用当前模型；请求失败或输出无效时回退到本地中英文关键词匹配。
2. 选中的完整记忆会用 `<relevant_memories>` 标签附加到最近一条文本用户消息的请求副本。公开消息历史不会因此改变；系统提示词也明确要求记忆只能作为背景和偏好，不能覆盖当前用户指令。
3. CLI 展示最终回答后，会再调用当前模型从最近对话提取稳定的用户偏好、约束和项目事实。瞬时请求、工具输出、assistant 自行声称的事实和敏感信息会在提取提示词中明确排除；无新增内容时不写文件。
4. 记忆达到 `CONSOLIDATE_THRESHOLD`（默认 10）后，会额外请求模型合并重复、过时或矛盾的内容，并最多保留 30 条。只有新结果完成校验和写入后才删除被替代的旧文件；空数组、非法 JSON、接口错误或写入失败都不会清空旧记忆。

文件名由清洗后的名称和稳定摘要组成，不直接使用模型给出的路径；读取接口拒绝绝对路径、目录穿越、索引文件和符号链接。名称、简介和正文也有长度上限，损坏或无法解码的单个文件会被跳过。自动写入会同步重建 `MEMORY.md`；手工新增文件仍可参与相关性扫描，但简短索引要等下一次自动写入才会更新。

相关性选择、对话提取和达到阈值后的合并都会产生额外 Messages API 请求。所有步骤均采用尽力而为策略：只读工作区或辅助模型请求失败不会中断主回答，但新记忆无法持久化。`resources/memory` 已加入 `.gitignore` 和 `.dockerignore`，避免个人偏好及项目上下文被意外提交或发送进镜像构建上下文；仍应定期检查并清理其中不应长期保存的信息。

仓库提供 `response-language.md.example`、`project-testing.md.example` 两条记忆示例，以及配套的 `MEMORY.md.example` 索引。需要手工初始化时，把它们复制为 `.md` 文件：

```powershell
Copy-Item resources/memory/response-language.md.example resources/memory/response-language.md
Copy-Item resources/memory/project-testing.md.example resources/memory/project-testing.md
Copy-Item resources/memory/MEMORY.md.example resources/memory/MEMORY.md
```

示例索引中的链接已经使用复制后的 `.md` 文件名。后续自动提取或合并记忆时，程序会根据目录中的实际 `.md` 文件重新生成 `MEMORY.md`；所有以 `.md.example` 结尾的示例均不会被运行时加载。

## TODO 任务管理

`todo_write` 接收完整的 `todos` 数组，并用它整体替换上一份任务清单。每个任务都必须包含非空的 `content` 和以下三种 `status` 之一：

| 状态 | 含义 | 终端标记 |
| --- | --- | --- |
| `pending` | 尚未开始 | `[ ]` |
| `in_progress` | 正在处理 | `[▸]` |
| `completed` | 已经完成 | `[✓]` |

例如：

```json
{
  "todos": [
    {"content": "分析需求", "status": "completed"},
    {"content": "实现功能", "status": "in_progress"},
    {"content": "运行测试", "status": "pending"}
  ]
}
```

任务清单保存在进程内存中的 `CURRENT_TODOS`，不会写入磁盘，程序退出后即被清空。每次有效调用会先复制规范化后的任务，再整体替换旧清单；如果参数类型、字段集合、任务内容或状态无效，工具会返回以 `Error:` 开头的错误并保留原清单。工具 Schema 要求传入数组；本地处理器还兼容 JSON 数组字符串和 Python 列表字面量字符串，便于处理少数模型或调用方对参数的重复序列化。

运行时通过 `rounds_since_todo` 记录最近一次有效 TODO 更新后经历的工具调用轮数。连续三个工具调用轮次没有成功执行 `todo_write` 时，程序会在下一次模型请求前加入 `<reminder>Update your todos.</reminder>`，随后重新计算提醒间隔；成功更新 TODO 会立即清零计数器。

## 上下文压缩与大输出

`run_agent/compact.py` 在每次主模型请求前整理消息历史。这里的阈值按 Python 序列化后的字符数估算，不等同于模型的精确 token 数；它们用于提前控制上下文规模，而不是替代服务端的上下文限制。

处理顺序如下：

1. 消息超过 50 条时保留开头、结尾及一条裁剪标记。边界若落在 `tool_use` 与紧随其后的 `tool_result` 之间，会自动扩展保留范围，避免产生孤立工具消息。
2. 较早且超过 120 个字符的工具结果会替换为可重试提示，最近 3 条工具结果保持完整。
3. 最新一条用户消息中的多个工具结果以 200,000 个字符为目标预算，超出时从最大结果开始写入 `.task_outputs/tool-results/`。
4. 整体消息历史仍超过 50,000 个估算字符时，程序先尝试写入 `.transcripts/transcript_<时间>.jsonl`，再调用当前模型生成摘要并用摘要替换原历史。
5. 如果主请求仍返回明确的上下文溢出错误，程序会再次保存转录、生成响应式摘要、保留最近 5 条消息（含必要的完整工具消息对）并重试一次。同一次溢出重试仍失败时，原始异常会继续抛出，避免无限循环。

单个工具返回超过 30,000 个字符时，不必等到下一轮请求：运行时会立即使用经过清洗并附加摘要的工具调用编号作为文件名，把完整内容以 UTF-8 写入 `.task_outputs/tool-results/`，只把路径和前 2,000 个字符交给模型。上述两个目录都已加入 `.gitignore` 和 `.dockerignore`。如果工作区是只读挂载，文件写入会安全跳过：摘要仍可继续生成，但不会留下本地转录；大工具结果则保持原样。

## Hooks 说明

hooks 通过 `HOOKS` 注册表绑定到 Agent 生命周期事件。`trigger_hooks` 会按照注册顺序依次调用回调函数；某个回调返回非 `None` 值时，当前事件后续的回调不会继续执行，并由运行时处理该返回值。

| 事件 | 触发时机 | 当前回调 | 行为 |
| --- | --- | --- | --- |
| `UserPromptSubmit` | 用户问题加入消息历史前 | `context_inject_hook` | 在终端打印当前工作目录 |
| `PreToolUse` | 工具处理器执行前 | `permission_hook`、`log_hook` | 先检查命令权限，通过后打印工具及参数预览 |
| `PostToolUse` | 工具处理器执行后 | `large_output_hook` | 工具输出超过 100,000 个字符时打印告警 |
| `Stop` | 模型给出最终回答时 | `summary_hook` | 统计并打印当前会话累计的工具调用次数 |

Shell 命令权限由 `run_agent/config.py` 中的两个列表控制：

- `DENY_LIST`：硬禁止列表。命令包含其中任意片段时直接拒绝，不提供继续执行选项。
- `DESTRUCTIVE`：破坏性命令列表。命令包含其中任意片段时暂停执行并提示 `Allow? [y/N]`；只有输入 `y` 或 `yes` 才会放行，空输入、其他回答或终端中断均视为拒绝。

权限检查不区分大小写，并且一条命令最多触发一次人工确认。被拒绝的调用不会进入对应工具处理器，拒绝原因会作为 `tool_result` 返回给模型，使模型可以选择更安全的替代操作。

## 项目结构

```text
run-agent/
├── main.py              # 命令行入口与交互循环
├── run_agent/
│   ├── __init__.py      # Python 包标识
│   ├── compact.py       # 消息裁剪、工具结果持久化、转录及历史摘要
│   ├── config.py        # 工作区路径、环境变量、API 客户端及命令权限配置
│   ├── frontmatter_text.py # skills 与 memories 共用的 YAML frontmatter 解析
│   ├── hooks/
│   │   ├── __init__.py      # hooks 公共导入入口
│   │   ├── hooks.py         # 生命周期钩子、命令权限检查及日志统计
│   │   └── hooks_config.py  # 生命周期事件注册表
│   ├── memories.py      # 持久记忆读写、筛选、提取及合并
│   ├── prompt.py        # 系统提示词、技能目录与记忆索引组合
│   ├── runtime.py       # Agent 调用循环、记忆注入、上下文压缩与 hooks 触发点
│   ├── skills.py        # 技能扫描及按需加载
│   ├── todos.py         # TODO 输入规范化、内存状态及终端展示
│   └── tools/
│       ├── __init__.py      # 文件与 Shell 工具公共导入入口
│       ├── tools.py         # 路径校验及文件与 Shell 工具实现
│       └── tools_config.py  # 工具 Schema 与处理器注册表
├── resources/
│   ├── memory/          # 本地持久记忆、MEMORY.md 索引及 .md.example 示例
│   └── skills/          # 工作区技能目录及 SKILL.md.example 示例
├── tests/
│   ├── unit/
│   │   ├── test_agent.py            # Agent 运行时单元测试
│   │   ├── test_compact.py          # 上下文压缩、转录及溢出重试单元测试
│   │   ├── test_hooks.py            # 生命周期钩子及权限流程单元测试
│   │   ├── test_memories.py         # 记忆读写、筛选、提取、合并及注入单元测试
│   │   ├── test_skills.py           # 技能解析、扫描、加载及注册单元测试
│   │   ├── test_todo.py             # TODO 工具、输入校验及提醒流程单元测试
│   │   └── test_tools.py            # 本地工具单元测试
│   ├── integration/
│   │   └── test_agent_tool_flow.py  # Agent 与文件工具集成测试
│   └── conftest.py                   # 公共夹具和模拟对象
├── pytest.ini            # pytest 测试发现与临时目录配置
├── requirements.txt      # 运行依赖
├── requirements-dev.txt  # 运行依赖与测试依赖
├── .env.example         # 环境变量示例
├── Dockerfile           # 容器镜像定义
├── .dockerignore        # Docker 构建忽略规则
├── .gitignore           # Git 忽略规则
└── README.md             # 项目说明
```

## 环境要求

- Python 3.10 或更高版本
- 可用的 Anthropic API 密钥，或兼容 Anthropic API 的服务
- Docker 仅在使用容器运行时需要

## 配置环境变量

复制环境变量模板：

```powershell
Copy-Item .env.example .env
```

Linux 或 macOS：

```bash
cp .env.example .env
```

随后编辑 `.env`：

```dotenv
ANTHROPIC_API_KEY=your-api-key
MODEL_ID=your-model-id

# 可选，自定义兼容接口地址
# ANTHROPIC_BASE_URL=https://your-api-endpoint.example.com
```

| 变量 | 是否必需 | 说明 |
| --- | --- | --- |
| `ANTHROPIC_API_KEY` | 是 | Anthropic 或兼容服务的访问密钥 |
| `MODEL_ID` | 是 | 调用的模型 ID |
| `ANTHROPIC_BASE_URL` | 否 | 自定义兼容接口的基础地址 |

`.env` 已加入 `.gitignore` 和 `.dockerignore`，不要把真实密钥提交到代码仓库或构建进镜像。

## 本地运行

创建并激活虚拟环境：

```powershell
py -3.10 -m venv .venv
.\.venv\Scripts\Activate.ps1
```

安装依赖并启动：

```powershell
python -m pip install -r requirements.txt
python main.py
```

Linux 或 macOS 的激活命令为：

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
python main.py
```

输入问题并按回车发送；输入 `q`、`exit` 或空行退出。如果模型请求执行命中 `DESTRUCTIVE` 的命令，程序会暂停并等待确认；未明确输入 `y` 或 `yes` 时不会执行该命令。

## 运行测试

测试依赖与运行依赖分开维护。首次测试前安装开发依赖：

```powershell
python -m pip install -r requirements-dev.txt
```

运行全部测试：

```powershell
python -m pytest tests -v
```

也可以分别运行单元测试和集成测试：

```powershell
python -m pytest tests/unit -v
python -m pytest tests/integration -v
```

测试通过临时目录隔离文件操作，并模拟 Anthropic 客户端，因此不会访问真实模型 API，也不会改动项目中的业务文件。hooks 测试会模拟用户审批输入，验证禁止命令不会询问、破坏性命令默认拒绝，以及运行时不会执行被拦截的工具；skills 测试会验证 frontmatter 容错、目录扫描、重扫清理、提示词目录、按需加载和工具注册；memory 测试会验证安全文件名、路径穿越防护、UTF-8 索引、模型筛选及本地降级、提取校验、安全合并和请求副本注入；TODO 测试会验证工具 Schema、任务状态替换、字符串输入兼容、异常输入保护、三轮提醒和成功更新后的计数器重置；compact 测试会验证工具消息对边界、微压缩、安全文件名、结果预算、唯一转录、摘要生成、上下文溢出重试和非上下文异常透传。`pytest.ini` 会把临时测试文件放在项目内的 `.pytest_tmp` 目录，该目录已被 Git 和 Docker 忽略。

## Docker 运行

构建镜像：

```powershell
docker build -t run-agent .
```

Docker 镜像只安装 `requirements.txt` 中的运行依赖并复制程序代码；测试源码、pytest 配置、开发依赖、测试缓存、转录、工具输出和本地记忆目录会被 `.dockerignore` 排除，以减小生产镜像的构建上下文并避免携带个人信息。skills 和 memories 均从运行工作区 `/workspace/resources` 读取，不会预先打包进镜像。

推荐把当前目录以只读方式挂载到容器的 `/workspace`：

```powershell
docker run --rm -it --env-file .env -v "${PWD}:/workspace:ro" run-agent
```

此时 `read_file` 等读取操作、skills 加载和已有 memories 读取可以正常使用，但 `write_file`、`edit_file`、`delete_file`、`copy_file` 和 `move_file` 无法修改挂载的文件。新记忆、索引、转录和大工具结果也无法写入：主回答与上下文摘要仍能工作，程序会跳过记忆持久化、打印转录未保存提示，并保留无法落盘的原始工具输出。如果确实需要完整的文件管理及运行产物持久化能力，可将挂载参数末尾的 `:ro` 改为 `:rw`；执行前请先确认目录中没有重要或敏感文件。

挂载其他目录时，将 `${PWD}` 替换成对应目录的绝对路径即可：

```powershell
docker run --rm -it --env-file .env -v "D:\path\to\workspace:/workspace:ro" run-agent
```

## 工作流程

1. 每轮用户对话开始时扫描 skills、读取 `MEMORY.md`，并把两者的简短目录加入系统提示词。
2. 用户提交问题后触发 `UserPromptSubmit`，随后问题被加入消息历史。
3. 根据最近用户文本选择相关 memories；请求失败时使用本地关键词匹配。请求模型前再依次裁剪消息、微压缩旧工具结果并控制最新工具结果预算；仍超过阈值时保存转录并生成摘要。
4. 把相关记忆附加到整理后消息的请求副本，再将系统提示词、请求消息和 `tools_config.py` 中的全部工具 JSON Schema 发送给模型。若服务端报告上下文溢出，则响应式压缩并按 `MAX_REACTIVE_RETRIES` 重试。
5. 如果模型请求调用工具，先触发 `PreToolUse` 完成权限检查和调用日志记录。
6. 权限检查通过后，程序根据 `TOOL_HANDLERS` 注册表找到对应函数并执行；被拒绝的调用会跳过执行。相关技能的完整说明也在此阶段通过 `load_skill` 按需获取。
7. 模型进入工具调用轮次时增加 TODO 提醒计数；成功执行 `todo_write` 时清零，累计三轮未更新时在下一次请求前插入提醒。
8. 工具执行完成后先用原始结果触发 `PostToolUse`；超长字符串随后落盘，再作为 `tool_result` 加入消息历史并返回给模型。
9. 重复上述过程，直到模型给出最终回答；此时触发 `Stop` 输出会话工具调用统计。
10. CLI 先展示最终回答，再提取本轮新增记忆；达到合并阈值时校验并写入精简结果，最后更新 `MEMORY.md`。

## 安全建议

- 优先在临时目录、虚拟机或 Docker 容器中运行。
- 对不需要修改的工作区使用只读挂载。
- 不要把 `DENY_LIST` 和 `DESTRUCTIVE` 当作完整安全边界；字符串拼接、Shell 展开或其他命令变体可能绕过简单匹配。
- 文件工具虽然会校验工作区边界，但 Shell 工具仍可能绕过该边界。
- 不要让 Agent 接触 SSH 密钥、云凭据、生产配置或个人隐私文件。
- `.transcripts`、`.task_outputs` 和 `resources/memory` 可能包含模型上下文、完整工具输出、用户偏好或项目事实，虽然默认不会提交到 Git，也应按照工作区原始数据的敏感级别管理和清理。
- 执行期间留意终端中带有 `[HOOK] >` 标记的工具名称、参数预览及其返回结果。
- 若用于真实项目，应继续增加命令权限控制、路径校验、资源限制和审计日志。

## 常见问题

### 启动时报 `MODEL_ID` 相关错误

确认 `.env` 文件存在，并且 `MODEL_ID` 已填写为服务支持的模型 ID。

### API 返回鉴权错误

检查 `ANTHROPIC_API_KEY` 是否正确。如果使用自定义服务，同时确认 `ANTHROPIC_BASE_URL` 与该服务的接口格式兼容。

### pytest 提示系统临时目录拒绝访问

项目中的 `pytest.ini` 已将临时目录设置为可写的 `.pytest_tmp`。请在项目根目录运行测试：

```powershell
python -m pytest tests -v
```

如果命令仍然使用 `C:\Users\用户名\AppData\Local\Temp`，确认当前目录中存在 `pytest.ini`，并且没有通过环境变量或命令行参数覆盖其中的 `--basetemp` 配置。

### Docker 容器无法修改文件

README 中的推荐命令使用了只读挂载。只有在明确需要且了解风险时，才把 `:ro` 改成 `:rw`。

### 文件工具提示 `Path escapes workspace`

传入路径解析后位于程序启动目录之外。请改用工作区内部的相对路径，或把需要处理的目录挂载为 Docker 容器的 `/workspace`。

### 为什么 Shell 命令提示 `Allow? [y/N]`

该命令命中了 `DESTRUCTIVE` 中配置的破坏性片段。检查命令和参数后，输入 `y` 或 `yes` 可以允许本次执行；直接回车或输入其他内容会拒绝本次调用。命中 `DENY_LIST` 的命令属于硬禁止操作，不会显示确认选项。

### 为什么消息历史中出现 TODO reminder

模型已经连续三个工具调用轮次没有成功更新任务清单，运行时因此插入提醒，避免多步骤任务的进度长时间失去同步。提醒只会作为下一次模型请求的上下文，不会自动修改 `CURRENT_TODOS`；成功调用 `todo_write` 后计数器会清零。

### 为什么工具结果变成 `<persisted-output>`

该工具返回的字符串超过了 `PERSIST_THRESHOLD`（默认 30,000 个字符），或同一条消息中的工具结果总量超过预算。完整内容保存在 `.task_outputs/tool-results/`，标记中会给出文件路径；模型可以按需使用 `read_file` 再读取。文件名由清洗后的工具调用编号和摘要组成，不会把编号中的路径分隔符直接用于文件路径。

### 为什么终端显示 `transcript saved`

消息历史的估算规模超过 `CONTEXT_LIMIT`，或主请求触发了上下文溢出。程序会先把压缩前消息保存到 `.transcripts/`，再用模型摘要继续工作。两个输出目录默认不会提交到 Git；如果目录不可写，终端会显示 `transcript not saved`，摘要流程仍会继续。

### 为什么没有生成或加载 memory

新记忆只在 `python main.py` 的一轮对话给出最终回答后提取，直接调用 `agent_loop` 不会自动写入；瞬时请求、重复内容、敏感信息或模型返回的非法结构也会被跳过。加载时只选择与最近用户文本相关的记忆，不会把整个目录注入上下文。另请确认 `resources/memory` 可写；只读 Docker 挂载可以读取已有记忆，但无法创建文件或更新 `MEMORY.md`。辅助模型请求失败不会影响主回答，因此通常只表现为本轮没有新增或命中记忆。

### 为什么新建的 skill 没有出现在目录中

确认文件位于程序工作区的 `resources/skills/<技能目录>/SKILL.md`，文件使用 UTF-8 编码，且技能目录和清单文件不是符号链接。技能会在每轮用户对话构建系统提示词时重新扫描；Docker 中还需要确认包含 `resources/skills` 的宿主目录已经挂载到 `/workspace`。

### 复制或移动时提示目标已经存在

`copy_file` 和 `move_file` 为避免意外覆盖，不会替换已有目标。请先选择新的目标路径，或在确认安全后通过其他工具显式处理原文件。
