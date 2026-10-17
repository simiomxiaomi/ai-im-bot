"""定时问候：从 EverydayWechat 的 alarm_info 迁移而来。

- cron 时间 + 随机抖动（同一天抖动固定，不会每次轮询都变）
- mode: ai（模型生成）/ fixed（模板拼接）
- 发送记录落 SQLite，重启不重复发
- 错过 15 分钟不补发，避免关机一晚第二天炸出一堆
"""

from __future__ import annotations

import asyncio
import logging
import random
import re
from datetime import datetime, timedelta

from .adapters.base import Adapter
from .config import Config, GreetingItem
from .llm import LLMClient
from .models import clean_reply
from .services import weather
from .store import Store

log = logging.getLogger("aibot.greeting")

WEEKDAYS = "一二三四五六日"
MISS_WINDOW_SECONDS = 900
MAX_FAILURES = 3
_TIME_RE = re.compile(r"^\s*(\d{1,2}):(\d{2})\s*$")


def _parse_time(value: str) -> tuple[int, int] | None:
    match = _TIME_RE.match(str(value))
    if not match:
        return None
    hour, minute = int(match.group(1)), int(match.group(2))
    if hour > 23 or minute > 59:
        return None
    return hour, minute


class Greeter:
    def __init__(self, cfg: Config, store: Store, llm: LLMClient, adapter: Adapter) -> None:
        self.cfg = cfg
        self.store = store
        self.llm = llm
        self.adapter = adapter

    async def run(self) -> None:
        items = self.cfg.greeting.items
        log.info("定时问候已启动：%d 条规则", len(items))
        for item in items:
            log.info("  · %s @ %s（抖动 %ds，%s）", item.name, item.time, item.jitter, item.mode)
        while True:
            try:
                await self.tick()
            except Exception:  # noqa: BLE001 - 定时任务不能因单次异常退出
                log.exception("问候检查失败")
            await asyncio.sleep(20)

    async def tick(self, now: datetime | None = None) -> None:
        now = now or datetime.now()
        for item in self.cfg.greeting.items:
            await self._maybe_send(item, now)

    async def _maybe_send(self, item: GreetingItem, now: datetime) -> None:
        if now.isoweekday() not in item.weekdays:
            return
        parsed = _parse_time(item.time)
        if not parsed:
            return
        base = now.replace(hour=parsed[0], minute=parsed[1], second=0, microsecond=0)
        jitter = random.Random(f"{item.name}|{now.date().isoformat()}").randint(
            0, max(0, item.jitter)
        )
        fire_at = base + timedelta(seconds=jitter)
        delta = (now - fire_at).total_seconds()
        if delta < 0 or delta > MISS_WINDOW_SECONDS:
            return

        key = f"greet:{item.name}:{now.date().isoformat()}"
        state = self.store.get_setting(key, "")
        if state == "sent":
            return
        if state.startswith("fail:"):
            try:
                if int(state.split(":", 1)[1]) >= MAX_FAILURES:
                    return
            except ValueError:
                pass

        text = await self._compose(item, now)
        if not text:
            self._mark_failure(key, state)
            return

        sent = 0
        for uid in item.friends:
            if await self.adapter.send_text("private", uid, text):
                sent += 1
                self.store.record_outgoing(
                    self.adapter.name, "private", uid, text, reason=f"定时问候 {item.name}"
                )
        for gid in item.groups:
            if await self.adapter.send_text("group", gid, text):
                sent += 1
                self.store.record_outgoing(
                    self.adapter.name, "group", gid, text, reason=f"定时问候 {item.name}"
                )

        if sent:
            self.store.set_setting(key, "sent")
            log.info("定时问候「%s」已发送 %d 个目标：%s", item.name, sent, text[:60])
        else:
            log.warning("定时问候「%s」发送失败（NapCat 未连接？）", item.name)
            self._mark_failure(key, state)

    def _mark_failure(self, key: str, state: str) -> None:
        attempts = 1
        if state.startswith("fail:"):
            try:
                attempts = int(state.split(":", 1)[1]) + 1
            except ValueError:
                attempts = 1
        self.store.set_setting(key, f"fail:{attempts}")

    async def _compose(self, item: GreetingItem, now: datetime) -> str | None:
        extra: list[str] = []
        if item.start_date:
            try:
                start = datetime.strptime(item.start_date, "%Y-%m-%d").date()
                days = (now.date() - start).days + 1
                if item.start_date_msg.count("{}") == 1:
                    extra.append(item.start_date_msg.format(days))
                else:
                    extra.append(f"认识你的第{days}天")
            except ValueError:
                log.warning("start_date 格式错误：%s", item.start_date)

        if item.weather_city:
            info = await weather(item.weather_city)
            if info:
                extra.append(info)

        date_text = f"{now.year}年{now.month}月{now.day}日 星期{WEEKDAYS[now.weekday()]}"

        if item.mode == "fixed":
            template = item.template or "{date}\n{extra}"
            text = template.format(
                date=date_text,
                extra="\n".join(extra),
                weather=extra[-1] if extra else "",
                name=item.name,
            )
            return clean_reply(text, self.cfg.llm.max_reply_chars) or None

        system = (
            "你是一个真人，正在用 QQ 给亲近的人发一条日常问候。"
            "语气自然、口语化，像随手打出来的，不要用列表，不要提 AI，控制在 30~80 字。"
        )
        user = (
            f"现在是 {date_text}。\n"
            f"对方和我的关系/备注：{item.name}\n"
            + (f"可用素材：\n" + "\n".join(f"- {e}" for e in extra) + "\n" if extra else "")
            + f"请写这条问候。要求：{item.prompt}"
        )
        try:
            reply = await self.llm.chat(
                [{"role": "system", "content": system}, {"role": "user", "content": user}],
                temperature=0.9,
            )
        except Exception as exc:  # noqa: BLE001
            log.error("生成问候失败：%s", exc)
            return None
        return clean_reply(reply, self.cfg.llm.max_reply_chars) or None
