import pytest

from run_agent import prompt, skills
from run_agent.tools import tools_config

# 本文件对 skills 扩展进行单元测试，通过临时技能目录覆盖 frontmatter 解析、扫描、目录生成、按需加载和工具注册。


# 在每项测试前后替换技能根目录并清空全局注册表，避免扫描结果在测试之间相互影响。
@pytest.fixture(autouse=True)
def isolated_skill_registry(tmp_path, monkeypatch):
    skill_root = tmp_path / "resources" / "skills"
    monkeypatch.setattr(skills, "SKILLS_DIR", skill_root)
    skills.SKILL_REGISTRY.clear()
    yield skill_root
    skills.SKILL_REGISTRY.clear()


# 创建 UTF-8 技能清单；按需建立技能子目录并返回生成的 SKILL.md 路径供测试继续检查。
def _write_skill(skill_root, directory_name, content):
    manifest = skill_root / directory_name / "SKILL.md"
    manifest.parent.mkdir(parents=True)
    manifest.write_text(content, encoding="utf-8")
    return manifest


# 验证合法 YAML frontmatter 与正文会被分别解析，且正文中的分隔符不会截断内容。
def test_parse_frontmatter_returns_metadata_and_complete_body():
    metadata, body = skills._parse_frontmatter(
        "---\nname: demo\ndescription: 示例技能\n---\n# 步骤\n保留 --- 正文"
    )

    assert metadata == {"name": "demo", "description": "示例技能"}
    assert body == "# 步骤\n保留 --- 正文"


# 验证缺少闭合分隔行、YAML 非对象或语法错误时安全回退为空元数据，不向调用方抛出异常。
@pytest.mark.parametrize(
    ("content", "expected_body"),
    [
        ("---\nname: demo\n# 没有闭合分隔行", ""),
        ("---\n- list item\n---\n正文", "正文"),
        ("---\nname: [\n---\n正文", "正文"),
    ],
)
def test_parse_frontmatter_tolerates_malformed_metadata(content, expected_body):
    metadata, body = skills._parse_frontmatter(content)

    assert metadata == {}
    assert body == expected_body


# 验证扫描会读取 UTF-8 技能、采用元数据名称与简介，并忽略缺少 SKILL.md 的普通目录。
def test_scan_skills_discovers_manifests(isolated_skill_registry):
    manifest = _write_skill(
        isolated_skill_registry,
        "writer",
        "---\nname: chinese-writer\ndescription: 中文写作助手\n---\n# 使用说明\n先分析需求。",
    )
    (isolated_skill_registry / "empty").mkdir(parents=True)

    assert skills.scan_skills() == 1
    assert list(skills.SKILL_REGISTRY) == ["chinese-writer"]
    assert skills.SKILL_REGISTRY["chinese-writer"] == {
        "name": "chinese-writer",
        "description": "中文写作助手",
        "content": manifest.read_text(encoding="utf-8"),
    }


# 验证名称和简介缺失时分别使用目录名及正文首行，并在重新扫描时移除已经删除的旧技能。
def test_scan_skills_uses_fallbacks_and_clears_stale_entries(
    isolated_skill_registry,
):
    manifest = _write_skill(
        isolated_skill_registry,
        "fallback",
        "# 兜底简介\n\n详细内容",
    )

    assert skills.scan_skills() == 1
    assert skills.SKILL_REGISTRY["fallback"]["description"] == "兜底简介"

    manifest.unlink()
    assert skills.scan_skills() == 0
    assert skills.SKILL_REGISTRY == {}


# 验证技能目录使用名称排序且完整说明按需加载，不存在或空名称会返回统一错误文本。
def test_list_and_load_skills(isolated_skill_registry):
    _write_skill(
        isolated_skill_registry,
        "z-directory",
        "---\nname: zeta\ndescription: 最后一个\n---\nZ 内容",
    )
    _write_skill(
        isolated_skill_registry,
        "a-directory",
        "---\nname: alpha\ndescription: 第一个\n---\nA 内容",
    )
    skills.scan_skills()

    assert skills.list_skills().splitlines() == [
        "- **alpha**: 第一个",
        "- **zeta**: 最后一个",
    ]
    assert "A 内容" in skills.load_skill("alpha")
    assert skills.load_skill("missing") == "Error: skill not found: missing"
    assert skills.load_skill(" ") == "Error: skill name must be a non-empty string"


# 验证系统提示词会先扫描技能，仅注入简短目录，并提示模型通过 load_skill 获取完整内容。
def test_build_system_scans_and_includes_skill_catalog(isolated_skill_registry):
    _write_skill(
        isolated_skill_registry,
        "reviewer",
        "---\nname: reviewer\ndescription: 审查代码\n---\n这是不应提前注入的完整说明。",
    )

    system = prompt.build_system()

    assert "- **reviewer**: 审查代码" in system
    assert "这是不应提前注入的完整说明。" not in system
    assert "use load_skill" in system


# 验证 load_skill 工具声明限制名称参数，且处理器映射保存函数本身而不是在导入阶段提前调用。
def test_load_skill_tool_schema_and_handler_are_registered():
    skill_tool = next(
        tool for tool in tools_config.TOOLS if tool["name"] == "load_skill"
    )
    schema = skill_tool["input_schema"]

    assert tools_config.TOOL_HANDLERS["load_skill"] is skills.load_skill
    assert schema["required"] == ["name"]
    assert schema["additionalProperties"] is False
    assert schema["properties"]["name"]["minLength"] == 1
