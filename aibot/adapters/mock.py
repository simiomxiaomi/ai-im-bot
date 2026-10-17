"""本地模拟适配器：不连 QQ 也能把整条链路跑通。"""

from __future__ import annotations

import asyncio
import itertools
import logging
import time

from ..models import IncomingMessage
from .base import Adapter, MessageHandler

log = logging.getLogger("aibot.mock")


class MockAdapter(Adapter):
    name = "mock"
    self_id = "10000"

    def __init__(self) -> None:
        self._handler: MessageHandler | None = None
        self._stop = asyncio.Event()
        self._seq = itertools.count(1)
        self.sent: list[dict] = []

    async def start(self, on_message: MessageHandler) -> None:
        self._handler = on_message
        log.info("Mock 适配器已启动：可在 Web 控制台里发测试消息")
        await self._stop.wait()

    async def stop(self) -> None:
        self._stop.set()

    async def send_text(
        self,
        session_type: str,
        session_id: str,
        text: str,
        at_user: str | None = None,
        reply_to: str | None = None,
    ) -> bool:
        record = {
            "time": time.strftime("%H:%M:%S"),
            "session_type": session_type,
            "session_id": session_id,
            "at_user": at_user,
            "text": text,
        }
        self.sent.append(record)
        log.info("[模拟发送] %s:%s%s → %s", session_type, session_id,
                 f" @{at_user}" if at_user else "", text)
        return True

    async def inject(
        self,
        text: str,
        *,
        session_type: str = "private",
        session_id: str = "10001",
        sender_name: str = "测试用户",
        sender_id: str = "20001",
        is_at_me: bool = True,
    ) -> IncomingMessage | None:
        if self._handler is None:
            return None
        msg = IncomingMessage(
            platform="mock",
            message_id=f"mock-{next(self._seq)}",
            session_type=session_type,  # type: ignore[arg-type]
            session_id=session_id,
            session_name=sender_name if session_type == "private" else f"测试群{session_id}",
            sender_id=sender_id,
            sender_name=sender_name,
            text=text,
            is_at_me=is_at_me,
        )
        await self._handler(msg)
        return msg
