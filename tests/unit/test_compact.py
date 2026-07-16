from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from run_agent import compact, runtime

# 本文件对上下文压缩功能进行单元测试，覆盖消息边界、工具结果持久化、转录、摘要和溢出重试流程。


# 验证裁剪中间消息时会同时保留工具调用及其结果，并用明确占位消息记录被移除的数量。
def test_snip_compact_preserves_tool_message_pairs():
    messages = [
        {"role": "user", "content": "start"},
        {"role": "assistant", "content": [{"type": "tool_use", "id": "old"}]},
        {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "old"}]},
        {"role": "assistant", "content": "middle answer"},
        {"role": "user", "content": "middle request"},
        {"role": "assistant", "content": [{"type": "tool_use", "id": "new"}]},
        {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "new"}]},
        {"role": "assistant", "content": "latest answer"},
    ]

    compacted = compact.snip_compact(messages, max_messages=6)

    assert compacted[:3] == messages[:3]
    assert compacted[3] == {"role": "user", "content": "[snipped 2 messages]"}
    assert compacted[4:] == messages[5:]


# 验证非法的最大消息数会被明确拒绝，避免极小窗口下生成不可预测的裁剪边界。
def test_snip_compact_rejects_too_small_limit():
    with pytest.raises(ValueError, match="at least 3"):
        compact.snip_compact([{"role": "user", "content": "hello"}], 2)


# 验证微压缩只替换较早且较长的工具结果，最近三条结果仍保持完整内容。
def test_micro_compact_keeps_recent_tool_results():
    messages = [
        {
            "role": "user",
            "content": [
                {
                    "type": "tool_result",
                    "tool_use_id": f"tool-{index}",
                    "content": str(index) * 130,
                }
            ],
        }
        for index in range(5)
    ]

    result = compact.micro_compact(messages)
    tool_results = compact.collect_tool_results(result)

    assert tool_results[0][2]["content"] == compact.COMPACTED_TOOL_RESULT
    assert tool_results[1][2]["content"] == compact.COMPACTED_TOOL_RESULT
    assert tool_results[2][2]["content"] == "2" * 130
    assert tool_results[-1][2]["content"] == "4" * 130


# 验证超长工具输出会以 UTF-8 写入受控目录，恶意工具编号不会逃逸目录且返回内容包含预览。
def test_persist_large_output_uses_safe_filename(tmp_path, monkeypatch):
    monkeypatch.setattr(compact, "TOOL_RESULTS_DIR", tmp_path)
    monkeypatch.setattr(compact, "PERSIST_THRESHOLD", 5)
    output = "中文abcdef"

    result = compact.persist_large_output("../../unsafe", output)

    saved_files = list(tmp_path.iterdir())
    assert len(saved_files) == 1
    assert saved_files[0].parent == tmp_path
    assert ".." not in saved_files[0].name
    assert saved_files[0].read_text(encoding="utf-8") == output
    assert "Preview:\n中文abcdef" in result


# 验证只读目录导致工具输出写入失败时会返回原始内容，不让持久化辅助逻辑中断主流程。
def test_persist_large_output_falls_back_when_directory_is_read_only(
    tmp_path,
    monkeypatch,
):
    monkeypatch.setattr(compact, "TOOL_RESULTS_DIR", tmp_path)
    monkeypatch.setattr(compact, "PERSIST_THRESHOLD", 1)
    monkeypatch.setattr(
        type(tmp_path),
        "write_text",
        Mock(side_effect=OSError("read-only filesystem")),
    )

    assert compact.persist_large_output("tool", "complete output") == "complete output"


# 验证当前工具结果总量超过预算时会优先持久化较大内容，并在消息中保留完整文件路径。
def test_tool_result_budget_persists_large_blocks(tmp_path, monkeypatch):
    monkeypatch.setattr(compact, "TOOL_RESULTS_DIR", tmp_path)
    messages = [
        {
            "role": "user",
            "content": [
                {"type": "tool_result", "tool_use_id": "one", "content": "a" * 400},
                {"type": "tool_result", "tool_use_id": "two", "content": "b" * 400},
            ],
        }
    ]

    result = compact.tool_result_budget(messages, max_bytes=500)

    contents = [block["content"] for block in result[-1]["content"]]
    assert any("<persisted-output>" in content for content in contents)
    assert list(tmp_path.glob("*.txt"))


# 验证同一时间戳下的转录文件仍保持唯一，并以可读 UTF-8 JSONL 保存中文消息。
def test_write_transcript_creates_unique_utf8_files(tmp_path, monkeypatch):
    monkeypatch.setattr(compact, "TRANSCRIPT_DIR", tmp_path)
    monkeypatch.setattr(compact.time, "time_ns", lambda: 123)
    messages = [{"role": "user", "content": "中文内容"}]

    first_path = compact.write_transcript(messages)
    second_path = compact.write_transcript(messages)

    assert first_path != second_path
    assert "中文内容" in first_path.read_text(encoding="utf-8")
    assert second_path.exists()


# 验证转录文件无法创建时返回 None，使后续历史摘要仍可以继续执行。
def test_write_transcript_returns_none_when_directory_is_read_only(
    tmp_path,
    monkeypatch,
):
    monkeypatch.setattr(compact, "TRANSCRIPT_DIR", tmp_path)
    monkeypatch.setattr(
        type(tmp_path),
        "open",
        Mock(side_effect=OSError("read-only filesystem")),
    )

    assert compact.write_transcript([{"role": "user", "content": "hello"}]) is None


