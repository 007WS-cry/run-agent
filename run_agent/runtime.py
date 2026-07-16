from run_agent.config import client, MODEL, SYSTEM, TOOLS, TOOL_HANDLERS


def _repair_unicode(text: str) -> str:
    if not any(0xD800 <= ord(character) <= 0xDFFF for character in text):
        return text
    return text.encode("utf-16", errors="surrogatepass").decode(
        "utf-16",
        errors="replace",
    )


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
