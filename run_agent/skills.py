import yaml

from run_agent.config import WORKDIR

# 本文件负责发现工作区 resources/skills 下的技能清单、解析 YAML frontmatter，并按需返回完整技能说明。

# 定义技能根目录；每个直接子目录可通过其中的 SKILL.md 声明一个技能。
SKILLS_DIR = WORKDIR / "resources" / "skills"

# 保存本次扫描得到的技能元数据和原始说明，键为技能名称；重新扫描时会整体清空并重建。
SKILL_REGISTRY: dict[str, dict[str, str]] = {}


# 解析 SKILL.md 开头由独立分隔行包围的 YAML frontmatter，并同时返回去除 frontmatter 后的正文。
def _parse_frontmatter(text: str) -> tuple[dict, str]:
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return {}, text.strip()

    closing_index = next(
        (index for index, line in enumerate(lines[1:], start=1) if line.strip() == "---"),
        None,
    )
    if closing_index is None:
        return {}, ""

    frontmatter = "\n".join(lines[1:closing_index])
    body = "\n".join(lines[closing_index + 1:]).strip()
    try:
        metadata = yaml.safe_load(frontmatter) or {}
    except yaml.YAMLError:
        metadata = {}
    if not isinstance(metadata, dict):
        metadata = {}
    return metadata, body


# 从技能正文提取首个非空标题或文本作为兜底简介，正文为空时返回统一的缺省说明。
def _fallback_description(body: str) -> str:
    for line in body.splitlines():
        description = line.strip().lstrip("#").strip()
        if description:
            return description
    return "No description provided."


# 扫描技能根目录并重建注册表；跳过链接、不可读文件和非法目录，使单个坏技能不会阻止程序启动。
def scan_skills() -> int:
    SKILL_REGISTRY.clear()
    if not SKILLS_DIR.is_dir() or SKILLS_DIR.is_symlink():
        return 0

    try:
        skill_directories = sorted(SKILLS_DIR.iterdir(), key=lambda path: path.name)
    except OSError:
        return 0

    for directory in skill_directories:
        if not directory.is_dir() or directory.is_symlink():
            continue
        manifest = directory / "SKILL.md"
        if not manifest.is_file() or manifest.is_symlink():
            continue

        try:
            raw = manifest.read_text(encoding="utf-8-sig")
        except (OSError, UnicodeError):
            continue

        metadata, body = _parse_frontmatter(raw)
        metadata_name = metadata.get("name")
        name = (
            metadata_name.strip()
            if isinstance(metadata_name, str) and metadata_name.strip()
            else directory.name
        )
        metadata_description = metadata.get("description")
        description = (
            metadata_description.strip()
            if isinstance(metadata_description, str) and metadata_description.strip()
            else _fallback_description(body)
        )
        SKILL_REGISTRY[name] = {
            "name": name,
            "description": description,
            "content": raw,
        }
    return len(SKILL_REGISTRY)


# 将已发现技能格式化为稳定排序的简短目录，供系统提示词展示且避免提前注入完整说明。
def list_skills() -> str:
    if not SKILL_REGISTRY:
        return "- (none)"
    return "\n".join(
        f"- **{skill['name']}**: {skill['description']}"
        for skill in sorted(SKILL_REGISTRY.values(), key=lambda item: item["name"])
    )


# 按目录中的精确名称加载技能原始说明；名称无效或不存在时返回统一错误文本供模型处理。
def load_skill(name: str) -> str:
    if not isinstance(name, str) or not name.strip():
        return "Error: skill name must be a non-empty string"

    normalized_name = name.strip()
    skill = SKILL_REGISTRY.get(normalized_name)
    if not skill:
        return f"Error: skill not found: {normalized_name}"
    return skill["content"]
