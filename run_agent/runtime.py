from run_agent.config import client, MODEL, SYSTEM, TOOLS, TOOL_HANDLERS

# 本文件负责驱动 Agent 对话循环，将消息发送给模型、执行模型请求的本地工具，并把执行结果写回对话历史。

# 修复字符串中的非法代理字符；先检测代理码位，仅在发现异常时通过 UTF-16 往返编码将其替换为安全字符。
def _repair_unicode(text: str) -> str:
    if not any(0xD800 <= ord(character) <= 0xDFFF for character in text):
        return text
    return text.encode("utf-16", errors="surrogatepass").decode(
        "utf-16",
        errors="replace",
    )

# 将任意消息值递归转换为可安全序列化的结构；逐层处理字符串、容器和 SDK 模型对象，最终得到基础 Python 数据。
def _make_json_safe(value):
    if isinstance(value, str):
        return _repair_unicode(value)
    if isinstance(value, dict):
        return {
            _repair_unicode(key) if isinstance(key, str) else key: _make_json_safe(item)
            for key, item in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [_make_json_safe(item) for item in value]

    model_dump = getattr(value, "model_dump", None)
    if callable(model_dump):
        return _make_json_safe(model_dump(exclude_none=True))
    return value

# 执行完整的 Agent 调用循环；反复请求模型、分发工具调用并追加工具结果，直到模型不再请求工具时结束。
def agent_loop(messages: list) -> None:
    while True:
        messages[:] = [_make_json_safe(message) for message in messages]
        response = client.messages.create(
            model=_repair_unicode(MODEL),
            system=_make_json_safe(SYSTEM),
            messages=messages,
            tools=_make_json_safe(TOOLS),
            max_tokens=8000,
        )
        messages.append({
            "role": "assistant",
            "content": _make_json_safe(response.content),
        })
        if response.stop_reason != "tool_use":
            return
        results = []
        for block in response.content:
            if block.type == "tool_use":
                print(f"\033[33m> {block.name}\033[0m")
                handler = TOOL_HANDLERS.get(block.name)
                tool_input = _make_json_safe(block.input)
                output = handler(**tool_input) if handler else f"Unknown: {block.name}"
                output = _make_json_safe(output)
                print(str(output)[:200])
                results.append({
                    "type": "tool_result",
                    "tool_use_id": _repair_unicode(block.id),
                    "content": output,
                })
        messages.append({"role": "user", "content": results})
