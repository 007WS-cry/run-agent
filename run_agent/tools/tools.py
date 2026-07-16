import shutil
import subprocess
from pathlib import Path

from run_agent.config import WORKDIR

# 本文件实现 Agent 可调用的本地工具，通过工作区路径校验和统一的错误返回完成命令执行及文件增删改查。

# 解析并校验目标路径；将输入拼接到工作区后规范化，再用父子路径关系阻止访问工作区之外的位置。
def safe_path(path: str) -> Path:
    if not isinstance(path, str) or not path.strip():
        raise ValueError("Path must be a non-empty string")

    resolved_path = (WORKDIR / path).resolve()
    if not resolved_path.is_relative_to(WORKDIR):
        raise ValueError(f"Path escapes workspace: {path}")
    return resolved_path

# 执行 Shell 命令并返回输出；先用关键字拦截明显危险命令，再通过子进程限时运行并合并标准输出与错误输出。
def run_bash(command: str) -> str:
    if not isinstance(command, str):
        return "Error: command must be a string"

    dangerous = ["rm -rf /", "sudo", "shutdown", "reboot", "> /dev/"]
    normalized_command = command.casefold()
    if any(pattern.casefold() in normalized_command for pattern in dangerous):
        return "Error: Dangerous command blocked"

    try:
        result = subprocess.run(
            command,
            shell=True,
            cwd=WORKDIR,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=120,
        )
        output = (result.stdout + result.stderr).strip()
        return output[:50000] if output else "(no output)"
    except subprocess.TimeoutExpired:
        return "Error: Timeout (120s)"
    except (FileNotFoundError, OSError) as error:
        return f"Error: {error}"

# 读取工作区内的 UTF-8 文本文件；先校验路径，再按可选行数截断内容并将异常转换为统一错误字符串。
def run_read_file(path: str, limit: int | None = None) -> str:
    try:
        if limit is not None and (
            isinstance(limit, bool) or not isinstance(limit, int) or limit < 1
        ):
            return "Error: limit must be a positive integer"

        content = safe_path(path).read_text(encoding="utf-8", errors="replace")
        if limit is None:
            return content

        lines = content.splitlines()
        if len(lines) > limit:
            remaining = len(lines) - limit
            lines = lines[:limit] + [f"... ({remaining} more lines)"]
        return "\n".join(lines)
    except Exception as error:
        return f"Error: {error}"

# 编辑工作区内的文本文件；读取完整内容后只替换首次出现的目标文本，并通过统一错误字符串反馈失败原因。
def run_edit_file(path: str, old_text: str, new_text: str) -> str:
    try:
        if not old_text:
            return "Error: old_text must not be empty"

        file_path = safe_path(path)
        content = file_path.read_text(encoding="utf-8", errors="replace")
        if old_text not in content:
            return f"Error: text not found in {path}"

        file_path.write_text(
            content.replace(old_text, new_text, 1),
            encoding="utf-8",
        )
        return f"Edited {path}"
    except Exception as error:
        return f"Error: {error}"

# 创建或覆盖工作区内的文本文件；校验路径后递归创建父目录，以 UTF-8 写入内容并返回实际字节数。
def run_write_file(path: str, content: str) -> str:
    try:
        file_path = safe_path(path)
        file_path.parent.mkdir(parents=True, exist_ok=True)
        file_path.write_text(content, encoding="utf-8")
        byte_count = len(content.encode("utf-8"))
        return f"Wrote {byte_count} bytes to {path}"
    except Exception as error:
        return f"Error: {error}"

# 删除工作区内的单个文件；校验路径并显式拒绝目录后调用文件删除操作，同时将异常转换为错误字符串。
def run_delete_file(path: str) -> str:
    try:
        file_path = safe_path(path)
        if file_path.is_dir():
            return f"Error: path is a directory: {path}"
        file_path.unlink()
        return f"Deleted {path}"
    except Exception as error:
        return f"Error: {error}"

# 复制工作区内的文件；分别校验源路径和目标路径，拒绝覆盖现有目标并保留源文件元数据。
def run_copy_file(old_path: str, new_path: str) -> str:
    try:
        source = safe_path(old_path)
        destination = safe_path(new_path)
        if not source.is_file():
            return f"Error: source is not a file: {old_path}"
        if destination.exists():
            return f"Error: destination already exists: {new_path}"

        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination)
        return f"Copied {old_path} to {new_path}"
    except Exception as error:
        return f"Error: {error}"

# 移动或重命名工作区内的文件；校验两端路径并拒绝覆盖后创建父目录，再通过文件系统移动源文件。
def run_move_file(old_path: str, new_path: str) -> str:
    try:
        source = safe_path(old_path)
        destination = safe_path(new_path)
        if not source.is_file():
            return f"Error: source is not a file: {old_path}"
        if destination.exists():
            return f"Error: destination already exists: {new_path}"

        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(source), str(destination))
        return f"Moved {old_path} to {new_path}"
    except Exception as error:
        return f"Error: {error}"
