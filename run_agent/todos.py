import ast
import json

# 本文件负责解析、校验并展示 todo_write 提交的完整任务清单。

# 在内存中保存最近一次由 todo_write 提交的完整任务清单，新的有效调用会整体替换旧内容。
CURRENT_TODOS: list[dict[str, str]] = []

# TODO 工具允许使用的三种任务状态，与工具 JSON Schema 中声明的枚举保持一致。
TODO_STATUSES: tuple[str, ...] = ("pending", "in_progress", "completed")

# 各任务状态在终端中的展示图标，进行中和已完成状态使用颜色突出当前进度。
STATUS_ICONS: dict[str, str] = {
    "pending": " ",
    "in_progress": "\033[36m▸\033[0m",
    "completed": "\033[32m✓\033[0m",
}


# 兼容列表、JSON 数组字符串和 Python 字面量字符串，并在全部条目通过校验后返回独立的规范化副本。
def _normalize_todos(
    todos: list | str,
) -> tuple[list[dict[str, str]] | None, str | None]:
    if isinstance(todos, str):
        try:
            todos = json.loads(todos)
        except json.JSONDecodeError:
            try:
                todos = ast.literal_eval(todos)
            except (SyntaxError, ValueError):
                return None, "Error: todos must be a list or JSON array string"

    if not isinstance(todos, list):
        return None, "Error: todos must be a list"

    normalized_todos = []
    for index, todo in enumerate(todos):
        if not isinstance(todo, dict):
            return None, f"Error: todo at index {index} must be an object"
        if set(todo) != {"content", "status"}:
            return (
                None,
                f"Error: todo at index {index} must contain only content and status",
            )

        content = todo["content"]
        status = todo["status"]
        if not isinstance(content, str) or not content.strip():
            return None, f"Error: todo at index {index} must have non-empty content"
        if not isinstance(status, str) or status not in TODO_STATUSES:
            return None, f"Error: todo at index {index} has invalid status"
        normalized_todos.append({"content": content, "status": status})
    return normalized_todos, None


# 校验并整体更新当前任务清单；成功后在终端打印带状态图标的任务视图，失败时保留原有清单。
def run_todo_write(todos: list | str) -> str:
    global CURRENT_TODOS

    normalized_todos, error = _normalize_todos(todos)
    if error:
        return error

    CURRENT_TODOS = normalized_todos
    lines = ["\n\033[33m## Current Tasks\033[0m"]
    for todo in CURRENT_TODOS:
        icon = STATUS_ICONS[todo["status"]]
        lines.append(f"  [{icon}] {todo['content']}")
    print("\n".join(lines))
    return f"Updated {len(CURRENT_TODOS)} tasks"
