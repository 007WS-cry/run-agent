import hashlib
import json
import re
import time
from pathlib import Path

from run_agent.config import MODEL, TOOL_RESULTS_DIR, TRANSCRIPT_DIR, client
from run_agent.content import extract_text

# 本文件负责控制消息历史规模，包括裁剪旧消息、压缩工具结果、持久化大输出、保存转录和生成历史摘要。

# 消息历史超过该估算字符数时主动请求模型生成摘要，以降低下一次主请求的上下文规模。
CONTEXT_LIMIT = 50_000

# 微压缩始终保留最近若干条工具结果的完整内容，避免影响当前步骤的连续执行。
KEEP_RECENT = 3

# 单条工具输出超过该字符数时写入磁盘，仅把路径和预览保留在消息历史中。
PERSIST_THRESHOLD = 30_000

# 工具结果被微压缩后使用的统一占位文本，提示模型必要时重新执行对应操作。
COMPACTED_TOOL_RESULT = "[Earlier tool result compacted. Re-run if needed.]"

# 历史摘要请求最多携带的序列化字符数，超出时同时保留对话开头和最新上下文。
SUMMARY_INPUT_LIMIT = 80_000


# 将消息序列化后的字符数作为轻量规模估算，供主动压缩阈值判断使用。
def estimate_size(messages: list[dict]) -> int:
    return len(json.dumps(messages, ensure_ascii=False, default=str))


# 兼容字典和 Anthropic SDK 对象两种内容块形式，返回内容块的类型名称。
def _block_type(block):
    return block.get("type") if isinstance(block, dict) else getattr(block, "type", None)


# 判断助手消息中是否包含工具调用块，供裁剪边界保护工具调用与结果的相邻关系。
def _message_has_tool_use(message: dict) -> bool:
    if message.get("role") != "assistant":
        return False
    content = message.get("content")
    if not isinstance(content, list):
        return False
    return any(_block_type(block) == "tool_use" for block in content)


# 判断用户消息中是否包含工具结果块，供裁剪和响应式压缩识别不可拆分的工具消息对。
def _is_tool_result_message(message: dict) -> bool:
    if message.get("role") != "user":
        return False
    content = message.get("content")
    if not isinstance(content, list):
        return False
    return any(
        isinstance(block, dict) and block.get("type") == "tool_result"
        for block in content
    )


# 裁剪过长消息列表的中间部分；调整首尾边界，避免拆开相邻的工具调用消息和工具结果消息。
def snip_compact(messages: list[dict], max_messages: int = 50) -> list[dict]:
    if max_messages < 3:
        raise ValueError("max_messages must be at least 3")
    if len(messages) <= max_messages:
        return messages

    keep_head = min(3, max_messages - 1)
    keep_tail = max_messages - keep_head - 1
    head_end = keep_head
    tail_start = len(messages) - keep_tail

    if _message_has_tool_use(messages[head_end - 1]):
        while head_end < len(messages) and _is_tool_result_message(messages[head_end]):
            head_end += 1
    if (
        0 < tail_start < len(messages)
        and _is_tool_result_message(messages[tail_start])
        and _message_has_tool_use(messages[tail_start - 1])
    ):
        tail_start -= 1
    if head_end >= tail_start:
        return messages

    snipped = tail_start - head_end
    marker = {"role": "user", "content": f"[snipped {snipped} messages]"}
    return messages[:head_end] + [marker] + messages[tail_start:]


# 收集消息历史中的全部工具结果块，并保留消息索引和块索引供后续原位压缩使用。
def collect_tool_results(messages: list[dict]) -> list[tuple[int, int, dict]]:
    blocks = []
    for message_index, message in enumerate(messages):
        content = message.get("content")
        if message.get("role") != "user" or not isinstance(content, list):
            continue
        for block_index, block in enumerate(content):
            if isinstance(block, dict) and block.get("type") == "tool_result":
                blocks.append((message_index, block_index, block))
    return blocks


# 将较早且较长的工具结果替换为占位文本，只完整保留最近的若干结果并原位更新消息历史。
def micro_compact(messages: list[dict]) -> list[dict]:
    tool_results = collect_tool_results(messages)
    if len(tool_results) <= KEEP_RECENT:
        return messages

    for _, _, block in tool_results[:-KEEP_RECENT]:
        if len(str(block.get("content", ""))) > 120:
            block["content"] = COMPACTED_TOOL_RESULT
    return messages


# 把工具调用编号转换为不含路径分隔符的稳定文件名，并追加摘要以降低不同编号清洗后的碰撞概率。
def _safe_tool_result_filename(tool_use_id: str) -> str:
    raw_identifier = str(tool_use_id)
    normalized = re.sub(r"[^A-Za-z0-9._-]+", "_", raw_identifier)
    normalized = normalized.strip("._-")[:64] or "unknown"
    digest = hashlib.sha256(
        raw_identifier.encode("utf-8", errors="replace")
    ).hexdigest()[:12]
    return f"{normalized}-{digest}.txt"


# 将完整工具输出写入受控目录并返回包含文件路径和可选内容预览的紧凑消息。
def _store_tool_output(tool_use_id: str, output: str, preview_chars: int = 2_000) -> str:
    try:
        TOOL_RESULTS_DIR.mkdir(parents=True, exist_ok=True)
        path = TOOL_RESULTS_DIR / _safe_tool_result_filename(tool_use_id)
        if not path.exists():
            path.write_text(output, encoding="utf-8")
    except (OSError, UnicodeError):
        # 只读工作区无法保存结果时保留原始输出，让文件系统限制不会中断 Agent 主流程。
        return output

    preview = output[:preview_chars]
    preview_section = f"\nPreview:\n{preview}" if preview else ""
    return (
        "<persisted-output>\n"
        f"Full output: {path}"
        f"{preview_section}\n"
        "</persisted-output>"
    )


