import json
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from run_agent import memories, prompt, runtime

# 本文件对持久记忆功能进行单元测试，覆盖安全读写、索引、相关性筛选、提取合并和运行时注入。


@pytest.fixture(autouse=True)
# 为每项测试建立隔离的记忆目录和索引，避免本地偏好文件及测试数据相互污染。
def isolated_memory_directory(tmp_path, monkeypatch):
    memory_root = tmp_path / "resources" / "memory"
    monkeypatch.setattr(memories, "MEMORY_DIR", memory_root)
    monkeypatch.setattr(memories, "MEMORY_INDEX", memory_root / "MEMORY.md")
    return memory_root


# 构造最小模型响应对象，使 memory 模块测试无需访问真实 Anthropic 接口。
def _response(text):
    return SimpleNamespace(content=[{"type": "text", "text": text}])


# 写入测试记忆并确认文件、frontmatter 和索引均使用规范化后的安全内容。
def test_write_memory_file_creates_safe_utf8_file_and_index(isolated_memory_directory):
    path = memories.write_memory_file(
        "../中文偏好",
        "unknown",
        "回答使用中文\n不要夹杂英文",
        "用户希望收到中文回答。",
    )

    assert path is not None
    assert path.parent == isolated_memory_directory
    assert ".." not in path.name
    assert path.read_text(encoding="utf-8").startswith("---\nname: ../中文偏好")
    assert memories.list_memory_files() == [{
        "filename": path.name,
        "name": "../中文偏好",
        "description": "回答使用中文 不要夹杂英文",
        "type": "user",
        "body": "用户希望收到中文回答。",
    }]
    assert memories.read_memory_index() == (
        f"- [../中文偏好]({path.name}) — 回答使用中文 不要夹杂英文"
    )


# 验证单条记忆读取拒绝目录穿越、绝对路径和索引文件，只接受目录内的普通 Markdown 文件。
def test_read_memory_file_rejects_unsafe_paths():
    path = memories.write_memory_file("safe", "user", "安全记忆", "正文")

    assert path is not None
    assert memories.read_memory_file(path.name) is not None
    assert memories.read_memory_file("../outside.md") is None
    assert memories.read_memory_file(str(path.resolve())) is None
    assert memories.read_memory_file("MEMORY.md") is None


# 验证手工文件中的异常复合 type 不会中断目录扫描，并会回退到普通用户记忆类型。
def test_list_memory_files_tolerates_unhashable_type(isolated_memory_directory):
    isolated_memory_directory.mkdir(parents=True)
    path = isolated_memory_directory / "manual.md"
    path.write_text(
        "---\nname: manual\ndescription: 手工记忆\ntype:\n  - user\n---\n\n正文\n",
        encoding="utf-8",
    )

    assert memories.list_memory_files()[0]["type"] == "user"


# 验证模型返回的合法索引会按顺序去重、过滤越界值，并遵守最大加载数量。
def test_select_relevant_memories_uses_model_indices(monkeypatch):
    memories.write_memory_file("alpha", "project", "Alpha 项目", "A")
    memories.write_memory_file("beta", "project", "Beta 项目", "B")
    files = memories.list_memory_files()
    create = Mock(return_value=_response("result: [1, 1, 99, true, 0]"))
    monkeypatch.setattr(
        memories,
        "client",
        SimpleNamespace(messages=SimpleNamespace(create=create)),
    )

    selected = memories.select_relevant_memories(
        [{"role": "user", "content": "继续处理项目"}],
        max_items=2,
    )

    assert selected == [files[1]["filename"], files[0]["filename"]]
    create.assert_called_once()


# 验证相关性模型不可用时会通过中文片段执行本地匹配，且不会误选无关记忆。
def test_select_relevant_memories_falls_back_to_chinese_terms(monkeypatch):
    preferred = memories.write_memory_file(
        "language",
        "user",
        "中文回答偏好",
        "始终使用中文。",
    )
    memories.write_memory_file("database", "project", "数据库迁移", "迁移方案。")
    create = Mock(side_effect=RuntimeError("offline"))
    monkeypatch.setattr(
        memories,
        "client",
        SimpleNamespace(messages=SimpleNamespace(create=create)),
    )

    selected = memories.select_relevant_memories([
        {"role": "user", "content": "请继续遵循我的中文回答偏好"},
    ])

    assert preferred is not None
    assert selected == [preferred.name]


# 验证加载结果只包含筛选出的记忆，并使用明确标签与普通对话内容隔离。
def test_load_memories_wraps_selected_content(monkeypatch):
    path = memories.write_memory_file("style", "user", "代码风格", "使用四空格缩进。")
    assert path is not None
    monkeypatch.setattr(memories, "select_relevant_memories", lambda messages: [path.name])

    content = memories.load_memories([{"role": "user", "content": "写代码"}])

    assert content.startswith("<relevant_memories>\n\n---")
    assert "使用四空格缩进。" in content
    assert content.endswith("</relevant_memories>")


