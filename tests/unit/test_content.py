from run_agent import memories
from run_agent.content import extract_text

# 本文件对公共内容块文本提取函数进行单元测试，验证多种输入形式以及旧模块导入入口的兼容性。


# 验证空值和普通值会转换为稳定字符串，使非标准模型响应也能由调用方安全处理。
def test_extract_text_handles_empty_and_scalar_content():
    assert extract_text(None) == ""
    assert extract_text("plain text") == "plain text"


# 验证字典与 SDK 文本块会按顺序合并，同时忽略工具调用等非文本内容块。
def test_extract_text_supports_dict_and_sdk_blocks(content_block_factory):
    content = [
        {"type": "text", "text": "first"},
        content_block_factory("tool_use", id="ignored"),
        content_block_factory("text", text="second"),
    ]

    assert extract_text(content) == "first\nsecond"


# 验证 memories 模块继续暴露原有函数名称，避免公共实现迁移后破坏既有导入方式。
def test_memories_keeps_extract_text_compatibility_alias():
    assert memories.extract_text is extract_text
