import os
from anthropic import Anthropic
from dotenv import load_dotenv
from run_agent.tools import (
    WORKDIR,
    run_bash,
    run_copy_file,
    run_delete_file,
    run_edit_file,
    run_move_file,
    run_read_file,
    run_todo_write,
    run_write_file,
)

# 本文件负责加载环境变量、初始化 Anthropic 客户端，并集中定义模型参数、系统提示词、工具描述和处理器映射。

# 优先读取项目的环境变量文件，使本地配置可以覆盖当前进程中的同名变量。
load_dotenv(override=True)

# 使用自定义接口地址时移除可能冲突的认证令牌，让客户端按照当前接口配置完成鉴权。
if os.getenv("ANTHROPIC_BASE_URL"):
    os.environ.pop("ANTHROPIC_AUTH_TOKEN", None)

# 根据可选的自定义接口地址创建全局客户端，并从环境变量取得运行时使用的模型编号。
client = Anthropic(base_url=os.getenv("ANTHROPIC_BASE_URL"))

MODEL = os.environ["MODEL_ID"]

# 定义 Agent 的职责、工作区边界和回复语言要求，供每次模型请求统一使用。
SYSTEM = f"You are an educational file-management agent that helps users safely explore the workspace rooted at {WORKDIR} by listing directories, locating files, inspecting file metadata, and reading or explaining file contents while clearly describing each operation and never accessing paths outside the workspace.Your final answer must always be written in the same language as the user’s query."

# 使用 Anthropic 工具调用格式声明全部可用工具及其参数约束，让模型能够生成结构化调用请求。
TOOLS = [
    {
        "name": "bash",
        "description": "Run a shell command with the workspace as the working directory and return its output.",
        "input_schema": {
            "type": "object",
            "properties": {
                "command": {
                    "type": "string",
                    "description": "The shell command to execute.",
                }
            },
            "required": ["command"],
        }
    },
    {
        "name": "read_file",
        "description": "Read a UTF-8 text file inside the workspace, optionally limiting the number of returned lines.",
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "The workspace-relative or absolute path of the file to read.",
                },
                "limit": {
                    "type": "integer",
                    "minimum": 1,
                    "description": "The optional maximum number of lines to return.",
                },
            },
            "required": ["path"],
        }
    },
    {
        "name": "edit_file",
        "description": "Replace the first exact occurrence of text in a UTF-8 file inside the workspace.",
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "The workspace-relative or absolute path of the file to edit.",
                },
                "old_text": {
                    "type": "string",
                    "minLength": 1,
                    "description": "The exact text to replace; only its first occurrence is changed.",
                },
                "new_text": {
                    "type": "string",
                    "description": "The replacement text, which may be empty.",
                },
            },
            "required": ["path", "old_text", "new_text"],
        }
    },
    {
        "name": "write_file",
        "description": "Create or overwrite a UTF-8 text file inside the workspace, creating parent directories as needed.",
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "The workspace-relative or absolute path of the file to write.",
                },
                "content": {
                    "type": "string",
                    "description": "The complete text content to write to the file.",
                },
            },
            "required": ["path", "content"],
        }
    },
    {
        "name": "delete_file",
        "description": "Delete a single file inside the workspace; directories are never removed.",
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "The workspace-relative or absolute path of the file to delete.",
                }
            },
            "required": ["path"],
        }
    },
    {
        "name": "copy_file",
        "description": "Copy a file to a new path inside the workspace without overwriting an existing destination.",
        "input_schema": {
            "type": "object",
            "properties": {
                "old_path": {
                    "type": "string",
                    "description": "The workspace-relative or absolute path of the source file.",
                },
                "new_path": {
                    "type": "string",
                    "description": "The workspace-relative or absolute destination path.",
                },
            },
            "required": ["old_path", "new_path"],
        }
    },
    {
        "name": "move_file",
        "description": "Move or rename a file inside the workspace without overwriting an existing destination.",
        "input_schema": {
            "type": "object",
            "properties": {
                "old_path": {
                    "type": "string",
                    "description": "The workspace-relative or absolute path of the source file.",
                },
                "new_path": {
                    "type": "string",
                    "description": "The workspace-relative or absolute destination path.",
                },
            },
            "required": ["old_path", "new_path"],
        }
    },
    # 声明用于整体替换当前任务清单的 TODO 工具，并限制每个任务必须包含内容和合法状态。
    {
        "name": "todo_write",
        "description": "Create or replace the current todo list to track multi-step work and display its latest state.",
        "input_schema": {
            "type": "object",
            "properties": {
                "todos": {
                    "type": "array",
                    "description": "The complete todo list that replaces the previous list.",
                    "items": {
                        "type": "object",
                        "properties": {
                            "content": {
                                "type": "string",
                                "minLength": 1,
                                "description": "A concise description of the task.",
                            },
                            "status": {
                                "type": "string",
                                "enum": ["pending", "in_progress", "completed"],
                                "description": "The current task state.",
                            },
                        },
                        "required": ["content", "status"],
                        "additionalProperties": False,
                    }
                },
            },
            "required": ["todos"],
            "additionalProperties": False,
        }
    }
]

# 将模型返回的工具名称映射到本地实现，运行时据此查找并执行对应函数。
TOOL_HANDLERS = {
    "bash": run_bash,
    "read_file": run_read_file,
    "edit_file": run_edit_file,
    "write_file": run_write_file,
    "copy_file": run_copy_file,
    "move_file": run_move_file,
    "delete_file": run_delete_file,
    "todo_write": run_todo_write,
}

# 定义生命周期事件与回调列表的注册表，hooks 模块会按事件名称向其中追加处理函数。
HOOKS = {
    "UserPromptSubmit": [],
    "PreToolUse": [],
    "PostToolUse": [],
    "Stop": [],
}

# 配置 Shell 命令硬禁止列表；命中任意片段时不向用户询问，直接拒绝执行。
DENY_LIST = ["rm -rf /", "sudo", "shutdown", "reboot", "mkfs", "dd if="]

# 配置需要人工确认的破坏性命令片段；只有用户明确输入 y 或 yes 后才会放行。
DESTRUCTIVE = ["rm ", "> /etc/", "chmod 777"]
