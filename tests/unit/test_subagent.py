from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import Mock, call

from run_agent import subagent
from run_agent.tools import tools_config

# 本文件对 Subagent 委派功能进行单元测试，通过模拟模型响应验证工具限制、消息协议、权限钩子和轮数上限。


# 验证 task 工具具有完整参数约束且 Subagent 只继承基础工具，同时延迟处理器能够正确调用委派入口。
def test_task_registration_and_subtool_scope(monkeypatch):
    task_schema = next(tool for tool in tools_config.TOOLS if tool["name"] == "task")

    assert task_schema["description"]
    assert task_schema["input_schema"]["properties"]["description"]["minLength"] == 1
    assert task_schema["input_schema"]["additionalProperties"] is False
    assert {tool["name"] for tool in tools_config.SUBTOOLS} == set(
        tools_config.SUBTOOL_NAMES
    )
    assert {"task", "todo_write", "load_skill"}.isdisjoint(
        tool["name"] for tool in tools_config.SUBTOOLS
    )

    spawn = Mock(return_value="delegated")
    monkeypatch.setattr(subagent, "spawn_subagent", spawn)

    assert tools_config.TOOL_HANDLERS["task"]("inspect the project") == "delegated"
    spawn.assert_called_once_with("inspect the project")


# 验证 Subagent 执行同一响应中的多个工具后只追加一条结果消息，并把最终文本总结返回给主 Agent。
def test_spawn_subagent_groups_tool_results(
    monkeypatch,
    response_factory,
    content_block_factory,
    capsys,
):
    first_handler = Mock(return_value="first output")
    second_handler = Mock(return_value="second output")
    monkeypatch.setattr(
        subagent,
        "SUBTOOL_HANDLERS",
        {"first_tool": first_handler, "second_tool": second_handler},
    )
    hooks = Mock(return_value=None)
    monkeypatch.setattr(subagent, "trigger_hooks", hooks)
    first_block = content_block_factory(
        "tool_use",
        id="tool-1",
        name="first_tool",
        input={"value": "one"},
    )
    second_block = content_block_factory(
        "tool_use",
        id="tool-2",
        name="second_tool",
        input={"value": "two"},
    )
    responses = [
        response_factory(content=[first_block, second_block], stop_reason="tool_use"),
        response_factory(content=[content_block_factory("text", text="finished")]),
    ]
    request_messages = []

    # 保存每次模型请求发出时的消息快照，避免 Mock 调用参数随后被原列表的追加操作改变。
    def create(**kwargs):
        request_messages.append(deepcopy(kwargs["messages"]))
        return responses.pop(0)

    monkeypatch.setattr(
        subagent,
        "client",
        SimpleNamespace(messages=SimpleNamespace(create=create)),
    )

    result = subagent.spawn_subagent("run both tools")

    assert result == "finished"
    first_handler.assert_called_once_with(value="one")
    second_handler.assert_called_once_with(value="two")
    assert request_messages[1][2] == {
        "role": "user",
        "content": [
            {
                "type": "tool_result",
                "tool_use_id": "tool-1",
                "content": "first output",
            },
            {
                "type": "tool_result",
                "tool_use_id": "tool-2",
                "content": "second output",
            },
        ],
    }
    hooks.assert_has_calls([
        call("PreToolUse", first_block),
        call("PostToolUse", first_block, "first output"),
        call("PreToolUse", second_block),
        call("PostToolUse", second_block, "second output"),
    ])
    assert "[sub] first_tool: first output" in capsys.readouterr().out


# 验证权限钩子拒绝 Subagent 工具后不会执行处理器或后置钩子，并会把拒绝原因返回给下一次模型请求。
def test_spawn_subagent_returns_denial_without_executing_handler(
    monkeypatch,
    response_factory,
    content_block_factory,
):
    handler = Mock()
    monkeypatch.setattr(subagent, "SUBTOOL_HANDLERS", {"bash": handler})
    blocked_tool = content_block_factory(
        "tool_use",
        id="blocked-1",
        name="bash",
        input={"command": "sudo whoami"},
    )
    responses = [
        response_factory(content=[blocked_tool], stop_reason="tool_use"),
        response_factory(content=[content_block_factory("text", text="stopped")]),
    ]
    request_messages = []

    # 仅在前置权限检查时返回拒绝原因，若错误触发后置钩子则让测试立即失败。
    def trigger(event, *args):
        if event == "PreToolUse":
            return "Permission denied by deny list"
        raise AssertionError("PostToolUse must not run for a blocked tool")

    # 记录被拒绝后的下一次模型请求，确认工具结果消息仍符合 Anthropic 消息协议。
    def create(**kwargs):
        request_messages.append(deepcopy(kwargs["messages"]))
        return responses.pop(0)

    monkeypatch.setattr(subagent, "trigger_hooks", trigger)
    monkeypatch.setattr(
        subagent,
        "client",
        SimpleNamespace(messages=SimpleNamespace(create=create)),
    )

    assert subagent.spawn_subagent("run a blocked command") == "stopped"
    handler.assert_not_called()
    assert request_messages[1][2]["content"] == [{
        "type": "tool_result",
        "tool_use_id": "blocked-1",
        "content": "Permission denied by deny list",
    }]


# 验证 Subagent 连续调用工具且始终不结束时会遵守轮数上限，返回错误而不是进入无限循环。
def test_spawn_subagent_stops_at_round_limit(
    monkeypatch,
    response_factory,
    content_block_factory,
):
    monkeypatch.setattr(subagent, "MAX_SUBAGENT_ROUNDS", 2)
    monkeypatch.setattr(subagent, "trigger_hooks", Mock(return_value=None))
    tool_block = content_block_factory(
        "tool_use",
        id="unknown-1",
        name="unknown_tool",
        input={},
    )
    create = Mock(
        return_value=response_factory(content=[tool_block], stop_reason="tool_use")
    )
    monkeypatch.setattr(
        subagent,
        "client",
        SimpleNamespace(messages=SimpleNamespace(create=create)),
    )

    result = subagent.spawn_subagent("never finish")

    assert result == (
        "Error: Subagent reached the maximum of 2 rounds without a final response."
    )
    assert create.call_count == 2
