"""群助手：固定命令（帮助 / 天气 / 每日一句），其余交给 AI。

从 EverydayWechat 的 group_helper 迁移而来，去掉了脆弱的十几条正则，
只保留最常用、最稳定的三条，自然语言交给大模型。
"""

from __future__ import annotations

import logging
import re

from .config import Config
from .models import IncomingMessage
from .services import hitokoto, weather

log = logging.getLogger("aibot.group")

_AT_PREFIX = re.compile(r"^\s*@[^\s]+\s*")
_HELP = re.compile(r"^(帮助|help|菜单|功能|能用什么|0)$", re.I)
_WEATHER = re.compile(r"^(天气|weather)\s*(.*)$", re.I)
_HITOKOTO = re.compile(r"^(每日一句|一言|hitokoto|毒鸡汤)$", re.I)


class GroupHelper:
    def __init__(self, cfg: Config) -> None:
        self.cfg = cfg

    async def handle(self, msg: IncomingMessage) -> str | None:
        if msg.session_type != "group":
            return None
        conf = self.cfg.group
        if not conf.enabled:
            return None

        text = _AT_PREFIX.sub("", msg.text or "").strip()
        if not text:
            return None

        if _HELP.match(text):
            return conf.help_text.format(name=msg.sender_name)

        match = _WEATHER.match(text)
        if match and conf.weather_enabled:
            city = match.group(2).strip()
            if not city:
                return "要查哪个城市？例如：天气 北京"
            result = await weather(city)
            if result:
                return result
            return f"没查到「{city}」的天气，换个城市名试试？"

        if _HITOKOTO.match(text) and conf.hitokoto_enabled:
            result = await hitokoto()
            return result or "一言服务暂时没响应，稍后再试。"

        return None
