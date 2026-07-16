import os
from pathlib import Path

from anthropic import Anthropic
from dotenv import load_dotenv

# 本文件负责加载环境变量，并集中定义工作区路径、Anthropic 客户端、模型参数和命令权限配置。

# 在模块加载时记录工作区的规范化绝对路径，后续文件工具和系统提示词共用该访问边界。
WORKDIR = Path.cwd().resolve()

# 保存完整会话转录的目录，压缩消息历史前会在此写入可追溯的 JSONL 文件。
TRANSCRIPT_DIR = WORKDIR / ".transcripts"

# 保存超长工具结果的目录，发送给模型的消息仅保留文件路径和内容预览。
TOOL_RESULTS_DIR = WORKDIR / ".task_outputs" / "tool-results"

# 优先读取项目的环境变量文件，使本地配置可以覆盖当前进程中的同名变量。
load_dotenv(override=True)

# 使用自定义接口地址时移除可能冲突的认证令牌，让客户端按照当前接口配置完成鉴权。
if os.getenv("ANTHROPIC_BASE_URL"):
    os.environ.pop("ANTHROPIC_AUTH_TOKEN", None)

# 根据可选的自定义接口地址创建全局客户端，供运行时请求和历史摘要共同使用。
client = Anthropic(base_url=os.getenv("ANTHROPIC_BASE_URL"))

# 从环境变量取得运行时和历史摘要共同使用的模型编号。
MODEL = os.environ["MODEL_ID"]

# 配置 Shell 命令硬禁止列表；命中任意片段时不向用户询问，直接拒绝执行。
DENY_LIST = ["rm -rf /", "sudo", "shutdown", "reboot", "mkfs", "dd if="]

# 配置需要人工确认的破坏性命令片段；只有用户明确输入 y 或 yes 后才会放行。
DESTRUCTIVE = ["rm ", "> /etc/", "chmod 777"]
