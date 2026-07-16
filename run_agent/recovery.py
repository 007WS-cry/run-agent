import random
import time
from dataclasses import dataclass
from typing import Callable, TypeVar

from run_agent.config import FALLBACK_MODEL, MODEL

# 本文件负责对模型请求故障进行分类，并为输出截断、上下文超限、限流和服务过载保存统一恢复状态。

# 普通模型请求使用的默认最大输出 token 数。
DEFAULT_MAX_TOKENS = 8_000

# 首次输出截断后重试请求使用的最大输出 token 数。
ESCALATED_MAX_TOKENS = 64_000

# 输出上限升级后仍被截断时允许追加续写提示的最大次数。
MAX_RECOVERY_RETRIES = 3

# 单次模型调用遇到 429 或 529 时允许执行的最大请求次数。
MAX_RETRIES = 10

# 指数退避的初始等待毫秒数。
BASE_DELAY_MS = 500

# 指数退避允许达到的最大等待毫秒数。
MAX_DELAY_MS = 32_000

# 在指数退避基础上增加的最大随机抖动比例。
JITTER_RATIO = 0.25

# 模型输出仍被截断时追加的续写指令，要求直接衔接原内容并避免重复说明。
CONTINUATION_PROMPT = (
    "Output token limit hit. Resume directly - "
    "no apology, no recap. Pick up mid-thought."
)

# 表示恢复包装器可以返回的任意模型响应类型。
ResponseT = TypeVar("ResponseT")


# 保存一次 Agent 循环内跨请求复用的恢复进度，防止同一路径无限重试。
@dataclass
class RecoveryState:
    # 保存当前请求使用的模型编号；连续服务过载时可切换为备用模型。
    current_model: str = MODEL
    # 保存当前请求的输出 token 上限；首次截断后从默认值升级到更大值。
    max_tokens: int = DEFAULT_MAX_TOKENS
    # 标记输出上限是否已经执行过一次升级。
    has_escalated: bool = False
    # 记录升级后已经追加续写提示的次数。
    recovery_count: int = 0
    # 标记是否至少执行过一次响应式上下文压缩。
    has_attempted_reactive_compact: bool = False
    # 记录已经执行的响应式上下文压缩次数。
    reactive_compact_count: int = 0
    # 记录当前瞬态故障序列中的重试编号。
    retry_attempt: int = 0
    # 记录连续收到 529 服务过载响应的次数。
    consecutive_529: int = 0


# 从接口异常或其响应对象中读取 HTTP 状态码，无法识别时返回 None。
def _error_status_code(error: Exception) -> int | None:
    status_code = getattr(error, "status_code", None)
    if status_code is None:
        status_code = getattr(getattr(error, "response", None), "status_code", None)
    try:
        return int(status_code) if status_code is not None else None
    except (TypeError, ValueError):
        return None


# 从接口异常的响应头中解析 Retry-After 秒数，缺失或格式无效时返回 None。
def _retry_after_seconds(error: Exception) -> float | None:
    response = getattr(error, "response", None)
    headers = getattr(response, "headers", None) or getattr(error, "headers", None)
    if not headers:
        return None

    retry_after = headers.get("retry-after") or headers.get("Retry-After")
    try:
        delay = float(retry_after)
    except (TypeError, ValueError):
        return None
    return delay if delay >= 0 else None


# 判断接口异常是否表示请求上下文过长；同时识别 HTTP 413、异常文本和结构化响应体。
def is_prompt_too_long_error(error: Exception) -> bool:
    if _error_status_code(error) == 413:
        return True

    error_text = f"{error} {getattr(error, 'body', '')}".casefold()
    markers = (
        "context window",
        "context_length_exceeded",
        "maximum context length",
        "prompt is too long",
        "prompt_too_long",
        "too many tokens",
    )
    return any(marker in error_text for marker in markers)


# 判断接口异常是否表示 429 限流，兼容状态码和 SDK 异常类型名称两种形式。
def is_rate_limit_error(error: Exception) -> bool:
    return (
        _error_status_code(error) == 429
        or type(error).__name__.casefold() == "ratelimiterror"
    )


# 判断接口异常是否表示 529 服务过载，兼容状态码、SDK 类型名称和结构化错误文本。
def is_overloaded_error(error: Exception) -> bool:
    error_text = f"{error} {getattr(error, 'body', '')}".casefold()
    return (
        _error_status_code(error) == 529
        or type(error).__name__.casefold() == "overloadederror"
        or "overloaded_error" in error_text
    )


# 计算瞬态故障的等待秒数；服务端给出 Retry-After 时优先采用，否则使用指数退避和随机抖动。
def retry_delay(attempt: int, retry_after: float | None = None) -> float:
    if attempt < 0:
        raise ValueError("attempt must not be negative")
    if retry_after is not None:
        if retry_after < 0:
            raise ValueError("retry_after must not be negative")
        return retry_after

    base = min(BASE_DELAY_MS * (2 ** attempt), MAX_DELAY_MS) / 1000
    return base + random.uniform(0, base * JITTER_RATIO)


# 执行模型请求并恢复 429/529 瞬态故障；达到次数上限或遇到其他异常时保留原异常交给外层处理。
def with_retry(
    request: Callable[[], ResponseT],
    state: RecoveryState,
    max_retries: int = MAX_RETRIES,
) -> ResponseT:
    if max_retries < 1:
        raise ValueError("max_retries must be a positive integer")

    for attempt in range(max_retries):
        try:
            response = request()
        except Exception as error:
            overloaded = is_overloaded_error(error)
            if not overloaded and not is_rate_limit_error(error):
                raise

            state.retry_attempt = attempt + 1
            if overloaded:
                state.consecutive_529 += 1
                if (
                    state.consecutive_529 >= 3
                    and FALLBACK_MODEL
                    and state.current_model != FALLBACK_MODEL
                ):
                    state.current_model = FALLBACK_MODEL
                    print(f"[recover] switched to fallback model: {FALLBACK_MODEL}")
            else:
                state.consecutive_529 = 0

            if attempt == max_retries - 1:
                raise

            delay = retry_delay(attempt, _retry_after_seconds(error))
            status = 529 if overloaded else 429
            print(
                f"[recover] HTTP {status}; retry "
                f"{attempt + 1}/{max_retries - 1} in {delay:.2f}s"
            )
            time.sleep(delay)
            continue

        state.retry_attempt = 0
        state.consecutive_529 = 0
        return response

    raise RuntimeError("retry loop ended unexpectedly")
