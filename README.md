# Run Agent

Run Agent 是一个用于学习 AI Agent 基本工作方式的轻量项目。程序把 Anthropic 模型与一组本地工具连接起来：模型可以读取、编辑和管理工作区文件，也可以执行 Shell 命令；程序会把每次工具执行结果返回给模型，直到模型不再请求工具。

当前系统提示词将它定位为文件管理学习助手，适合列出目录、查找文件、查看文件信息，以及读取、解释和管理工作区中的内容。

> [!WARNING]
> 本项目会执行模型生成的工具调用和 Shell 命令。专用文件工具会校验路径必须位于工作区内，但 `run_bash` 仅拦截少量明显危险的命令，不是真正的安全沙箱，也不能保证 Shell 命令不会访问工作区之外。请勿在含有重要文件或敏感数据的目录中直接运行；仅需查看文件时，建议通过 Docker 只读挂载工作区。

## 功能

- 通过 Anthropic Messages API 与模型交互
- 支持 Bash、读取、编辑、写入、删除、复制和移动共 7 个工具
- 通过工具处理器映射统一分发模型的工具调用
- 专用文件工具会拒绝访问工作区之外的路径
- 复制和移动不会覆盖已经存在的目标文件
- Shell 命令最长执行 120 秒
- 单次工具输出最多返回 50,000 个字符
- 支持 `.env` 环境变量和自定义 Anthropic API 地址
- 提供非 root 用户运行的 Docker 环境

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

文件工具接受工作区相对路径或绝对路径，但路径解析后的结果必须仍在工作区内。所有工具都会返回字符串结果，执行错误也会以 `Error:` 开头的字符串反馈给模型。

## 项目结构

```text
run-agent/
├── main.py              # 命令行入口与交互循环
├── run_agent/
│   ├── __init__.py      # Python 包标识
│   ├── config.py        # 环境变量、API 客户端及工具定义
│   ├── runtime.py       # Agent 调用循环
│   └── tools.py         # 路径校验及所有本地工具实现
├── requirements.txt     # Python 依赖
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

输入问题并按回车发送；输入 `q`、`exit` 或空行退出。

## Docker 运行

构建镜像：

```powershell
docker build -t run-agent .
```

推荐把当前目录以只读方式挂载到容器的 `/workspace`：

```powershell
docker run --rm -it --env-file .env -v "${PWD}:/workspace:ro" run-agent
```

此时 `read_file` 等读取操作可以正常使用，但 `write_file`、`edit_file`、`delete_file`、`copy_file` 和 `move_file` 无法修改挂载的文件。如果确实需要完整的文件管理能力，可将挂载参数末尾的 `:ro` 改为 `:rw`；执行前请先确认目录中没有重要或敏感文件。

挂载其他目录时，将 `${PWD}` 替换成对应目录的绝对路径即可：

```powershell
docker run --rm -it --env-file .env -v "D:\path\to\workspace:/workspace:ro" run-agent
```

## 工作流程

1. 用户的问题被加入消息历史。
2. 程序把消息和全部工具的 JSON Schema 发送给模型。
3. 如果模型请求调用工具，程序根据工具名称从 `TOOL_HANDLERS` 中找到对应函数并执行。
4. 函数返回值作为 `tool_result` 加入消息历史并返回给模型。
5. 重复上述过程，直到模型给出最终回答。

## 安全建议

- 优先在临时目录、虚拟机或 Docker 容器中运行。
- 对不需要修改的工作区使用只读挂载。
- 文件工具虽然会校验工作区边界，但 Shell 工具仍可能绕过该边界。
- 不要让 Agent 接触 SSH 密钥、云凭据、生产配置或个人隐私文件。
- 执行期间留意终端中以 `>` 开头显示的工具名称及其返回结果。
- 若用于真实项目，应继续增加命令权限控制、路径校验、资源限制和审计日志。

## 常见问题

### 启动时报 `MODEL_ID` 相关错误

确认 `.env` 文件存在，并且 `MODEL_ID` 已填写为服务支持的模型 ID。

### API 返回鉴权错误

检查 `ANTHROPIC_API_KEY` 是否正确。如果使用自定义服务，同时确认 `ANTHROPIC_BASE_URL` 与该服务的接口格式兼容。

### Docker 容器无法修改文件

README 中的推荐命令使用了只读挂载。只有在明确需要且了解风险时，才把 `:ro` 改成 `:rw`。

### 文件工具提示 `Path escapes workspace`

传入路径解析后位于程序启动目录之外。请改用工作区内部的相对路径，或把需要处理的目录挂载为 Docker 容器的 `/workspace`。

### 复制或移动时提示目标已经存在

`copy_file` 和 `move_file` 为避免意外覆盖，不会替换已有目标。请先选择新的目标路径，或在确认安全后通过其他工具显式处理原文件。
