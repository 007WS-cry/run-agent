from run_agent import runtime

# 本文件验证 Agent 与真实文件工具之间的集成流程，通过模拟模型响应驱动写入、读取和结果回传的完整链路。

# 验证 Agent 能连续写入并读取同一文件；模拟一次包含两个工具调用的模型响应，再检查文件内容和回传消息。
def test_agent_can_write_then_read_a_file(
    workspace,
    mocked_message_create,
    response_factory,
    content_block_factory,
):
    write_block = content_block_factory(
        "tool_use",
        id="write-1",
        name="write_file",
        input={"path": "generated/result.txt", "content": "integration payload"},
    )
    read_block = content_block_factory(
        "tool_use",
        id="read-1",
        name="read_file",
        input={"path": "generated/result.txt"},
    )
    final_block = content_block_factory("text", text="File created and checked.")
    mocked_message_create.side_effect = [
        response_factory(
            content=[write_block, read_block],
            stop_reason="tool_use",
        ),
        response_factory(content=[final_block]),
    ]
    messages = [{"role": "user", "content": "Create and verify the file."}]

    runtime.agent_loop(messages)

    target = workspace / "generated" / "result.txt"
    assert target.read_text(encoding="utf-8") == "integration payload"
    assert mocked_message_create.call_count == 2
    assert messages[2] == {
        "role": "user",
        "content": [
            {
                "type": "tool_result",
                "tool_use_id": "write-1",
                "content": "Wrote 19 bytes to generated/result.txt",
            },
            {
                "type": "tool_result",
                "tool_use_id": "read-1",
                "content": "integration payload",
            },
        ],
    }
    assert messages[-1] == {
        "role": "assistant",
        "content": [{"type": "text", "text": "File created and checked."}],
    }