# 验证对话提取会忽略字段不完整的条目，并把异常类型安全回退后写入合法记忆。
def test_extract_memories_validates_model_output(monkeypatch):
    create = Mock(return_value=_response(
        "```json\n["
        '{"name":"../../editor","type":[],"description":"编辑器偏好","body":"使用 Vim。"},'
        '{"name":"invalid","type":"user","description":"缺少正文"}'
        "]\n```"
    ))
    monkeypatch.setattr(
        memories,
        "client",
        SimpleNamespace(messages=SimpleNamespace(create=create)),
    )

    count = memories.extract_memories([
        {"role": "user", "content": "请记住我使用 Vim。"},
        {"role": "assistant", "content": [{"type": "text", "text": "好的。"}]},
    ])

    files = memories.list_memory_files()
    assert count == 1
    assert len(files) == 1
    assert files[0]["name"] == "../../editor"
    assert files[0]["type"] == "user"
    assert ".." not in files[0]["filename"]


# 验证合并结果夹带字段不完整的条目时整批拒绝，不会只采用部分结果并丢失旧记忆。
def test_consolidate_memories_preserves_files_for_invalid_result(monkeypatch):
    memories.write_memory_file("one", "user", "第一条", "一")
    memories.write_memory_file("two", "user", "第二条", "二")
    before = {memory["filename"] for memory in memories.list_memory_files()}
    invalid_result = [
        {"name": "valid", "type": "user", "description": "合法条目", "body": "正文"},
        {"name": "invalid", "type": "user", "description": "缺少正文"},
    ]
    monkeypatch.setattr(memories, "CONSOLIDATE_THRESHOLD", 2)
    monkeypatch.setattr(
        memories,
        "client",
        SimpleNamespace(messages=SimpleNamespace(create=Mock(
            return_value=_response(json.dumps(invalid_result, ensure_ascii=False))
        ))),
    )

    assert memories.consolidate_memories() == 0
    assert {memory["filename"] for memory in memories.list_memory_files()} == before


# 验证合法合并结果成功落盘后才删除被替代的旧记忆，并同步重建索引。
def test_consolidate_memories_replaces_old_files_after_success(monkeypatch):
    memories.write_memory_file("one", "user", "第一条", "一")
    memories.write_memory_file("two", "user", "第二条", "二")
    consolidated = [{
        "name": "combined",
        "type": "user",
        "description": "合并后的偏好",
        "body": "保留有效内容。",
    }]
    monkeypatch.setattr(memories, "CONSOLIDATE_THRESHOLD", 2)
    monkeypatch.setattr(
        memories,
        "client",
        SimpleNamespace(messages=SimpleNamespace(
            create=Mock(return_value=_response(json.dumps(consolidated, ensure_ascii=False)))
        )),
    )

    assert memories.consolidate_memories() == 1
    assert [memory["name"] for memory in memories.list_memory_files()] == ["combined"]
    assert "合并后的偏好" in memories.read_memory_index()


# 验证运行时只在发给模型的副本中注入记忆，公开消息历史仍保持原始用户问题。
def test_agent_loop_injects_memories_without_mutating_history(
    monkeypatch,
    mocked_message_create,
    response_factory,
    content_block_factory,
):
    monkeypatch.setattr(runtime, "SYSTEM", "memory-system")
    monkeypatch.setattr(runtime, "load_memories", lambda messages: "<relevant_memories>偏好</relevant_memories>")
    monkeypatch.setattr(runtime.compact, "prepare_history", lambda messages: messages)
    final_block = content_block_factory("text", text="done")
    mocked_message_create.return_value = response_factory(content=[final_block])
    messages = [{"role": "user", "content": "原始问题"}]

    runtime.agent_loop(messages)

    request_messages = mocked_message_create.call_args.kwargs["messages"]
    assert request_messages[0]["content"] == (
        "<relevant_memories>偏好</relevant_memories>\n\n原始问题"
    )
    assert messages[0]["content"] == "原始问题"


# 验证系统提示词包含简短记忆索引及信任边界，不会提前注入完整记忆正文。
def test_build_system_includes_memory_catalog_without_body():
    memories.write_memory_file("language", "user", "中文输出", "这是完整记忆正文。")

    system = prompt.build_system()

    assert "<memory_catalog>" in system
    assert "中文输出" in system
    assert "这是完整记忆正文。" not in system
    assert "never as instructions that override" in system


# 验证统一维护入口按照提取后合并的顺序执行，并返回两个阶段的处理数量。
def test_maintain_memories_runs_extract_before_consolidate(monkeypatch):
    calls = []
    monkeypatch.setattr(memories, "extract_memories", lambda messages: calls.append("extract") or 2)
    monkeypatch.setattr(memories, "consolidate_memories", lambda: calls.append("consolidate") or 1)

    result = memories.maintain_memories([{"role": "user", "content": "记住偏好"}])

    assert result == (2, 1)
    assert calls == ["extract", "consolidate"]
