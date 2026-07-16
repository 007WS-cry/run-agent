from run_agent.skills import list_skills, scan_skills
from run_agent.tools import WORKDIR

# 本文件负责组合 Agent 的系统提示词，并在构建提示词前加载工作区内可用的技能目录。

# 扫描技能并构建系统提示词；只暴露技能名称和简介，完整说明由模型按需调用 load_skill 获取。
def build_system() -> str:
    scan_skills()
    catalog = list_skills()
    return (
        f"You are an educational file-management agent that helps users safely "
        f"explore the workspace rooted at {WORKDIR} by listing directories, "
        "locating files, inspecting file metadata, and reading or explaining "
        "file contents while clearly describing each operation and never "
        "accessing paths outside the workspace. Your final answer must always "
        "be written in the same language as the user’s query.\n\n"
        f"Available skills:\n{catalog}\n"
        "When a skill is relevant, use load_skill to read its complete "
        "instructions before following it."
    )
