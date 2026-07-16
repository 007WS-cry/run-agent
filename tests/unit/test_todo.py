from unittest.mock import Mock

import pytest

from run_agent import config, runtime, tools

# 本文件对 TODO 工具及其运行时提醒机制进行单元测试，覆盖配置契约、状态更新、输入校验和计数器重置流程。


# 在每项测试前清空内存任务清单和提醒计数器，避免全局状态在测试之间相互影响。
@pytest.fixture(autouse=True)
def reset_todo_state(monkeypatch):
    monkeypatch.setattr(tools, "CURRENT_TODOS", [])
    monkeypatch.setattr(runtime, "rounds_since_todo", 0)


# 验证 TODO 工具已经注册处理器，并通过 JSON Schema 要求完整清单及每个任务的内容和状态。
def test_todo_tool_schema_and_handler_are_registered():
    todo_tool = next(tool for tool in config.TOOLS if tool["name"] == "todo_write")
    schema = todo_tool["input_schema"]
    item_schema = schema["properties"]["todos"]["items"]

    assert config.TOOL_HANDLERS["todo_write"] is tools.run_todo_write
    assert schema["required"] == ["todos"]
    assert schema["additionalProperties"] is False
    assert item_schema["required"] == ["content", "status"]
    assert item_schema["properties"]["status"]["enum"] == [
        "pending",
        "in_progress",
        "completed",
    ]


# 验证有效任务会整体替换当前清单，并按照三种状态打印对应的终端图标。
def test_todo_write_replaces_state_and_prints_tasks(capsys):
    todos = [
        {"content": "分析需求", "status": "pending"},
        {"content": "编写实现", "status": "in_progress"},
        {"content": "运行测试", "status": "completed"},
    ]

    result = tools.run_todo_write(todos)

    assert result == "Updated 3 tasks"
    assert tools.CURRENT_TODOS == todos
    assert tools.CURRENT_TODOS is not todos
    output = capsys.readouterr().out
    assert "[ ] 分析需求" in output
    assert "[▸] 编写实现" in output
    assert "[✓] 运行测试" in output


@pytest.mark.parametrize(
    ("todos", "message"),
    [
        (None, "todos must be a list"),
        (["not-an-object"], "must be an object"),
        ([{"content": "", "status": "pending"}], "non-empty content"),
        ([{"content": "任务", "status": "unknown"}], "invalid status"),
        ([{"content": "任务", "status": []}], "invalid status"),
    ],
)
# 验证异常输入会返回统一错误文本，并保留此前已经成功写入的任务清单。
def test_todo_write_rejects_invalid_input_without_changing_state(todos, message):
    previous = [{"content": "保留任务", "status": "pending"}]
    tools.CURRENT_TODOS = previous

    result = tools.run_todo_write(todos)

    assert result.startswith("Error:")
    assert message in result
    assert tools.CURRENT_TODOS is previous


# 验证连续三个未更新 TODO 的工具调用轮次后，运行时会在下一次模型请求前插入提醒消息。
def test_agent_loop_injects_todo_reminder_after_three_tool_rounds(
    monkeypatch,
    mocked_message_create,
    response_factory,
    content_block_factory,
):
    handler = Mock(return_value="file content")
    monkeypatch.setattr(runtime, "TOOL_HANDLERS", {"read_file": handler})
    runtime.rounds_since_todo = 2
    tool_block = content_block_factory(
        "tool_use",
        id="read-1",
        name="read_file",
        input={"path": "README.md"},
    )
    mocked_message_create.side_effect = [
        response_factory(content=[tool_block], stop_reason="tool_use"),
        response_factory(content=[]),
    ]
    messages = [{"role": "user", "content": "读取文件"}]

    runtime.agent_loop(messages)

    handler.assert_called_once_with(path="README.md")
    reminder = {
        "role": "user",
        "content": "<reminder>Update your todos.</reminder>",
    }
    assert reminder in messages
    assert runtime.rounds_since_todo == 0


# 验证有效的 todo_write 调用会清零累计轮次，从而避免紧接着插入不必要的更新提醒。
def test_successful_todo_write_resets_reminder_counter(
    monkeypatch,
    mocked_message_create,
    response_factory,
    content_block_factory,
):
    monkeypatch.setattr(
        runtime,
        "TOOL_HANDLERS",
        {"todo_write": tools.run_todo_write},
    )
    runtime.rounds_since_todo = 2
    tool_block = content_block_factory(
        "tool_use",
        id="todo-1",
        name="todo_write",
        input={"todos": [{"content": "补充测试", "status": "in_progress"}]},
    )
    mocked_message_create.side_effect = [
        response_factory(content=[tool_block], stop_reason="tool_use"),
        response_factory(content=[]),
    ]
    messages = [{"role": "user", "content": "更新任务"}]

    runtime.agent_loop(messages)

    assert runtime.rounds_since_todo == 0
    assert tools.CURRENT_TODOS == [
        {"content": "补充测试", "status": "in_progress"}
    ]
    assert not any(
        message.get("content") == "<reminder>Update your todos.</reminder>"
        for message in messages
    )
