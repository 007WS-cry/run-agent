import os
import shutil
import subprocess
from pathlib import Path


WORKDIR = Path.cwd().resolve()


def safe_path(path: str) -> Path:
    """Resolve a path and ensure it stays inside the workspace."""
    if not isinstance(path, str) or not path.strip():
        raise ValueError("Path must be a non-empty string")

    resolved_path = (WORKDIR / path).resolve()
    if not resolved_path.is_relative_to(WORKDIR):
        raise ValueError(f"Path escapes workspace: {path}")
    return resolved_path


def run_bash(command: str) -> str:
    dangerous = ["rm -rf /", "sudo", "shutdown", "reboot", "> /dev/"]
    if any(pattern in command for pattern in dangerous):
        return "Error: Dangerous command blocked"

    try:
        result = subprocess.run(
            command,
            shell=True,
            cwd=os.getcwd(),
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


def run_read_file(path: str, limit: int | None = None) -> str:
    try:
        if limit is not None and limit < 1:
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


def run_write_file(path: str, content: str) -> str:
    try:
        file_path = safe_path(path)
        file_path.parent.mkdir(parents=True, exist_ok=True)
        file_path.write_text(content, encoding="utf-8")
        byte_count = len(content.encode("utf-8"))
        return f"Wrote {byte_count} bytes to {path}"
    except Exception as error:
        return f"Error: {error}"


def run_delete_file(path: str) -> str:
    try:
        file_path = safe_path(path)
        if file_path.is_dir():
            return f"Error: path is a directory: {path}"
        file_path.unlink()
        return f"Deleted {path}"
    except Exception as error:
        return f"Error: {error}"


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
