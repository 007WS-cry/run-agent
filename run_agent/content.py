# 本文件提供 Anthropic 响应内容的通用文本提取能力，供摘要、记忆和 Subagent 等模型调用流程复用。


# 兼容字典和 Anthropic SDK 对象两种内容块，只按原始顺序拼接其中的文本内容。
def extract_text(content) -> str:
    if content is None:
        return ""
    if not isinstance(content, list):
        return str(content)

    text_blocks = []
    for block in content:
        if isinstance(block, dict):
            if block.get("type") == "text":
                text_blocks.append(str(block.get("text", "")))
        elif getattr(block, "type", None) == "text":
            text_blocks.append(str(getattr(block, "text", "")))
    return "\n".join(text_blocks)
