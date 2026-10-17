"""轻量外部数据源：天气 / 一言。失败返回 None，不阻塞主流程。"""

from __future__ import annotations

import logging

import httpx

log = logging.getLogger("aibot.services")

_TIMEOUT = httpx.Timeout(8.0, connect=5.0)


async def weather(city: str) -> str | None:
    """wttr.in 一行天气，例如：北京: ⛅️ +18°C。"""
    city = (city or "").strip()
    if not city:
        return None
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            resp = await client.get(
                f"https://wttr.in/{city}",
                params={"format": "3", "lang": "zh"},
                headers={"User-Agent": "curl/8.0"},
            )
        if resp.status_code != 200:
            log.warning("天气查询失败：HTTP %s", resp.status_code)
            return None
        text = (resp.text or "").strip()
        return text[:120] if text and "Unknown location" not in text else None
    except Exception as exc:  # noqa: BLE001
        log.warning("天气查询异常：%s", exc)
        return None


async def hitokoto() -> str | None:
    """一言（hitokoto），失败返回 None。"""
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            resp = await client.get(
                "https://v1.hitokoto.cn/",
                params={"encode": "text", "charset": "utf-8"},
            )
        if resp.status_code != 200:
            return None
        text = (resp.text or "").strip()
        return text[:200] if text else None
    except Exception as exc:  # noqa: BLE001
        log.warning("一言查询异常：%s", exc)
        return None
