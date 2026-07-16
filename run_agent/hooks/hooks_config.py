# 本文件集中保存生命周期事件注册表，使钩子实现与通用运行配置保持解耦。

# 定义生命周期事件与回调列表的注册表，hooks 模块会按事件名称向其中追加处理函数。
HOOKS = {
    "UserPromptSubmit": [],
    "PreToolUse": [],
    "PostToolUse": [],
    "Stop": [],
}