# 验证历史摘要请求使用配置模型，并能从 SDK 文本块中合并得到最终摘要内容。
def test_summarize_history_returns_model_text(monkeypatch):
    create = Mock(
        return_value=SimpleNamespace(
            content=[SimpleNamespace(type="text", text="保留关键结论")]
        )
    )
    fake_client = SimpleNamespace(messages=SimpleNamespace(create=create))
    monkeypatch.setattr(compact, "client", fake_client)
    monkeypatch.setattr(compact, "MODEL", "summary-model")

    result = compact.summarize_history([{"role": "user", "content": "总结"}])

    assert result == "保留关键结论"
    assert create.call_args.kwargs["model"] == "summary-model"
    assert create.call_args.kwargs["max_tokens"] == 2_000


# 验证摘要输入超限时从中间截断，同时保留对话起点和最新进展以免摘要遗漏当前任务。
def test_summarize_history_keeps_head_and_tail_when_input_is_too_large(monkeypatch):
    create = Mock(return_value=SimpleNamespace(content=[]))
    fake_client = SimpleNamespace(messages=SimpleNamespace(create=create))
    monkeypatch.setattr(compact, "client", fake_client)
    monkeypatch.setattr(compact, "SUMMARY_INPUT_LIMIT", 200)
    messages = [
        {"role": "user", "content": "START-" + "a" * 300},
        {"role": "assistant", "content": "b" * 300 + "-LATEST"},
    ]

    compact.summarize_history(messages)

    prompt = create.call_args.kwargs["messages"][0]["content"]
    assert "START-" in prompt
    assert "-LATEST" in prompt
    assert "middle of conversation omitted" in prompt


# 验证响应式压缩会保留位于尾部边界上的完整工具调用消息对，而不是只留下孤立工具结果。
def test_reactive_compact_preserves_tail_tool_pair(monkeypatch):
    messages = [
        {"role": "user", "content": "old"},
        {"role": "assistant", "content": [{"type": "tool_use", "id": "pair"}]},
        {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "pair"}]},
        {"role": "assistant", "content": "a"},
        {"role": "user", "content": "b"},
        {"role": "assistant", "content": "c"},
        {"role": "user", "content": "d"},
    ]
    monkeypatch.setattr(compact, "write_transcript", Mock(return_value="history.jsonl"))
    monkeypatch.setattr(compact, "summarize_history", Mock(return_value="摘要"))

    result = compact.reactive_compact(messages)

    assert result[0]["content"] == "[Reactive compact]\n\n摘要"
    assert result[1:] == messages[1:]


# 验证请求前整理在估算规模超过阈值时会进入主动历史压缩，并返回摘要替换后的消息列表。
def test_prepare_history_compacts_messages_above_limit(monkeypatch):
    messages = [{"role": "user", "content": "x" * 200}]
    expected = [{"role": "user", "content": "[Compacted]\n\n摘要"}]
    compact_history = Mock(return_value=expected)
    monkeypatch.setattr(compact, "CONTEXT_LIMIT", 10)
    monkeypatch.setattr(compact, "compact_history", compact_history)

    result = compact.prepare_history(messages)

    compact_history.assert_called_once_with(messages)
    assert result == expected


# 验证请求出现明确的上下文溢出时运行时只压缩并重试一次，成功后把最终回复写回压缩后的历史。
def test_agent_loop_retries_after_context_overflow(
    monkeypatch,
    mocked_message_create,
    response_factory,
    content_block_factory,
):
    compacted_messages = [{"role": "user", "content": "[Reactive compact]\n\n摘要"}]
    reactive = Mock(return_value=compacted_messages)
    monkeypatch.setattr(compact, "prepare_history", lambda messages: messages)
    monkeypatch.setattr(compact, "reactive_compact", reactive)
    final_block = content_block_factory("text", text="done")
    mocked_message_create.side_effect = [
        RuntimeError("prompt is too long"),
        response_factory(content=[final_block]),
    ]
    messages = [{"role": "user", "content": "very long request"}]

    runtime.agent_loop(messages)

    reactive.assert_called_once()
    assert mocked_message_create.call_count == 2
    assert messages == [
        *compacted_messages,
        {"role": "assistant", "content": [{"type": "text", "text": "done"}]},
    ]


# 验证运行时会在工具执行后立即持久化超长结果，并把紧凑标记而不是完整正文回传给模型。
def test_agent_loop_persists_large_tool_output(
    tmp_path,
    monkeypatch,
    mocked_message_create,
    response_factory,
    content_block_factory,
):
    handler = Mock(return_value="large tool output")
    monkeypatch.setattr(runtime, "TOOL_HANDLERS", {"demo_tool": handler})
    monkeypatch.setattr(compact, "TOOL_RESULTS_DIR", tmp_path)
    monkeypatch.setattr(compact, "PERSIST_THRESHOLD", 5)
    tool_block = content_block_factory(
        "tool_use",
        id="large-1",
        name="demo_tool",
        input={},
    )
    mocked_message_create.side_effect = [
        response_factory(content=[tool_block], stop_reason="tool_use"),
        response_factory(content=[]),
    ]
    messages = [{"role": "user", "content": "run tool"}]

    runtime.agent_loop(messages)

    tool_result = messages[2]["content"][0]["content"]
    assert "<persisted-output>" in tool_result
    assert "Preview:\nlarge tool output" in tool_result
    assert next(tmp_path.glob("*.txt")).read_text(encoding="utf-8") == (
        "large tool output"
    )


# 验证非上下文类接口异常不会被误判为可重试错误，避免隐藏鉴权或网络故障。
def test_agent_loop_reraises_unrelated_api_errors(
    monkeypatch,
    mocked_message_create,
):
    monkeypatch.setattr(compact, "prepare_history", lambda messages: messages)
    mocked_message_create.side_effect = RuntimeError("authentication failed")

    with pytest.raises(RuntimeError, match="authentication failed"):
        runtime.agent_loop([{"role": "user", "content": "hello"}])
