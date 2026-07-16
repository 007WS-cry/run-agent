from run_agent.tools.tools import (
    run_bash,
    run_copy_file,
    run_delete_file,
    run_edit_file,
    run_move_file,
    run_read_file,
    run_write_file,
)
from run_agent.todos import run_todo_write
from run_agent.skills import load_skill

# 本文件集中声明 Anthropic 工具 Schema 及其本地处理器注册表，使工具实现与运行时配置保持解耦。

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
    },
    # 声明把独立编码任务委派给受限 Subagent 的工具；任务说明不能为空，也不接受未声明的额外参数。
    {
        "name": "task",
        "description": "Delegate a self-contained coding task to a subagent and return its final summary.",
        "input_schema": {
            "type": "object",
            "properties": {
                "description": {
                    "type": "string",
                    "minLength": 1,
                    "description": "A complete, self-contained description of the coding task.",
                }
            },
            "required": ["description"],
            "additionalProperties": False,
        }
    },
    # 声明按名称加载完整技能说明的工具；名称必须来自系统提示词中的启动时技能目录。
    {
        "name": "load_skill",
        "description": "Load the complete SKILL.md instructions for an available skill by its catalog name.",
        "input_schema": {
            "type": "object",
            "properties": {
                "name": {
                    "type": "string",
                    "minLength": 1,
                    "description": "The exact skill name shown in the available skills catalog.",
                }
            },
            "required": ["name"],
            "additionalProperties": False,
        }
    }
]

# 保存 Subagent 可以调用的基础工具名称；排除 task、TODO 和技能工具以限制职责并阻止递归委派。
SUBTOOL_NAMES = frozenset({
    "bash",
    "read_file",
    "edit_file",
    "write_file",
    "delete_file",
    "copy_file",
    "move_file",
})

# 从主工具 Schema 中筛选 Subagent 工具，复用同一份参数约束以避免两套声明逐渐不一致。
SUBTOOLS = [tool for tool in TOOLS if tool["name"] in SUBTOOL_NAMES]

# 将 Subagent 可用工具名称映射到已有本地实现，确保子任务使用与主 Agent 相同的路径和权限边界。
SUBTOOL_HANDLERS = {
    "bash": run_bash,
    "read_file": run_read_file,
    "edit_file": run_edit_file,
    "write_file": run_write_file,
    "copy_file": run_copy_file,
    "move_file": run_move_file,
    "delete_file": run_delete_file,
}


# 延迟导入 Subagent 入口并执行委派任务，避免工具配置与 Subagent 模块在初始化阶段形成循环依赖。
def run_task(description: str) -> str:
    from run_agent.subagent import spawn_subagent

    return spawn_subagent(description)


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
    "task": run_task,
    "load_skill": load_skill,
}
