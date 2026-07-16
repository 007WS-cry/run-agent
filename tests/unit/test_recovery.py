from copy import deepcopy
from types import SimpleNamespace

import pytest

from run_agent import recovery, runtime

# 本文件对模型请求恢复功能进行单元测试，覆盖错误分类、退避等待、备用模型切换和输出截断续写流程。


# 模拟带 HTTP 状态码和响应头的接口异常，使测试不依赖真实 Anthropic 网络响应。
class FakeStatusError(RuntimeError):
    # 保存异常消息、状态码和响应头，复现恢复模块读取 SDK 异常属性时需要的最小结构。
    def __init__(self, message: str, status_code: int, headers=None):
        super().__init__(message)
        self.status_code = status_code
        self.response = SimpleNamespace(
            status_code=status_code,
            headers=headers or {},
        )


# 验证指数退避会加入配置范围内的抖动，并在服务端给出 Retry-After 时优先使用指定秒数。
def test_retry_delay_uses_jitter_and_retry_after(monkeypatch):
    monkeypatch.setattr(recovery.random, "uniform", lambda lower, upper: upper)

    assert recovery.retry_delay(2) == 2.5
    assert recovery.retry_delay(5, retry_after=7.25) == 7.25


# 验证 429 限流会按 Retry-After 等待后重试，并在下一次请求成功时返回原始响应。
def test_with_retry_recovers_rate_limit_with_retry_after(monkeypatch):
    calls = []
    delays = []
    error = FakeStatusError("rate limited", 429, {"Retry-After": "1.5"})

    # 模拟先限流后成功的请求；记录调用次数以确认包装器没有额外执行请求。
    def request():
        calls.append("request")
        if len(calls) == 1:
            raise error
        return "ok"

    monkeypatch.setattr(recovery.time, "sleep", delays.append)

    result = recovery.with_retry(
        request,
        recovery.RecoveryState(current_model="primary"),
    )

    assert result == "ok"
    assert calls == ["request", "request"]
    assert delays == [1.5]


# 验证连续三次 529 会把后续请求切换到备用模型，并在备用模型成功后清空瞬态错误计数。
def test_with_retry_switches_to_fallback_after_three_overloads(monkeypatch):
    used_models = []
    state = recovery.RecoveryState(current_model="primary")

    # 模拟前三次服务过载、第四次成功；每次读取状态中的当前模型以验证切换发生在下一次请求前。
    def request():
        used_models.append(state.current_model)
        if len(used_models) <= 3:
            raise FakeStatusError("overloaded", 529)
        return "fallback response"

    monkeypatch.setattr(recovery, "FALLBACK_MODEL", "backup")
    monkeypatch.setattr(recovery.random, "uniform", lambda lower, upper: 0)
    monkeypatch.setattr(recovery.time, "sleep", lambda delay: None)

    result = recovery.with_retry(request, state)

    assert result == "fallback response"
    assert used_models == ["primary", "primary", "primary", "backup"]
    assert state.current_model == "backup"
    assert state.retry_attempt == 0
    assert state.consecutive_529 == 0


# 验证达到最大瞬态请求次数时重新抛出最后一个接口异常，并且不会在最后一次失败后继续等待。
def test_with_retry_reraises_after_attempt_limit(monkeypatch):
    calls = []
    delays = []
    error = FakeStatusError("still overloaded", 529)

    # 模拟始终过载的请求；所有调用抛出同一个异常以便检查最终异常没有被包装替换。
    def request():
        calls.append("request")
        raise error

    monkeypatch.setattr(recovery.random, "uniform", lambda lower, upper: 0)
    monkeypatch.setattr(recovery.time, "sleep", delays.append)

    with pytest.raises(FakeStatusError) as raised:
        recovery.with_retry(
            request,
            recovery.RecoveryState(current_model="primary"),
            max_retries=3,
        )

    assert raised.value is error
    assert len(calls) == 3
    assert delays == [0.5, 1.0]


