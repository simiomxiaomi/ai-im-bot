"""定时问候与群助手测试（不依赖 QQ / NapChat 连接）。

运行：python -m unittest discover -s tests -v
"""

from __future__ import annotations

import unittest
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from aibot.greeting import Greeter
from aibot.group_helper import GroupHelper
from aibot.models import IncomingMessage


class FakeStore:
    def __init__(self) -> None:
        self.data: dict[str, str] = {}
        self.out: list[tuple] = []

    def get_setting(self, key: str, default: str = "") -> str:
        return self.data.get(key, default)

    def set_setting(self, key: str, value: str) -> None:
        self.data[key] = value

    def record_outgoing(self, *args, **kwargs) -> int:
        self.out.append((args, kwargs))
        return 1


class FakeLLM:
    def __init__(self) -> None:
        self.calls = 0

    async def chat(self, messages, **kwargs) -> str:
        self.calls += 1
        return "早呀，今天也要开心。"


class FakeAdapter:
    name = "onebot"

    def __init__(self) -> None:
        self.sent: list[tuple] = []

    async def send_text(self, session_type, session_id, text, at_user=None, reply_to=None) -> bool:
        self.sent.append((session_type, session_id, text))
        return True


def make_greeter(item, store=None, llm=None, adapter=None):
    cfg = SimpleNamespace(
        llm=SimpleNamespace(max_reply_chars=200),
        greeting=SimpleNamespace(items=[item]),
    )
    return Greeter(cfg, store or FakeStore(), llm or FakeLLM(), adapter or FakeAdapter())  # type: ignore[arg-type]


def make_item(**overrides):
    base = dict(
        name="早安", time="08:30", jitter=0, friends=["123456"], groups=[],
        mode="fixed", template="早上好", weather_city="", start_date="",
        start_date_msg="", weekdays=[1, 2, 3, 4, 5, 6, 7],
        prompt="写一条自然的早安。",
    )
    base.update(overrides)
    return SimpleNamespace(**base)


class GreetingTest(unittest.IsolatedAsyncioTestCase):
    async def test_fixed_mode_sends_once_per_day(self) -> None:
        adapter = FakeAdapter()
        greeter = make_greeter(make_item(), adapter=adapter)
        now = datetime(2026, 10, 19, 8, 31)  # 周一
        await greeter.tick(now)
        self.assertEqual(len(adapter.sent), 1)
        self.assertEqual(adapter.sent[0][1], "123456")
        await greeter.tick(now)
        self.assertEqual(len(adapter.sent), 1, "同一天不应重复发送")

    async def test_not_in_weekdays(self) -> None:
        adapter = FakeAdapter()
        greeter = make_greeter(make_item(name="周末", time="09:00", weekdays=[6, 7]), adapter=adapter)
        await greeter.tick(datetime(2026, 10, 19, 9, 1))  # 周一
        self.assertEqual(adapter.sent, [])

    async def test_missed_window_is_skipped(self) -> None:
        adapter = FakeAdapter()
        greeter = make_greeter(make_item(name="早", time="08:00"), adapter=adapter)
        await greeter.tick(datetime(2026, 10, 19, 12, 0))
        self.assertEqual(adapter.sent, [])

    async def test_ai_mode_uses_llm(self) -> None:
        llm = FakeLLM()
        adapter = FakeAdapter()
        greeter = make_greeter(make_item(mode="ai"), llm=llm, adapter=adapter)
        await greeter.tick(datetime(2026, 10, 19, 8, 31))
        self.assertEqual(llm.calls, 1)
        self.assertEqual(len(adapter.sent), 1)


class GroupHelperTest(unittest.IsolatedAsyncioTestCase):
    def cfg(self):
        return SimpleNamespace(
            group=SimpleNamespace(
                enabled=True, weather_enabled=True, hitokoto_enabled=True,
                help_text="帮助菜单给{name}",
            )
        )

    def msg(self, **kw):
        base = dict(
            platform="onebot", message_id="m1", session_type="group",
            session_id="30001", session_name="测试群", sender_id="20001",
            sender_name="小明", text="@我 帮助",
        )
        base.update(kw)
        return IncomingMessage(**base)

    async def test_help(self) -> None:
        helper = GroupHelper(self.cfg())
        self.assertEqual(await helper.handle(self.msg()), "帮助菜单给小明")

    async def test_weather(self) -> None:
        helper = GroupHelper(self.cfg())
        with patch("aibot.group_helper.weather", new=AsyncMock(return_value="北京: 晴 18°C")):
            self.assertEqual(await helper.handle(self.msg(text="@我 天气 北京")), "北京: 晴 18°C")

    async def test_weather_without_city(self) -> None:
        helper = GroupHelper(self.cfg())
        self.assertIn("哪个城市", await helper.handle(self.msg(text="@我 天气")) or "")

    async def test_free_text_falls_through(self) -> None:
        helper = GroupHelper(self.cfg())
        self.assertIsNone(await helper.handle(self.msg(text="@我 今天好累")))

    async def test_private_ignored(self) -> None:
        helper = GroupHelper(self.cfg())
        self.assertIsNone(await helper.handle(self.msg(session_type="private", text="帮助")))


if __name__ == "__main__":
    unittest.main()
