"""消息处理管线：去重 → 策略 → 上下文 → 模型 → 发送 → 落库。"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass

from .adapters.base import Adapter
from .config import Config
from .llm import LLMClient, LLMError
from .models import IncomingMessage, clean_reply
from .policy import PolicyEngine
from .store import Store

log = logging.getLogger("aibot.pipeline")


@dataclass(slots=True)
class HandleResult:
    accepted: bool
    reason: str
    reply: str = ""
    sent: bool = False
    dry_run: bool = False


class Pipeline:
    def __init__(
        self,
        cfg: Config,
        store: Store,
        llm: LLMClient,
        adapter: Adapter,
        policy: PolicyEngine,
    ) -> None:
        self.cfg = cfg
        self.store = store
        self.llm = llm
        self.adapter = adapter
        self.policy = policy
        self._locks: dict[str, asyncio.Lock] = {}
        self._sem = asyncio.Semaphore(4)
        self._handled = 0
        # 可选：群助手等固定命令处理器，签名 async (msg) -> str | None
        self.command_handler = None

    # ------------------------------------------------------------------ 开关

    @property
    def auto_reply(self) -> bool:
        return self.store.get_bool("auto_reply", True)

    def set_auto_reply(self, enabled: bool) -> None:
        self.store.set_setting("auto_reply", "1" if enabled else "0")
        log.warning("自动回复已%s", "开启" if enabled else "关闭（人工接管）")

    # ------------------------------------------------------------------ 主流程

    async def handle(self, msg: IncomingMessage) -> HandleResult:
        if self.adapter.self_id and msg.sender_id == self.adapter.self_id:
            return HandleResult(False, "忽略自己发送的消息")

        lock = self._locks.setdefault(msg.session_key, asyncio.Lock())
        async with lock:
            if msg.message_id and self.store.seen(msg.platform, msg.message_id):
                return HandleResult(False, "重复消息")

            if not self.auto_reply:
                self.store.record_incoming(msg, False, "自动回复已暂停")
                return HandleResult(False, "自动回复已暂停")

            decision = self.policy.check(msg)
            self.store.record_incoming(msg, decision.allowed, decision.reason)
            label = f"{msg.session_type}:{msg.session_name}({msg.session_id})"
            if not decision.allowed:
                log.info("拦截 %s [%s] %s → %s", label, msg.sender_name, _short(msg.text), decision.reason)
                return HandleResult(False, decision.reason)

            log.info("回复 %s [%s] %s", label, msg.sender_name, _short(msg.text))
            started = time.monotonic()
            reply: str | None = None

            # 先让群助手处理固定命令（帮助/天气/每日一句），命中就不调用模型
            if self.command_handler is not None:
                try:
                    reply = await self.command_handler(msg)
                except Exception:  # noqa: BLE001
                    log.exception("命令处理失败，转交模型")

            if reply is None:
                try:
                    async with self._sem:
                        reply = await self.llm.chat(self._build_messages(msg))
                except LLMError as exc:
                    log.error("模型调用失败：%s", exc)
                    fallback = self.cfg.reply.fallback.strip()
                    if not fallback:
                        self.store.record_outgoing(
                            msg.platform, msg.session_type, msg.session_id,
                            "", msg.session_name, reason=f"AI 失败：{exc}"[:200],
                        )
                        return HandleResult(True, f"AI 调用失败：{exc}")
                    reply = fallback
                except Exception as exc:  # noqa: BLE001 - 任何异常都不能让进程挂掉
                    log.exception("生成回复时出现未预期错误")
                    return HandleResult(True, f"内部错误：{exc}")

            reply = clean_reply(reply, self.cfg.llm.max_reply_chars)
            if not reply:
                return HandleResult(True, "模型返回空内容")

            out_text = f"{self.cfg.reply.prefix}{reply}{self.cfg.reply.suffix}"
            at_user = msg.sender_id if (msg.session_type == "group" and self.cfg.reply.group_at_sender) else None

            if self.cfg.reply.dry_run:
                self.store.record_outgoing(
                    msg.platform, msg.session_type, msg.session_id, out_text,
                    msg.session_name, reason="dry_run（未发送）",
                )
                self.policy.note_reply(msg.session_key)
                log.warning("[dry_run] 未发送：%s", _short(out_text, 80))
                return HandleResult(True, "dry_run", out_text, sent=False, dry_run=True)

            sent = await self.adapter.send_text(
                msg.session_type, msg.session_id, out_text, at_user=at_user, reply_to=msg.message_id
            )
            if sent:
                self.policy.note_reply(msg.session_key)
                self.store.add_turn(
                    msg.session_key, _prompt_user_text(msg), reply
                )
            self.store.record_outgoing(
                msg.platform, msg.session_type, msg.session_id, out_text,
                msg.session_name, reason="" if sent else "发送失败",
            )
            elapsed = int((time.monotonic() - started) * 1000)
            self._handled += 1
            log.info("已回复 %s（%dms，%d 字）", label, elapsed, len(out_text))
            return HandleResult(True, "已回复" if sent else "发送失败", out_text, sent=sent)

    # ------------------------------------------------------------------ 组装上下文

    def _build_messages(self, msg: IncomingMessage) -> list[dict[str, str]]:
        system = self.cfg.llm.system_prompt.strip()
        now = time.strftime("%Y-%m-%d %H:%M", time.localtime())
        where = f"群聊「{msg.session_name}」" if msg.session_type == "group" else "私聊"
        system += (
            f"\n\n当前时间：{now}"
            f"\n当前会话：{where}，对方昵称：{msg.sender_name}（QQ/ID: {msg.sender_id}）"
        )
        messages: list[dict[str, str]] = [{"role": "system", "content": system}]
        messages.extend(self.store.history(msg.session_key, self.cfg.llm.history_turns))
        messages.append({"role": "user", "content": _prompt_user_text(msg)})
        return messages

    def snapshot(self) -> dict:
        return {
            "handled": self._handled,
            "auto_reply": self.auto_reply,
            "dry_run": self.cfg.reply.dry_run,
            "policy": self.policy.snapshot(),
        }


def _prompt_user_text(msg: IncomingMessage) -> str:
    if msg.session_type == "group":
        return f"{msg.sender_name}：{msg.text}"
    return msg.text


def _short(text: str, limit: int = 60) -> str:
    text = (text or "").replace("\n", " ")
    return text if len(text) <= limit else text[: limit - 1] + "…"