# 验证鉴权等非瞬态异常不会被重试或等待，避免恢复逻辑隐藏真实配置问题。
def test_with_retry_reraises_unrelated_error_without_sleep(monkeypatch):
    sleep = lambda delay: pytest.fail("unexpected sleep")
    monkeypatch.setattr(recovery.time, "sleep", sleep)

    with pytest.raises(RuntimeError, match="authentication failed"):
        recovery.with_retry(
            lambda: (_ for _ in ()).throw(RuntimeError("authentication failed")),
            recovery.RecoveryState(current_model="primary"),
        )


# 验证上下文超限分类同时识别 HTTP 413 和常见服务端错误标记，不把普通异常误判为可压缩错误。
def test_prompt_too_long_error_classification():
    assert recovery.is_prompt_too_long_error(FakeStatusError("payload", 413))
    assert recovery.is_prompt_too_long_error(
        RuntimeError("context_length_exceeded")
    )
    assert not recovery.is_prompt_too_long_error(RuntimeError("invalid api key"))


# 验证第一次输出截断只提高 token 上限并重试相同历史，不会把需要丢弃的不完整回答加入消息。
def test_agent_loop_escalates_max_tokens_without_appending_truncated_output(
    monkeypatch,
    response_factory,
    content_block_factory,
):
    snapshots = []
    responses = iter([
        response_factory(
            content=[content_block_factory("text", text="discarded")],
            stop_reason="max_tokens",
        ),
        response_factory(
            content=[content_block_factory("text", text="complete")],
        ),
    ])

    # 模拟模型创建方法并深拷贝每次参数，避免后续历史原位更新影响先前请求快照。
    def create(**kwargs):
        snapshots.append(deepcopy(kwargs))
        return next(responses)

    monkeypatch.setattr(
        runtime,
        "client",
        SimpleNamespace(messages=SimpleNamespace(create=create)),
    )
    monkeypatch.setattr(runtime, "MODEL", "primary")
    monkeypatch.setattr(runtime, "SYSTEM", "unit-test-system")
    monkeypatch.setattr(runtime.compact, "prepare_history", lambda messages: messages)
    messages = [{"role": "user", "content": "write a long answer"}]

    runtime.agent_loop(messages)

    assert [snapshot["max_tokens"] for snapshot in snapshots] == [8_000, 64_000]
    assert snapshots[0]["messages"] == snapshots[1]["messages"] == [
        {"role": "user", "content": "write a long answer"}
    ]
    assert messages == [
        {"role": "user", "content": "write a long answer"},
        {"role": "assistant", "content": [{"type": "text", "text": "complete"}]},
    ]


# 验证升级后持续截断时最多追加三条续写提示，超过限制后保留最后一段输出并结束循环。
def test_agent_loop_limits_continuations_after_escalation(
    monkeypatch,
    response_factory,
    content_block_factory,
):
    responses = [
        response_factory(
            content=[content_block_factory("text", text=f"part-{index}")],
            stop_reason="max_tokens",
        )
        for index in range(5)
    ]
    create = SimpleNamespace(side_effect=None)
    response_iterator = iter(responses)

    # 模拟始终因为输出上限停止的模型请求，记录调用次数以验证升级一次加三次续写的边界。
    def create_message(**kwargs):
        create.call_count = getattr(create, "call_count", 0) + 1
        return next(response_iterator)

    monkeypatch.setattr(
        runtime,
        "client",
        SimpleNamespace(messages=SimpleNamespace(create=create_message)),
    )
    monkeypatch.setattr(runtime, "MODEL", "primary")
    monkeypatch.setattr(runtime, "SYSTEM", "unit-test-system")
    monkeypatch.setattr(runtime.compact, "prepare_history", lambda messages: messages)
    messages = [{"role": "user", "content": "continue until complete"}]

    runtime.agent_loop(messages)

    assert create.call_count == 5
    assert [
        message["content"][0]["text"]
        for message in messages
        if message["role"] == "assistant"
    ] == ["part-1", "part-2", "part-3", "part-4"]
    assert sum(
        message == {"role": "user", "content": recovery.CONTINUATION_PROMPT}
        for message in messages
    ) == 3
    assert messages[-1] == {
        "role": "assistant",
        "content": [{"type": "text", "text": "part-4"}],
    }
