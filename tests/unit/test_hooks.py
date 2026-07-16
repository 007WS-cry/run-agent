from types import SimpleNamespace
from unittest.mock import Mock

from run_agent import hooks
from run_agent import runtime

# 本文件对生命周期钩子进行单元测试，验证命令拦截、人工确认、日志告警、上下文提示及运行时阻断流程。

# 构造带有工具名称和输入参数的最小模拟内容块，供各项钩子测试复用。
def make_tool_block(name, **tool_input):
    return SimpleNamespace(name=name, input=tool_input)


# 验证硬禁止列表中的命令会立即被拒绝，且整个过程不会弹出人工确认提示。
def test_permission_hook_blocks_deny_list_without_prompt(monkeypatch, capsys):
    def prompt(_):
        raise AssertionError("must not prompt")

    monkeypatch.setattr("builtins.input", prompt)

    result = hooks.permission_hook(make_tool_block("bash", command="sudo whoami"))

    assert result == "Permission denied by deny list"
    assert "sudo" in capsys.readouterr().out


# 验证破坏性命令必须经过用户确认：普通回答拒绝执行，明确输入 yes 后才会放行。
def test_permission_hook_requires_confirmation_for_destructive_command(
    monkeypatch,
):
    block = make_tool_block("bash", command="rm example.txt")
    monkeypatch.setattr("builtins.input", lambda _: "n")
    assert hooks.permission_hook(block) == "Permission denied by user"

    monkeypatch.setattr("builtins.input", lambda _: "YES")
    assert hooks.permission_hook(block) is None


# 验证安全 Shell 命令和非 Shell 工具不会被权限钩子误拦截。
def test_permission_hook_allows_safe_and_non_shell_tools():
    assert hooks.permission_hook(make_tool_block("bash", command="pwd")) is None
    assert hooks.permission_hook(make_tool_block("read_file", path="README.md")) is None


# 验证日志钩子会打印工具名称以及输入参数的简短预览。
def test_log_hook_prints_tool_and_argument_preview(capsys):
    block = make_tool_block("read_file", path="README.md", limit=10)

    assert hooks.log_hook(block) is None

    output = capsys.readouterr().out
    assert "> read_file" in output
    assert "README.md" in output


# 验证输出长度等于阈值时保持安静，只有真正超过阈值后才打印大输出告警。
def test_large_output_hook_only_warns_above_threshold(monkeypatch, capsys):
    monkeypatch.setattr(hooks, "LARGE_OUTPUT_THRESHOLD", 3)
    block = make_tool_block("demo")

    assert hooks.large_output_hook(block, "123") is None
    assert capsys.readouterr().out == ""

    assert hooks.large_output_hook(block, "1234") is None
    assert "Large output from demo: 4 chars" in capsys.readouterr().out


# 验证用户提交钩子会在终端提示当前工作目录。
def test_context_hook_reports_workspace(capsys):
    assert hooks.context_inject_hook("hello") is None
    assert str(hooks.WORKDIR) in capsys.readouterr().out


# 验证停止钩子只统计消息历史中的工具结果块，不会把普通文本内容计入调用次数。
def test_summary_hook_counts_tool_results(capsys):
    messages = [
        {"role": "user", "content": "run a tool"},
        {
            "role": "user",
            "content": [
                {"type": "tool_result", "content": "one"},
                {"type": "tool_result", "content": "two"},
            ],
        },
        {"role": "assistant", "content": [{"type": "text", "text": "done"}]},
    ]

    assert hooks.summary_hook(messages) is None
    assert "session used 2 tool calls" in capsys.readouterr().out


# 验证运行时收到权限拒绝结果后会把原因反馈给模型，并确保对应工具处理器从未执行。
def test_agent_loop_returns_denial_without_executing_handler(
    monkeypatch,
    mocked_message_create,
    response_factory,
    content_block_factory,
):
    handler = Mock()
    monkeypatch.setattr(runtime, "TOOL_HANDLERS", {"bash": handler})

    def trigger(event, *args):
        if event == "PreToolUse":
            return "Permission denied by deny list"
        return None

    monkeypatch.setattr(runtime, "trigger_hooks", trigger)
    tool_block = content_block_factory(
        "tool_use",
        id="blocked-1",
        name="bash",
        input={"command": "sudo whoami"},
    )
    mocked_message_create.side_effect = [
        response_factory(content=[tool_block], stop_reason="tool_use"),
        response_factory(content=[]),
    ]
    messages = [{"role": "user", "content": "run it"}]

    runtime.agent_loop(messages)

    handler.assert_not_called()
    assert messages[2]["content"] == [
        {
            "type": "tool_result",
            "tool_use_id": "blocked-1",
            "content": "Permission denied by deny list",
        }
    ]
