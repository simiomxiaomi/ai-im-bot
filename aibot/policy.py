"""触发策略：白/黑名单、群 @、关键词、安静时段、冷却与限流。

策略只在「是否要让 AI 介入」这一层做判断，不负责发送，也不负责记日志。
"""

from __future__ import annotations

import time
from collections import deque
from dataclasses import dataclass

from .config import PolicyConfig, _parse_hour_range
from .models import IncomingMessage


@dataclass(slots=True)
class Decision:
    allowed: bool
    reason: str = ""


class PolicyEngine:
    def __init__(self, cfg: PolicyConfig) -> None:
        self.cfg = cfg
        self._recent_global: deque[float] = deque()
        self._recent_sessions: dict[str, deque[float]] = {}
        self._last_reply: dict[str, float] = {}
        self._now = time.time

    def check(self, msg: IncomingMessage, now: float | None = None) -> Decision:
        now = self._now() if now is None else now
        cfg = self.cfg

        # 1. 黑白名单（黑名单优先，且无论群聊私聊都生效）
        if msg.sender_id in cfg.block_users:
            return Decision(False, "发送者在黑名单")
        if msg.session_type == "group" and msg.session_id in cfg.block_groups:
            return Decision(False, "群在黑名单")

        # 2. 会话类型开关
        if msg.session_type == "private":
            if cfg.private == "off":
                return Decision(False, "私聊自动回复已关闭")
            if cfg.private == "whitelist" and msg.sender_id not in cfg.allow_users:
                return Decision(False, "发送者不在私聊白名单")
        else:
            if cfg.group == "off":
                return Decision(False, "群聊自动回复已关闭")
            if cfg.allow_groups and msg.session_id not in cfg.allow_groups:
                return Decision(False, "群不在白名单")
            if cfg.group == "mention" and not msg.is_at_me:
                return Decision(False, "群聊未 @ 机器人")

        # 3. 关键词（配置了就必须命中）
        if cfg.keywords and not any(k and k in msg.text for k in cfg.keywords):
            return Decision(False, "未命中关键词")

        # 4. 安静时段
        if cfg.quiet_hours and _in_quiet_hours(now, cfg.quiet_hours):
            return Decision(False, "处于安静时段")

        # 5. 冷却与限流
        key = msg.session_key
        last = self._last_reply.get(key)
        if last is not None and now - last < cfg.cooldown_seconds:
            return Decision(False, f"会话冷却中（{cfg.cooldown_seconds:g}s）")

        global_window = _trim(self._recent_global, now - 60)
        if len(global_window) >= cfg.max_per_minute:
            return Decision(False, "达到全局每分钟上限")

        session_window = _trim(self._recent_sessions.get(key, deque()), now - 3600)
        if len(session_window) >= cfg.max_per_session_per_hour:
            return Decision(False, "达到该会话每小时上限")

        return Decision(True, "ok")

    def note_reply(self, session_key: str, now: float | None = None) -> None:
        now = self._now() if now is None else now
        self._last_reply[session_key] = now
        self._recent_global.append(now)
        self._recent_sessions.setdefault(session_key, deque()).append(now)

    def snapshot(self) -> dict:
        now = self._now()
        _trim(self._recent_global, now - 60)
        return {
            "replies_last_minute": len(self._recent_global),
            "max_per_minute": self.cfg.max_per_minute,
            "active_sessions": len(self._last_reply),
        }

    # 测试用：注入可控时钟
    def set_clock(self, fn) -> None:
        self._now = fn


def _trim(window: deque[float], threshold: float) -> deque[float]:
    while window and window[0] < threshold:
        window.popleft()
    return window


def _in_quiet_hours(now: float, ranges: list[str]) -> bool:
    local = time.localtime(now)
    minute = local.tm_hour * 60 + local.tm_min
    for expr in ranges:
        parsed = _parse_hour_range(expr)
        if not parsed:
            continue
        start, end = parsed
        if start == end:
            continue
        if start < end:
            if start <= minute < end:
                return True
        else:  # 跨午夜，如 23:30-08:00
            if minute >= start or minute < end:
                return True
    return False
