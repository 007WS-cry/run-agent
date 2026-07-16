import os
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

# 本文件为全部测试提供公共夹具和模拟对象，用临时工作区隔离文件操作，并替换模型客户端以避免真实网络请求。

# 导入运行时模块会同时加载配置，因此预先提供无害默认值，避免测试依赖真实密钥或模型配置。
os.environ.setdefault("ANTHROPIC_API_KEY", "test-api-key")
os.environ.setdefault("MODEL_ID", "test-model")

# 模拟 Anthropic SDK 的内容块对象，使测试既能使用属性访问，也能验证模型对象到字典的转换过程。
class FakeContentBlock(SimpleNamespace):
    # 将模拟内容块导出为字典；复制实例属性并按参数过滤空值，以复现 SDK 的序列化行为。
    def model_dump(self, exclude_none=True):
        values = vars(self).copy()
        if exclude_none:
            values = {key: value for key, value in values.items() if value is not None}
        return values

@pytest.fixture
# 创建隔离的文件工具工作区；利用 pytest 临时目录并替换模块常量，使每个测试只操作自己的目录。
def workspace(tmp_path, monkeypatch):
    from run_agent import tools

    root = tmp_path.resolve()
    monkeypatch.setattr(tools, "WORKDIR", root)
    return root

@pytest.fixture
# 提供内容块工厂夹具；通过闭包接收内容类型和动态字段，批量构造结构一致的模拟 SDK 对象。
def content_block_factory():
    # 构造单个模拟内容块；把类型与附加字段合并为可通过属性访问的对象。
    def make_block(block_type, **values):
        return FakeContentBlock(type=block_type, **values)

    return make_block

@pytest.fixture
# 提供模型响应工厂夹具；通过闭包组合内容列表和停止原因，生成运行时循环需要的最小响应对象。
def response_factory():
    # 构造单次模拟模型响应；将内容和停止原因保存为属性供 Agent 循环读取。
    def make_response(*, content, stop_reason="end_turn"):
        return SimpleNamespace(content=content, stop_reason=stop_reason)

    return make_response

@pytest.fixture
# 模拟模型消息创建方法；用 Mock 替换运行时客户端，使测试能够预设响应并检查调用且不会访问网络。
def mocked_message_create(monkeypatch):
    from run_agent import runtime

    create = Mock()
    fake_client = SimpleNamespace(messages=SimpleNamespace(create=create))
    monkeypatch.setattr(runtime, "client", fake_client)
    return create
