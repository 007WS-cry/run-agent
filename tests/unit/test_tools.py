import os
import subprocess
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from run_agent import tools

# 本文件对全部本地工具进行单元测试，通过隔离工作区和模拟子进程验证路径安全、文件操作、命令拦截及异常处理。

# 验证工作区内部路径可以正常解析；传入相对路径并比较规范化后的绝对路径结果。
def test_safe_path_accepts_paths_inside_workspace(workspace):
    result = tools.safe_path("nested/example.txt")

    assert result == workspace / "nested" / "example.txt"

@pytest.mark.parametrize("path", ["", "   ", None])
# 验证空路径和非字符串路径会被拒绝；参数化多种无效输入并检查统一的 ValueError 信息。
def test_safe_path_rejects_empty_paths(workspace, path):
    with pytest.raises(ValueError, match="non-empty string"):
        tools.safe_path(path)

# 验证路径不能逃离工作区；传入上级目录路径并检查边界校验抛出的异常。
def test_safe_path_rejects_paths_outside_workspace(workspace):
    with pytest.raises(ValueError, match="escapes workspace"):
        tools.safe_path("../outside.txt")

# 验证文件写入、完整读取和限行读取；写入多行文本后同时检查磁盘内容、截断提示和非法行数处理。
def test_write_and_read_file_with_line_limit(workspace):
    path = "notes/example.txt"
    content = "first\nsecond\nthird"

    result = tools.run_write_file(path, content)

    assert result == f"Wrote {len(content.encode('utf-8'))} bytes to {path}"
    assert (workspace / path).read_text(encoding="utf-8") == content
    assert tools.run_read_file(path) == content
    assert tools.run_read_file(path, limit=2) == "first\nsecond\n... (1 more lines)"
    assert tools.run_read_file(path, limit=0) == "Error: limit must be a positive integer"

# 验证文本编辑只替换首次匹配；准备重复内容后检查替换结果，并覆盖文本不存在和旧文本为空的错误分支。
def test_edit_file_replaces_only_first_occurrence(workspace):
    target = workspace / "example.txt"
    target.write_text("old old", encoding="utf-8")

    result = tools.run_edit_file("example.txt", "old", "new")

    assert result == "Edited example.txt"
    assert target.read_text(encoding="utf-8") == "new old"
    assert tools.run_edit_file("example.txt", "missing", "new").startswith(
        "Error: text not found"
    )
    assert tools.run_edit_file("example.txt", "", "new") == (
        "Error: old_text must not be empty"
    )

# 验证文件复制、移动和删除的连续流程；依次执行三个真实文件操作并检查每一步的路径状态和内容。
def test_copy_move_and_delete_file(workspace):
    source = workspace / "source.txt"
    source.write_text("payload", encoding="utf-8")

    assert tools.run_copy_file("source.txt", "copies/copied.txt") == (
        "Copied source.txt to copies/copied.txt"
    )
    assert source.exists()
    assert (workspace / "copies" / "copied.txt").read_text(encoding="utf-8") == (
        "payload"
    )

    assert tools.run_move_file("copies/copied.txt", "archive/moved.txt") == (
        "Moved copies/copied.txt to archive/moved.txt"
    )
    assert not (workspace / "copies" / "copied.txt").exists()
    assert (workspace / "archive" / "moved.txt").exists()

    assert tools.run_delete_file("archive/moved.txt") == "Deleted archive/moved.txt"
    assert not (workspace / "archive" / "moved.txt").exists()

# 验证复制和移动不会覆盖已有目标；预先创建源文件与目标文件，再检查错误结果及双方内容保持不变。
def test_copy_and_move_refuse_to_overwrite_destination(workspace):
    (workspace / "source.txt").write_text("source", encoding="utf-8")
    (workspace / "destination.txt").write_text("destination", encoding="utf-8")

    assert tools.run_copy_file("source.txt", "destination.txt") == (
        "Error: destination already exists: destination.txt"
    )
    assert tools.run_move_file("source.txt", "destination.txt") == (
        "Error: destination already exists: destination.txt"
    )
    assert (workspace / "source.txt").exists()
    assert (workspace / "destination.txt").read_text(encoding="utf-8") == "destination"

# 验证删除工具拒绝处理目录；创建真实目录后调用删除函数，并检查错误信息和目录仍然存在。
def test_delete_file_refuses_directories(workspace):
    (workspace / "folder").mkdir()

    assert tools.run_delete_file("folder") == "Error: path is a directory: folder"
    assert (workspace / "folder").is_dir()

# 验证文件工具会返回错误字符串而不是向外抛出异常；对缺失文件执行多种操作并检查统一失败结果。
def test_file_tools_return_errors_instead_of_raising(workspace):
    assert tools.run_read_file("missing.txt").startswith("Error:")
    assert tools.run_delete_file("missing.txt").startswith("Error:")
    assert tools.run_copy_file("missing.txt", "copy.txt") == (
        "Error: source is not a file: missing.txt"
    )
    assert tools.run_move_file("missing.txt", "moved.txt") == (
        "Error: source is not a file: missing.txt"
    )

# 验证 Shell 工具会合并标准输出和错误输出；模拟子进程完成结果，并检查返回文本及完整调用参数。
def test_run_bash_combines_stdout_and_stderr(monkeypatch):
    completed = SimpleNamespace(stdout="output\n", stderr="warning\n")
    run = Mock(return_value=completed)
    monkeypatch.setattr(tools.subprocess, "run", run)

    result = tools.run_bash("example command")

    assert result == "output\nwarning"
    run.assert_called_once_with(
        "example command",
        shell=True,
        cwd=os.getcwd(),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=120,
    )

# 验证无任何子进程输出时返回占位文本；模拟空输出结果并检查工具的规范化返回值。
def test_run_bash_reports_empty_output(monkeypatch):
    monkeypatch.setattr(
        tools.subprocess,
        "run",
        Mock(return_value=SimpleNamespace(stdout="", stderr="")),
    )

    assert tools.run_bash("example command") == "(no output)"

@pytest.mark.parametrize(
    "command",
    ["sudo whoami", "shutdown now", "reboot", "rm -rf /", "echo x > /dev/null"],
)
# 验证明显危险命令会在执行前被拦截；参数化危险模式并确认子进程方法从未被调用。
def test_run_bash_blocks_dangerous_commands(monkeypatch, command):
    run = Mock()
    monkeypatch.setattr(tools.subprocess, "run", run)

    assert tools.run_bash(command) == "Error: Dangerous command blocked"
    run.assert_not_called()

# 验证 Shell 命令超时会转换为可读错误；让模拟子进程抛出超时异常并检查固定的超时提示。
def test_run_bash_reports_timeout(monkeypatch):
    run = Mock(side_effect=subprocess.TimeoutExpired(cmd="slow", timeout=120))
    monkeypatch.setattr(tools.subprocess, "run", run)

    assert tools.run_bash("slow") == "Error: Timeout (120s)"
