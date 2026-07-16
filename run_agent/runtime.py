from run_agent.compact import prepare_history, reactive_compact, persist_large_output
from run_agent.config import MODEL, client
from run_agent.hooks.hooks import trigger_hooks
from run_agent.prompt import build_system
from run_agent.tools.tools_config import TOOL_HANDLERS, TOOLS

# 本文件负责驱动 Agent 对话循环，将消息发送给模型、执行本地工具、压缩上下文，并把结果写回对话历史。

# 在运行时模块加载时构建系统提示词，使配置模块不再反向依赖提示词和工具模块。
SYSTEM = build_system()

# 记录最近一次有效 TODO 更新之后经历的工具调用轮数，用于定期提醒模型维护任务清单。
rounds_since_todo = 0

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


# 判断接口异常是否表示上下文长度溢出；同时检查异常文本和 SDK 提供的结构化响应体。
def _is_context_overflow_error(error: Exception) -> bool:
    error_text = f"{error} {getattr(error, 'body', '')}".casefold()
    markers = (
        "context window",
        "context_length_exceeded",
        "maximum context length",
        "prompt is too long",
        "too many tokens",
    )
    return any(marker in error_text for marker in markers)


# 执行完整的 Agent 调用循环；反复请求模型、分发工具调用并追加工具结果，直到模型不再请求工具时结束。
def agent_loop(messages: list) -> None:
    global rounds_since_todo

    # 每个成功的模型响应都会恢复一次响应式压缩机会，避免同一次溢出在重试失败后进入无限循环。
    reactive_compaction_used = False
    while True:
        # 连续三个工具调用轮次没有更新 TODO 时插入提醒，并重新开始计算下一次提醒间隔。
        if rounds_since_todo >= 3 and messages:
            messages.append({
                "role": "user",
                "content": "<reminder>Update your todos.</reminder>",
            })
            rounds_since_todo = 0
        messages[:] = [_make_json_safe(message) for message in messages]
        messages[:] = prepare_history(messages)
        try:
            response = client.messages.create(
                model=_repair_unicode(MODEL),
                system=_make_json_safe(SYSTEM),
                messages=messages,
                tools=_make_json_safe(TOOLS),
                max_tokens=8000,
            )
        except Exception as error:
            # 只对明确的上下文溢出执行一次响应式压缩；其他接口异常保持原样交给调用方处理。
            if reactive_compaction_used or not _is_context_overflow_error(error):
                raise
            messages[:] = reactive_compact(messages)
            reactive_compaction_used = True
            continue
        reactive_compaction_used = False
        messages.append({
            "role": "assistant",
            "content": _make_json_safe(response.content),
        })
        if response.stop_reason != "tool_use":
            # 最终回答产生后触发停止钩子；钩子返回补充消息时继续下一轮，否则结束本次 Agent 循环。
            force = trigger_hooks("Stop", messages)
            if force:
                messages.append({"role": "user", "content": str(force)})
                continue
            return
        # 每次模型返回工具调用都视为一个执行轮次；有效的 todo_write 会在执行后把计数器清零。
        rounds_since_todo += 1
        results = []
        for block in response.content:
            if block.type != "tool_use":
                continue
            # 工具执行前先运行权限与日志钩子；非空结果表示本次调用被拦截，并作为工具结果反馈给模型。
            blocked = trigger_hooks("PreToolUse", block)
            if blocked:
                results.append({
                    "type": "tool_result",
                    "tool_use_id": _repair_unicode(block.id),
                    "content": str(blocked),
                })
                continue
            handler = TOOL_HANDLERS.get(block.name)
            tool_input = _make_json_safe(block.input)
            output = handler(**tool_input) if handler else f"Unknown: {block.name}"
            output = _make_json_safe(output)
            # 工具执行成功后先用原始结果触发后置钩子，再把超长文本持久化为文件以控制消息体积。
            trigger_hooks("PostToolUse", block, output)
            if isinstance(output, str):
                output = persist_large_output(block.id, output)
            # 只有成功更新任务清单才重置提醒计数，错误结果仍保留已经累计的轮次。
            if block.name == "todo_write" and not str(output).startswith("Error:"):
                rounds_since_todo = 0
            print(str(output)[:200])
            results.append({
                "type": "tool_result",
                "tool_use_id": _repair_unicode(block.id),
                "content": output,
            })
        messages.append({"role": "user", "content": results})
