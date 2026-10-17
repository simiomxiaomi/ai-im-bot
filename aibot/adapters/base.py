"""适配器接口：把不同平台收敛成「收消息 / 发消息」两件事。"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Awaitable, Callable

from ..models import IncomingMessage

MessageHandler = Callable[[IncomingMessage], Awaitable[None]]


class AdapterError(RuntimeError):
    pass


class Adapter(ABC):
    name: str = "base"
    self_id: str = ""

    @abstractmethod
    async def start(self, on_message: MessageHandler) -> None:
        """启动适配器。应当阻塞到 stop() 被调用，或自行维持重连循环。"""

    @abstractmethod
    async def send_text(
        self,
        session_type: str,
        session_id: str,
        text: str,
        at_user: str | None = None,
        reply_to: str | None = None,
    ) -> bool:
        """发送文本，返回是否成功。"""

    @abstractmethod
    async def stop(self) -> None:
        """释放资源，停止重连。"""

    async def health(self) -> dict:
        return {"name": self.name, "connected": False, "self_id": self.self_id}
