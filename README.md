# Run Agent

Run Agent 是一个用于学习 AI Agent 基本工作方式的最小项目。程序把 Anthropic 模型与一个 Shell 工具连接起来：模型可以生成命令，程序执行命令并把结果返回给模型，直到模型不再请求工具。

当前系统提示词将它定位为文件管理学习助手，适合列出目录、查找文件、查看文件信息，以及读取和解释工作区中的内容。

> [!WARNING]
> 本项目会执行模型生成的 Shell 命令。`run_bash` 仅拦截少量明显危险的命令，不是真正的安全沙箱。请勿在含有重要文件或敏感数据的目录中直接运行；使用 Docker 时建议只读挂载工作区。

## 功能

- 通过 Anthropic Messages API 与模型交互
- 支持模型调用 Shell 工具并连续处理执行结果
- Shell 命令最长执行 120 秒
- 单次工具输出最多返回 50,000 个字符
- 支持 `.env` 环境变量和自定义 Anthropic API 地址
- 提供非 root 用户运行的 Docker 环境

## 项目结构

```text
run-agent/
├── main.py            # Agent 主程序
├── requirements.txt   # Python 依赖
├── .env.example       # 环境变量示例
├── Dockerfile         # 容器镜像定义
├── .dockerignore      # Docker 构建忽略规则
├── .gitignore         # Git 忽略规则
└── README.md           # 项目说明
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

此时 Agent 可以查看当前目录，但不能修改挂载的文件。如果确实需要让它写入工作区，可将挂载参数末尾的 `:ro` 改为 `:rw`；执行前请先确认目录中没有重要或敏感文件。

挂载其他目录时，将 `${PWD}` 替换成对应目录的绝对路径即可：

```powershell
docker run --rm -it --env-file .env -v "D:\path\to\workspace:/workspace:ro" run-agent
```

## 工作流程

1. 用户的问题被加入消息历史。
2. 程序把消息和 Shell 工具定义发送给模型。
3. 如果模型请求调用工具，程序通过 `run_bash` 执行命令。
4. 命令输出作为工具结果返回给模型。
5. 重复上述过程，直到模型给出最终回答。

## 安全建议

- 优先在临时目录、虚拟机或 Docker 容器中运行。
- 对不需要修改的工作区使用只读挂载。
- 不要让 Agent 接触 SSH 密钥、云凭据、生产配置或个人隐私文件。
- 执行前观察终端中以 `$` 开头显示的命令。
- 若用于真实项目，应继续增加命令权限控制、路径校验、资源限制和审计日志。

## 常见问题

### 启动时报 `MODEL_ID` 相关错误

确认 `.env` 文件存在，并且 `MODEL_ID` 已填写为服务支持的模型 ID。

### API 返回鉴权错误

检查 `ANTHROPIC_API_KEY` 是否正确。如果使用自定义服务，同时确认 `ANTHROPIC_BASE_URL` 与该服务的接口格式兼容。

### Docker 容器无法修改文件

README 中的推荐命令使用了只读挂载。只有在明确需要且了解风险时，才把 `:ro` 改成 `:rw`。
