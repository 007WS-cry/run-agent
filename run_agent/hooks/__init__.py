from run_agent.hooks.hooks import (
    context_inject_hook,
    large_output_hook,
    log_hook,
    permission_hook,
    register_hook,
    summary_hook,
    trigger_hooks,
)

# 本模块对外暴露生命周期钩子的稳定导入入口，回调实现和事件注册表分别保存在同目录的独立模块中。

# 明确 hooks 包支持的公共符号，避免事件注册表等内部状态被通配导入意外暴露。
__all__ = [
    "context_inject_hook",
    "large_output_hook",
    "log_hook",
    "permission_hook",
    "register_hook",
    "summary_hook",
    "trigger_hooks",
]
