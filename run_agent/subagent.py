from run_agent.config import MODEL, WORKDIR, client
from run_agent.content import extract_text
from run_agent.hooks.hooks import trigger_hooks
from run_agent.recovery import DEFAULT_MAX_TOKENS
from run_agent.tools.tools_config import SUBTOOL_HANDLERS, SUBTOOLS

# 本文件负责执行受限的 Subagent 对话循环，让主 Agent 能把独立编码任务委派给不具备再次委派能力的子任务。

# 限制单次 Subagent 最多经历的模型请求轮数，防止模型持续调用工具而无法结束任务。
MAX_SUBAGENT_ROUNDS = 30

# 定义 Subagent 的固定系统提示词；共享主进程工作区，同时明确要求完成任务后返回简短总结且不得继续创建 Subagent。
SUB_SYSTEM = (
    f"You are a coding subagent at {WORKDIR}. "
    "Complete the task, then return a concise final summary. "
    "Do not spawn more agents."
)

# 启动 Subagent 并执行其受限工具循环；最终返回文本总结，超过轮数限制时返回明确错误供主 Agent 处理。
def spawn_subagent(description: str) -> str:
    messages = [{"role": "user", "content": description}]
    for _ in range(MAX_SUBAGENT_ROUNDS):
        response = client.messages.create(
            model=MODEL,
            system=SUB_SYSTEM,
            messages=messages,
            tools=SUBTOOLS,
            max_tokens=DEFAULT_MAX_TOKENS,
        )
        messages.append({"role": "assistant", "content": response.content})
        if response.stop_reason != "tool_use":
            return extract_text(response.content)

        results = []
        for block in response.content:
            if block.type != "tool_use":
                continue
            blocked = trigger_hooks("PreToolUse", block)
            if blocked:
                results.append({
                    "type": "tool_result",
                    "tool_use_id": block.id,
                    "content": str(blocked),
                })
                continue
            handler = SUBTOOL_HANDLERS.get(block.name)
            output = handler(**block.input) if handler else f"Unknown: {block.name}"
            trigger_hooks("PostToolUse", block, output)
            print(f"  \033[90m[sub] {block.name}: {str(output)[:100]}\033[0m")
            results.append({
                "type": "tool_result",
                "tool_use_id": block.id,
                "content": output,
            })
        messages.append({"role": "user", "content": results})

    return (
        "Error: Subagent reached the maximum of "
        f"{MAX_SUBAGENT_ROUNDS} rounds without a final response."
    )
