"""应用组装与生命周期。"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

from .adapters.base import Adapter
from .adapters.mock import MockAdapter
from .adapters.onebot import OneBotAdapter
from .config import Config
from .greeting import Greeter
from .group_helper import GroupHelper
from .llm import LLMClient
from .models import IncomingMessage
from .pipeline import Pipeline
from .policy import PolicyEngine
from .store import Store

log = logging.getLogger("aibot.app")


class Application:
    def __init__(self, cfg: Config) -> None:
        self.cfg = cfg
        self.started_at = time.time()
        self.store = Store(cfg.db_path, history_limit=max(200, cfg.llm.history_turns * 10))
        self.llm = LLMClient(cfg.llm)
        self.adapter = build_adapter(cfg)
        self.policy = PolicyEngine(cfg.policy)
        self.pipeline = Pipeline(cfg, self.store, self.llm, self.adapter, self.policy)
        self.pipeline.command_handler = GroupHelper(cfg).handle
        self.greeter = Greeter(cfg, self.store, self.llm, self.adapter)
        self.loop: asyncio.AbstractEventLoop | None = None
        self.web: Any = None
        self._health: dict[str, Any] = {"name": self.adapter.name, "connected": False}
        self._stop = asyncio.Event()
        self._adapter_task: asyncio.Task | None = None
        self._health_task: asyncio.Task | None = None
        self._greeter_task: asyncio.Task | None = None

    # ------------------------------------------------------------------ 生命周期

    async def run(self) -> None:
        self.loop = asyncio.get_running_loop()
        log.info("启动：平台=%s，模型=%s", self.cfg.platform_type, self.cfg.llm.model)
        if self.pipeline.auto_reply:
            log.warning("自动回复当前为开启状态；随时可在 Web 控制台暂停")

        if self.cfg.web.enabled:
            from .web.server import WebServer

            self.web = WebServer(self.cfg, self)
            self.web.start()
            log.info("Web 控制台：http://%s:%d/?token=%s",
                     self.cfg.web.host, self.cfg.web.port, self.cfg.web.token or "(未设置)")

        self._health_task = asyncio.create_task(self._health_loop())
        self._adapter_task = asyncio.create_task(self._run_adapter())
        if self.cfg.greeting.enabled and self.cfg.greeting.items:
            self._greeter_task = asyncio.create_task(self.greeter.run())

        stop_task = asyncio.create_task(self._stop.wait())
        done, _ = await asyncio.wait(
            {self._adapter_task, stop_task}, return_when=asyncio.FIRST_COMPLETED
        )
        if self._adapter_task in done:
            exc = self._adapter_task.exception()
            if exc:
                log.error("适配器退出：%s", exc)
        stop_task.cancel()
        await self.shutdown()

    async def _run_adapter(self) -> None:
        assert self.adapter is not None
        await self.adapter.start(self.pipeline.handle)

    async def _health_loop(self) -> None:
        while True:
            try:
                self._health = await self.adapter.health()
            except Exception:  # noqa: BLE001
                log.debug("获取适配器状态失败", exc_info=True)
            await asyncio.sleep(5)

    async def shutdown(self) -> None:
        log.info("正在停止…")
        self._stop.set()
        if self._health_task:
            self._health_task.cancel()
        if self._greeter_task:
            self._greeter_task.cancel()
        try:
            await self.adapter.stop()
        except Exception:  # noqa: BLE001
            log.debug("停止适配器时出错", exc_info=True)
        if self.web:
            self.web.stop()
        await self.llm.close()
        self.store.close()
        log.info("已停止")

    def request_stop(self) -> None:
        if self.loop:
            self.loop.call_soon_threadsafe(self._stop.set)

    # ------------------------------------------------------------------ Web 调用

    def submit(self, coro):
        """从 Web 线程提交协程到主事件循环。"""
        if self.loop is None:
            raise RuntimeError("事件循环尚未启动")
        return asyncio.run_coroutine_threadsafe(coro, self.loop)

    async def inject_message(
        self,
        *,
        session_type: str,
        session_id: str,
        text: str,
        sender_name: str = "测试用户",
        sender_id: str = "",
        is_at_me: bool = True,
    ) -> dict:
        if session_type not in {"private", "group"}:
            return {"ok": False, "error": "session_type 只能是 private/group"}
        msg = IncomingMessage(
            platform=self.adapter.name,
            message_id=f"web-{int(time.time() * 1000)}",
            session_type=session_type,  # type: ignore[arg-type]
            session_id=str(session_id),
            session_name=str(session_id),
            sender_id=str(sender_id or session_id),
            sender_name=sender_name,
            text=text,
            is_at_me=is_at_me,
        )
        result = await self.pipeline.handle(msg)
        return {
            "ok": True,
            "accepted": result.accepted,
            "reason": result.reason,
            "reply": result.reply,
            "sent": result.sent,
        }

    async def send_manual(self, session_type: str, session_id: str, text: str) -> dict:
        ok = await self.adapter.send_text(session_type, session_id, text)
        if ok:
            self.store.record_outgoing(
                self.adapter.name, session_type, session_id, text, reason="手动发送"
            )
        return {"ok": ok}

    def status(self) -> dict:
        return {
            "running": not self._stop.is_set(),
            "uptime_seconds": int(time.time() - self.started_at),
            "platform": self.cfg.platform_type,
            "adapter": self._health,
            "pipeline": self.pipeline.snapshot(),
            "stats": self.store.stats(),
            "model": self.cfg.llm.model,
            "base_url": self.cfg.llm.base_url,
            "llm_usage": self.llm.last_usage,
            "greeting": {
                "enabled": self.cfg.greeting.enabled,
                "items": len(self.cfg.greeting.items),
            },
            "web": {"host": self.cfg.web.host, "port": self.cfg.web.port},
            "warnings": self.cfg.warnings,
        }


def build_adapter(cfg: Config) -> Adapter:
    if cfg.platform_type == "mock":
        return MockAdapter()
    if cfg.platform_type == "wechat":
        from .adapters.wechat_uia import WeChatUIAAdapter

        return WeChatUIAAdapter(cfg.wechat)
    return OneBotAdapter(cfg.onebot)
