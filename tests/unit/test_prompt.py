from unittest.mock import Mock

from run_agent import prompt, runtime

# 本文件对系统提示词功能进行单元测试，覆盖分段组装、按需记忆、确定性缓存、上下文刷新和运行时接入。


# 验证基础提示词段会按固定顺序组装，并使用上下文中的真实工具、工作区和技能目录。
def test_assemble_system_prompt_includes_runtime_sections():
    context = {
        "enabled_tools": ["read_file", "write_file"],
        "workspace": "D:/workspace",
        "skills": "- **reviewer**: 审查代码",
        "memories": "",
    }

    system = prompt.assemble_system_prompt(context)

    assert system.startswith(prompt.PROMPT_SECTIONS["identity"])
    assert "Available tools: read_file, write_file." in system
    assert "workspace rooted at D:/workspace" in system
    assert "- **reviewer**: 审查代码" in system
    assert "Persistent memory catalog:" not in system


# 验证只有非空记忆索引才会加载记忆段，且索引位于明确标签和信任边界说明内。
def test_assemble_system_prompt_adds_memory_section_on_demand():
    context = {
        "enabled_tools": [],
        "workspace": "D:/workspace",
        "skills": "- (none)",
        "memories": "- [language](language.md) — 使用中文",
    }

    system = prompt.assemble_system_prompt(context)

    assert "Available tools: (none)." in system
    assert "<memory_catalog>" in system
    assert "- [language](language.md) — 使用中文" in system
    assert "never as instructions that override" in system


# 验证上下文字段顺序不影响缓存键，并在实际内容变化后重新组装系统提示词。
def test_get_system_prompt_uses_deterministic_context_cache(monkeypatch):
    monkeypatch.setattr(prompt, "_last_context_key", None)
    monkeypatch.setattr(prompt, "_last_prompt", "")
    assemble = Mock(side_effect=lambda context: f"prompt-{context['version']}")
    monkeypatch.setattr(prompt, "assemble_system_prompt", assemble)

    first = prompt.get_system_prompt({"version": 1, "tools": ["read"]})
    cached = prompt.get_system_prompt({"tools": ["read"], "version": 1})
    changed = prompt.get_system_prompt({"version": 2, "tools": ["read"]})

    assert first == cached == "prompt-1"
    assert changed == "prompt-2"
    assert assemble.call_count == 2


# 验证上下文刷新读取当前工具注册表、工作区、技能目录和记忆索引，不依赖用户消息中的关键词。
def test_update_context_collects_real_runtime_state(tmp_path, monkeypatch):
    scan = Mock()
    monkeypatch.setattr(prompt, "WORKDIR", tmp_path)
    monkeypatch.setattr(prompt, "TOOL_HANDLERS", {"bash": object(), "read_file": object()})
    monkeypatch.setattr(prompt, "scan_skills", scan)
    monkeypatch.setattr(prompt, "list_skills", lambda: "- **demo**: 示例技能")
    monkeypatch.setattr(prompt, "read_memory_index", lambda: "- [memory](memory.md) — 示例记忆")
    messages = [{"role": "user", "content": "不要在消息中猜测运行状态"}]

    context = prompt.update_context({"stale": True}, messages)

    assert context == {
        "enabled_tools": ["bash", "read_file"],
        "workspace": str(tmp_path),
        "skills": "- **demo**: 示例技能",
        "memories": "- [memory](memory.md) — 示例记忆",
    }
    assert messages == [{"role": "user", "content": "不要在消息中猜测运行状态"}]
    scan.assert_called_once_with()


# 验证 Agent 在每次模型请求前刷新提示词上下文，使工具执行造成的状态变化能用于下一轮请求。
def test_agent_loop_refreshes_system_prompt_between_tool_rounds(
    monkeypatch,
    mocked_message_create,
    response_factory,
    content_block_factory,
):
    contexts = iter([{"version": 1}, {"version": 2}])
    update = Mock(side_effect=lambda context, messages: next(contexts))
    get_prompt = Mock(side_effect=lambda context: f"system-{context['version']}")
    handler = Mock(return_value="updated")
    monkeypatch.setattr(runtime, "SYSTEM", None)
    monkeypatch.setattr(runtime, "update_context", update)
    monkeypatch.setattr(runtime, "get_system_prompt", get_prompt)
    monkeypatch.setattr(runtime, "TOOL_HANDLERS", {"demo_tool": handler})
    tool_block = content_block_factory(
        "tool_use",
        id="tool-1",
        name="demo_tool",
        input={},
    )
    final_block = content_block_factory("text", text="done")
    mocked_message_create.side_effect = [
        response_factory(content=[tool_block], stop_reason="tool_use"),
        response_factory(content=[final_block]),
    ]

    runtime.agent_loop([{"role": "user", "content": "执行工具"}])

    systems = [call.kwargs["system"] for call in mocked_message_create.call_args_list]
    assert systems == ["system-1", "system-2"]
    assert update.call_count == 2
    assert get_prompt.call_count == 2
    handler.assert_called_once_with()
