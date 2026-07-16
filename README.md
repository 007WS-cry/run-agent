# Run Agent

Run Agent 是一个用于学习 AI Agent 基本工作方式的轻量项目。程序把 Anthropic 模型与一组本地工具连接起来：模型可以读取、编辑和管理工作区文件，也可以执行 Shell 命令、维护 TODO 任务清单，并按需加载工作区中的 skills；程序会把每次工具执行结果返回给模型，直到模型不再请求工具。

当前系统提示词将它定位为文件管理学习助手，适合列出目录、查找文件、查看文件信息，以及读取、解释和管理工作区中的内容。

> [!WARNING]
> 本项目会执行模型生成的工具调用和 Shell 命令。专用文件工具会校验路径必须位于工作区内，权限 hook 也会禁止或要求确认部分危险命令，但这些检查基于简单的字符串匹配，不是真正的安全沙箱，也不能保证 Shell 命令不会访问工作区之外。请勿在含有重要文件或敏感数据的目录中直接运行；仅需查看文件时，建议通过 Docker 只读挂载工作区。

## 功能

- 通过 Anthropic Messages API 与模型交互
- 支持 Bash、读取、编辑、写入、删除、复制、移动、TODO 管理和技能加载共 9 个工具
- 通过工具处理器映射统一分发模型的工具调用
- 支持用 `todo_write` 整体更新内存任务清单，并在连续三个工具调用轮次未更新时提醒模型维护 TODO
- 启动时扫描 `resources/skills/*/SKILL.md`，把技能名称和简介加入系统提示词，并通过 `load_skill` 按需加载完整说明
- 支持 `UserPromptSubmit`、`PreToolUse`、`PostToolUse` 和 `Stop` 生命周期 hooks
- 禁止执行 `DENY_LIST` 中的 Shell 命令，并在执行 `DESTRUCTIVE` 命令前请求用户确认
- 在终端显示工具调用预览、大输出告警、当前工作目录和会话工具调用统计
- 专用文件工具会拒绝访问工作区之外的路径
- 复制和移动不会覆盖已经存在的目标文件
- Shell 命令最长执行 120 秒
- Shell 命令单次最多返回 50,000 个字符
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

文件工具接受工作区相对路径或绝对路径，但路径解析后的结果必须仍在工作区内。所有工具都会返回字符串结果，执行错误也会以 `Error:` 开头的字符串反馈给模型。

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

程序构建系统提示词时会扫描一次技能目录，只将名称和简介组成的简短目录注入上下文。模型判断某个技能与当前任务相关后，再调用 `load_skill` 获取原始 `SKILL.md` 全文。这样可以避免在每次请求中预先携带所有技能内容。运行期间新增、删除或修改技能后，需要重新启动程序才能刷新目录。

扫描会忽略缺少 `SKILL.md` 的目录、符号链接和无法读取的文件。缺失、未闭合或无效的 frontmatter 不会阻止其他技能加载：名称会回退到目录名，能够识别正文时简介会取正文首个非空标题或文本，否则使用缺省说明。技能目录不存在时程序仍可正常运行，系统提示词会显示当前没有可用技能。

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

任务清单保存在进程内存中的 `CURRENT_TODOS`，不会写入磁盘，程序退出后即被清空。每次有效调用会整体替换旧清单；如果参数类型、任务内容或状态无效，工具会返回以 `Error:` 开头的错误并保留原清单。

运行时通过 `rounds_since_todo` 记录最近一次有效 TODO 更新后经历的工具调用轮数。连续三个工具调用轮次没有成功执行 `todo_write` 时，程序会在下一次模型请求前加入 `<reminder>Update your todos.</reminder>`，随后重新计算提醒间隔；成功更新 TODO 会立即清零计数器。

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
│   ├── config.py        # 环境变量、API 客户端、工具及 hooks 配置
│   ├── hooks.py         # 生命周期钩子、命令权限检查及日志统计
│   ├── prompt.py        # 系统提示词与技能目录组合
│   ├── runtime.py       # Agent 调用循环与 hooks 触发点
│   ├── skills.py        # 技能扫描、frontmatter 解析及按需加载
│   └── tools.py         # 路径校验及其他本地工具实现
├── resources/
│   └── skills/          # 工作区技能目录，每个子目录包含一个 SKILL.md
├── tests/
│   ├── unit/
│   │   ├── test_agent.py            # Agent 运行时单元测试
│   │   ├── test_hooks.py            # 生命周期钩子及权限流程单元测试
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

