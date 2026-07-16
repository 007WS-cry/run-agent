from run_agent.config import DESTRUCTIVE, DENY_LIST, HOOKS
from run_agent.tools import WORKDIR

# 本文件实现 Agent 生命周期钩子，包括命令权限检查、工具调用日志、输出告警、上下文提示和会话汇总。

# 单次工具输出超过该字符数时只打印告警，不修改实际返回给模型的内容。
LARGE_OUTPUT_THRESHOLD = 100_000


# 将回调函数追加到指定事件的钩子列表中，注册顺序就是事件触发时的执行顺序。
def register_hook(event: str, callback) -> None:
    HOOKS[event].append(callback)


# 依次执行指定事件下的全部钩子；遇到第一个非空返回值时立即停止并把结果交给调用方处理。
def trigger_hooks(event: str, *args):
    for callback in HOOKS[event]:
        result = callback(*args)
        if result is not None:
            return result
    return None


# 在 Shell 工具执行前检查命令；禁止列表直接拦截，破坏性列表则交由用户确认，其他工具和安全命令正常放行。
def permission_hook(block):
    if block.name != "bash":
        return None

    # 命令参数必须是字符串；异常输入按默认拒绝处理，避免权限检查失效后继续执行。
    command = block.input.get("command", "")
    if not isinstance(command, str):
        return "Permission denied: invalid shell command"

    # 使用不区分大小写的包含匹配逐项检查硬禁止列表，命中后不再询问用户。
    normalized_command = command.casefold()
    for pattern in DENY_LIST:
        if pattern.casefold() in normalized_command:
            print(f"\n\033[31m⛔ Blocked: '{pattern}' is on the deny list\033[0m")
            return "Permission denied by deny list"

    # 只记录第一个命中的破坏性模式，使一条命令最多触发一次人工确认。
    matched_pattern = next(
        (
            pattern
            for pattern in DESTRUCTIVE
            if pattern.casefold() in normalized_command
        ),
        None,
    )
    if matched_pattern is None:
        return None

    print("\n\033[33m⚠ Potentially destructive command\033[0m")
    print(f"   Tool: {block.name}({block.input})")
    try:
        # 仅 y 或 yes 表示允许；空输入、终端中断及其他回答均按拒绝处理。
        choice = input("   Allow? [y/N] ").strip().casefold()
    except (EOFError, KeyboardInterrupt):
        choice = ""

    if choice not in ("y", "yes"):
        return "Permission denied by user"
    return None


# 在工具执行前打印工具名称和至多两个参数的简短预览，便于用户观察 Agent 的实际操作。
def log_hook(block):
    args_preview = str(list(block.input.values())[:2])[:60]
    print(f"\033[90m[HOOK]\033[0m \033[33m> {block.name}\033[0m ({args_preview})")
    return None


# 在工具执行后检查返回内容长度，超过阈值时打印黄色告警，提醒用户关注较大的上下文占用。
def large_output_hook(block, output):
    output_length = len(str(output))
    if output_length > LARGE_OUTPUT_THRESHOLD:
        print(
            f"\033[33m[HOOK] ⚠ Large output from {block.name}: "
            f"{output_length} chars\033[0m"
        )
    return None


# 在用户提交问题时打印当前工作目录，为本轮任务提供直观的工作区上下文提示。
def context_inject_hook(query: str):
    print(f"\033[90m[HOOK] UserPromptSubmit: working in {WORKDIR}\033[0m")
    return None


# 在一轮 Agent 循环停止时遍历消息历史，统计并打印当前会话累计产生的工具结果数量。
def summary_hook(messages: list):
    tool_count = 0
    for message in messages:
        content = message.get("content", [])
        if not isinstance(content, list):
            continue
        tool_count += sum(
            1
            for block in content
            if isinstance(block, dict) and block.get("type") == "tool_result"
        )

    print(f"\033[90m[HOOK] Stop: session used {tool_count} tool calls\033[0m")
    return None


# 把各个回调绑定到对应生命周期事件；权限检查优先于日志，以免被拒绝的工具显示为即将执行。
register_hook("UserPromptSubmit", context_inject_hook)
register_hook("PreToolUse", permission_hook)
register_hook("PreToolUse", log_hook)
register_hook("PostToolUse", large_output_hook)
register_hook("Stop", summary_hook)
