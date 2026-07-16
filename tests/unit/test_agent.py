from unittest.mock import Mock

from run_agent import runtime

# 本文件对 Agent 运行时进行单元测试，通过模拟内容块、模型响应和工具处理器验证数据清理与调用循环的各个分支。

# 验证合法 Unicode 文本保持不变；直接调用修复函数并比较输入输出，确认正常字符不会被重复编码。
def test_repair_unicode_preserves_valid_text():
    assert runtime._repair_unicode("hello, 世界") == "hello, 世界"

# 验证未配对代理字符会被替换；构造包含非法码位的字符串，检查 UTF-16 往返处理后的安全结果。
def test_repair_unicode_replaces_unpaired_surrogate():
    assert runtime._repair_unicode("before\ud800after") == "before�after"

# 验证嵌套 SDK 对象可以安全序列化；组合字典、元组和模拟内容块，检查递归转换及空值过滤结果。
def test_make_json_safe_handles_nested_sdk_objects(content_block_factory):
    value = {
        "items": [
            content_block_factory("text", text="ok", optional=None),
            ("before\ud800after",),
        ]
    }

    assert runtime._make_json_safe(value) == {
        "items": [
            {"type": "text", "text": "ok"},
            ["before�after"],
        ]
    }

# 验证模型给出最终回复时循环立即结束；固定运行参数并模拟普通响应，再检查请求参数和追加的助手消息。
def test_agent_loop_stops_after_final_response(
    monkeypatch,
    mocked_message_create,
    response_factory,
    content_block_factory,
):
    monkeypatch.setattr(runtime, "MODEL", "unit-test-model")
    monkeypatch.setattr(runtime, "SYSTEM", "unit-test-system")
    monkeypatch.setattr(runtime, "TOOLS", [{"name": "test-tool"}])
    final_block = content_block_factory("text", text="done")
    mocked_message_create.return_value = response_factory(content=[final_block])
    messages = [{"role": "user", "content": "hello"}]

    runtime.agent_loop(messages)

    assert messages == [
        {"role": "user", "content": "hello"},
        {"role": "assistant", "content": [{"type": "text", "text": "done"}]},
    ]
    mocked_message_create.assert_called_once_with(
        model="unit-test-model",
        system="unit-test-system",
        messages=messages,
        tools=[{"name": "test-tool"}],
        max_tokens=8000,
    )

# 验证 Agent 会执行工具并把结果交回模型；模拟工具调用和最终回复两次响应，检查处理器参数、消息顺序和终端输出。
def test_agent_loop_executes_tool_and_returns_result_to_model(
    monkeypatch,
    mocked_message_create,
    response_factory,
    content_block_factory,
    capsys,
):
    handler = Mock(return_value="tool output")
    monkeypatch.setattr(runtime, "TOOL_HANDLERS", {"demo_tool": handler})
    tool_block = content_block_factory(
        "tool_use",
        id="tool-1",
        name="demo_tool",
        input={"value": "input"},
    )
    final_block = content_block_factory("text", text="finished")
    mocked_message_create.side_effect = [
        response_factory(content=[tool_block], stop_reason="tool_use"),
        response_factory(content=[final_block]),
    ]
    messages = [{"role": "user", "content": "use the tool"}]

    runtime.agent_loop(messages)

    handler.assert_called_once_with(value="input")
    assert mocked_message_create.call_count == 2
    assert messages[1] == {
        "role": "assistant",
        "content": [
            {
                "type": "tool_use",
                "id": "tool-1",
                "name": "demo_tool",
                "input": {"value": "input"},
            }
        ],
    }
    assert messages[2] == {
        "role": "user",
        "content": [
            {
                "type": "tool_result",
                "tool_use_id": "tool-1",
                "content": "tool output",
            }
        ],
    }
    assert messages[3] == {
        "role": "assistant",
        "content": [{"type": "text", "text": "finished"}],
    }
    output = capsys.readouterr().out
    assert "> demo_tool" in output
    assert "tool output" in output

# 验证未知工具会生成明确错误结果；模拟不存在的工具名称，并检查 Agent 写回模型的工具结果内容。
def test_agent_loop_returns_unknown_tool_error(
    mocked_message_create,
    response_factory,
    content_block_factory,
):
    tool_block = content_block_factory(
        "tool_use",
        id="unknown-1",
        name="missing_tool",
        input={},
    )
    mocked_message_create.side_effect = [
        response_factory(content=[tool_block], stop_reason="tool_use"),
        response_factory(content=[]),
    ]
    messages = [{"role": "user", "content": "call an unknown tool"}]

    runtime.agent_loop(messages)

    assert messages[2]["content"] == [
        {
            "type": "tool_result",
            "tool_use_id": "unknown-1",
            "content": "Unknown: missing_tool",
        }
    ]
