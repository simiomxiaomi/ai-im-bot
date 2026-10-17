"""适配器接口与实现。

这里只导出接口；具体适配器由调用方按需导入，
避免仅导入 base 时把 websockets 等可选依赖也一起拖进来。
"""

from .base import Adapter, AdapterError, MessageHandler

__all__ = ["Adapter", "AdapterError", "MessageHandler"]