# 超过阈值时持久化单条工具输出，较短内容保持原样以避免不必要的磁盘写入。
def persist_large_output(tool_use_id: str, output: str) -> str:
    if len(output) <= PERSIST_THRESHOLD:
        return output
    return _store_tool_output(tool_use_id, output)


# 将最新用户消息中的工具结果控制在目标总预算内，按内容长度从大到小持久化以尽快释放上下文。
def tool_result_budget(
    messages: list[dict],
    max_bytes: int = 200_000,
) -> list[dict]:
    if max_bytes < 1:
        raise ValueError("max_bytes must be a positive integer")

    last_message = messages[-1] if messages else None
    if (
        not last_message
        or last_message.get("role") != "user"
        or not isinstance(last_message.get("content"), list)
    ):
        return messages

    blocks = [
        block
        for block in last_message["content"]
        if isinstance(block, dict) and block.get("type") == "tool_result"
    ]
    total = sum(len(str(block.get("content", ""))) for block in blocks)
    if total <= max_bytes:
        return messages

    ranked_blocks = sorted(
        blocks,
        key=lambda block: len(str(block.get("content", ""))),
        reverse=True,
    )
    for block in ranked_blocks:
        if total <= max_bytes:
            break
        content = str(block.get("content", ""))
        tool_use_id = str(block.get("tool_use_id", "unknown"))
        compacted = _store_tool_output(tool_use_id, content, preview_chars=0)
        if len(compacted) >= len(content):
            continue
        block["content"] = compacted
        total -= len(content) - len(compacted)
    return messages


# 尝试将压缩前的完整消息历史写入唯一 JSONL 文件；目录不可写时返回 None，但不阻断后续摘要。
def write_transcript(messages: list[dict]) -> Path | None:
    try:
        TRANSCRIPT_DIR.mkdir(parents=True, exist_ok=True)
        timestamp = time.time_ns()
        path = TRANSCRIPT_DIR / f"transcript_{timestamp}.jsonl"
        suffix = 1
        while path.exists():
            path = TRANSCRIPT_DIR / f"transcript_{timestamp}_{suffix}.jsonl"
            suffix += 1

        with path.open("w", encoding="utf-8", newline="\n") as transcript:
            for message in messages:
                serialized = json.dumps(message, ensure_ascii=False, default=str)
                transcript.write(serialized + "\n")
        return path
    except (OSError, UnicodeError):
        return None


# 序列化摘要所需的对话上下文；超出上限时从中间截断，确保最初目标和最近进展都能进入摘要请求。
def _serialize_summary_context(messages: list[dict]) -> str:
    conversation = json.dumps(messages, ensure_ascii=False, default=str)
    if len(conversation) <= SUMMARY_INPUT_LIMIT:
        return conversation

    marker = "\n...[middle of conversation omitted]...\n"
    available = SUMMARY_INPUT_LIMIT - len(marker)
    head_length = available // 2
    tail_length = available - head_length
    return conversation[:head_length] + marker + conversation[-tail_length:]


# 调用模型总结历史消息，保留当前目标、关键结论、文件改动、剩余工作和用户约束。
def summarize_history(messages: list[dict]) -> str:
    conversation = _serialize_summary_context(messages)
    prompt = (
        "Summarize this coding-agent conversation so work can continue.\n"
        "Preserve: 1. current goal, 2. key findings/decisions, 3. files read/changed, "
        "4. remaining work, 5. user constraints.\n"
        "Be compact but concrete.\n\n"
        + conversation
    )
    response = client.messages.create(
        model=MODEL,
        messages=[{"role": "user", "content": prompt}],
        max_tokens=2_000,
    )
    return extract_text(response.content).strip() or "(empty summary)"


# 主动压缩完整历史；先保存转录，再用单条摘要消息替换原有上下文。
def compact_history(messages: list[dict]) -> list[dict]:
    transcript_path = write_transcript(messages)
    if transcript_path is not None:
        print(f"[transcript saved: {transcript_path}]")
    else:
        print("[transcript not saved: output directory is unavailable]")
    summary = summarize_history(messages)
    return [{"role": "user", "content": f"[Compacted]\n\n{summary}"}]


# 在主请求触发上下文溢出后生成摘要，并保留最近消息及完整的工具调用消息对用于重试。
def reactive_compact(messages: list[dict]) -> list[dict]:
    transcript_path = write_transcript(messages)
    if transcript_path is not None:
        print(f"[transcript saved: {transcript_path}]")
    else:
        print("[transcript not saved: output directory is unavailable]")
    summary = summarize_history(messages)
    tail_start = max(0, len(messages) - 5)
    if (
        0 < tail_start < len(messages)
        and _is_tool_result_message(messages[tail_start])
        and _message_has_tool_use(messages[tail_start - 1])
    ):
        tail_start -= 1
    summary_message = {
        "role": "user",
        "content": f"[Reactive compact]\n\n{summary}",
    }
    return [summary_message, *messages[tail_start:]]


# 依次执行消息裁剪、旧工具结果微压缩和当前工具结果预算控制，必要时再生成全量历史摘要。
def prepare_history(messages: list[dict]) -> list[dict]:
    prepared = snip_compact(messages)
    prepared = micro_compact(prepared)
    prepared = tool_result_budget(prepared)
    if estimate_size(prepared) > CONTEXT_LIMIT:
        return compact_history(prepared)
    return prepared