测试通过临时目录隔离文件操作，并模拟 Anthropic 客户端，因此不会访问真实模型 API，也不会改动项目中的业务文件。hooks 测试会模拟用户审批输入，验证禁止命令不会询问、破坏性命令默认拒绝，以及运行时不会执行被拦截的工具；skills 测试会验证 frontmatter 容错、目录扫描、重扫清理、提示词目录、按需加载和工具注册；TODO 测试会验证工具 Schema、任务状态替换、异常输入保护、三轮提醒和成功更新后的计数器重置。`pytest.ini` 会把临时测试文件放在项目内的 `.pytest_tmp` 目录，该目录已被 Git 和 Docker 忽略。

## Docker 运行

构建镜像：

```powershell
docker build -t run-agent .
```

Docker 镜像只安装 `requirements.txt` 中的运行依赖并复制程序代码；测试源码、pytest 配置、开发依赖和测试缓存会被 `.dockerignore` 排除，以减小生产镜像的构建上下文。skills 从运行工作区 `/workspace/resources/skills` 扫描，不会预先打包进镜像。

推荐把当前目录以只读方式挂载到容器的 `/workspace`：

```powershell
docker run --rm -it --env-file .env -v "${PWD}:/workspace:ro" run-agent
```

此时 `read_file` 等读取操作以及 skills 加载可以正常使用，但 `write_file`、`edit_file`、`delete_file`、`copy_file` 和 `move_file` 无法修改挂载的文件。如果确实需要完整的文件管理能力，可将挂载参数末尾的 `:ro` 改为 `:rw`；执行前请先确认目录中没有重要或敏感文件。

挂载其他目录时，将 `${PWD}` 替换成对应目录的绝对路径即可：

```powershell
docker run --rm -it --env-file .env -v "D:\path\to\workspace:/workspace:ro" run-agent
```

## 工作流程

1. 程序启动时扫描 skills，并把名称和简介组成的目录加入系统提示词。
2. 用户提交问题后触发 `UserPromptSubmit`，随后问题被加入消息历史。
3. 程序把系统提示词、消息和全部工具的 JSON Schema 发送给模型。
4. 如果模型请求调用工具，先触发 `PreToolUse` 完成权限检查和调用日志记录。
5. 权限检查通过后，程序根据工具名称从 `TOOL_HANDLERS` 中找到对应函数并执行；被拒绝的调用会跳过执行。相关技能的完整说明也在此阶段通过 `load_skill` 按需获取。
6. 模型进入工具调用轮次时增加 TODO 提醒计数；成功执行 `todo_write` 时清零，累计三轮未更新时在下一次请求前插入提醒。
7. 工具执行完成后触发 `PostToolUse`，再把返回值作为 `tool_result` 加入消息历史并返回给模型。
8. 重复上述过程，直到模型给出最终回答；此时触发 `Stop` 输出会话工具调用统计。

## 安全建议

- 优先在临时目录、虚拟机或 Docker 容器中运行。
- 对不需要修改的工作区使用只读挂载。
- 不要把 `DENY_LIST` 和 `DESTRUCTIVE` 当作完整安全边界；字符串拼接、Shell 展开或其他命令变体可能绕过简单匹配。
- 文件工具虽然会校验工作区边界，但 Shell 工具仍可能绕过该边界。
- 不要让 Agent 接触 SSH 密钥、云凭据、生产配置或个人隐私文件。
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

### 为什么新建的 skill 没有出现在目录中

确认文件位于程序工作区的 `resources/skills/<技能目录>/SKILL.md`，文件使用 UTF-8 编码，且技能目录和清单文件不是符号链接。技能只在系统提示词构建时扫描；如果程序已经启动，请退出后重新运行 `python main.py`。Docker 中还需要确认包含 `resources/skills` 的宿主目录已经挂载到 `/workspace`。

### 复制或移动时提示目标已经存在

`copy_file` 和 `move_file` 为避免意外覆盖，不会替换已有目标。请先选择新的目标路径，或在确认安全后通过其他工具显式处理原文件。
