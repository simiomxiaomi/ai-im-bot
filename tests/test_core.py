"""核心逻辑测试：策略、存储、管线、OneBot 解析。

运行：python -m unittest discover -s tests -v
"""

from __future__ import annotations

import asyncio
import tempfile
import time
import unittest
from pathlib import Path

from aibot.adapters.mock import MockAdapter
from aibot.adapters.onebot import _normalize_segments, OneBotAdapter
from aibot.config import Config, OneBotConfig, PolicyConfig
from aibot.llm import LLMError
from aibot.models import IncomingMessage, cq_escape, onebot_text_from_segments
from aibot.pipeline import Pipeline
from aibot.policy import PolicyEngine
from aibot.store import Store


def make_msg(**overrides) -> IncomingMessage:
    base = dict(
        platform="qq",
        message_id="m1",
        session_type="private",
        session_id="20001",
        session_name="小明",
        sender_id="20001",
        sender_name="小明",
        text="在吗",
    )
    base.update(overrides)
    return IncomingMessage(**base)  # type: ignore[arg-type]


class FakeLLM:
    def __init__(self, reply: str = "在的，怎么了？", error: Exception | None = None) -> None:
        self.reply = reply
        self.error = error
        self.calls: list[list[dict]] = []
        self.last_usage: dict = {}

    async def chat(self, messages, **kwargs):
        self.calls.append(messages)
        if self.error:
            raise self.error
        return self.reply

    async def chat_with_reasoning(self, messages, **kwargs):
        return await self.chat(messages, **kwargs), ""


TMP_ROOT = Path(__file__).resolve().parent.parent / ".testtmp"
TMP_ROOT.mkdir(exist_ok=True)


def _reset_dir(name: str) -> Path:
    """测试用固定目录：沙箱环境下系统 Temp 目录可能无法清理。"""
    path = TMP_ROOT / name
    path.mkdir(exist_ok=True)
    for item in path.glob("*"):
        if item.is_file():
            item.unlink(missing_ok=True)
    return path


class StoreTest(unittest.TestCase):
    def setUp(self) -> None:
        self.dir = _reset_dir("store")
        self.store = Store(self.dir / "test.db")

    def tearDown(self) -> None:
        self.store.close()

    def test_dedupe_and_record(self) -> None:
        msg = make_msg()
        self.assertFalse(self.store.seen("qq", "m1"))
        self.store.record_incoming(msg, True, "ok")
        self.assertTrue(self.store.seen("qq", "m1"))
        # 重复插入不应抛异常
        self.store.record_incoming(msg, True, "ok")
        rows = self.store.recent_messages(10)
        self.assertEqual(len(rows), 1)

    def test_memory_roundtrip_and_reset(self) -> None:
        self.store.add_turn("qq:private:1", "你好", "你好呀")
        self.store.add_turn("qq:private:1", "在吗", "在")
        history = self.store.history("qq:private:1", 8)
        self.assertEqual([h["role"] for h in history], ["user", "assistant", "user", "assistant"])
        self.assertEqual(history[-1]["content"], "在")
        self.assertEqual(self.store.reset_session("qq:private:1"), 4)
        self.assertEqual(self.store.history("qq:private:1", 8), [])

    def test_stats(self) -> None:
        self.store.record_incoming(make_msg(), True, "ok")
        self.store.record_incoming(make_msg(message_id="m2"), False, "黑名单")
        self.store.record_outgoing("qq", "private", "20001", "回复")
        stats = self.store.stats()
        self.assertEqual(stats["today_incoming"], 2)
        self.assertEqual(stats["today_outgoing"], 1)
        self.assertEqual(stats["today_blocked"], 1)

    def test_auto_reply_setting(self) -> None:
        self.assertTrue(self.store.get_bool("auto_reply", True))
        self.store.set_setting("auto_reply", "0")
        self.assertFalse(self.store.get_bool("auto_reply", True))


class PolicyTest(unittest.TestCase):
    def setUp(self) -> None:
        self.cfg = PolicyConfig()
        self.now = 1_700_000_000.0

    def engine(self, **kwargs) -> PolicyEngine:
        cfg = PolicyConfig(**{**self.cfg.__dict__, **kwargs})
        engine = PolicyEngine(cfg)
        engine.set_clock(lambda: self.now)
        return engine

    def test_private_whitelist(self) -> None:
        engine = self.engine(private="whitelist", allow_users=["20001"])
        self.assertTrue(engine.check(make_msg(), self.now).allowed)
        self.assertFalse(engine.check(make_msg(sender_id="9999"), self.now).allowed)

    def test_block_user_wins(self) -> None:
        engine = self.engine(private="all", block_users=["20001"])
        decision = engine.check(make_msg(), self.now)
        self.assertFalse(decision.allowed)
        self.assertIn("黑名单", decision.reason)

    def test_group_mention(self) -> None:
        engine = self.engine(group="mention")
        group_msg = make_msg(session_type="group", session_id="30001", message_id="g1", is_at_me=False)
        self.assertFalse(engine.check(group_msg, self.now).allowed)
        group_msg.is_at_me = True
        self.assertTrue(engine.check(group_msg, self.now).allowed)

    def test_keywords(self) -> None:
        engine = self.engine(private="all", keywords=["报价", "多少钱"])
        self.assertFalse(engine.check(make_msg(), self.now).allowed)
        self.assertTrue(engine.check(make_msg(text="这个多少钱"), self.now).allowed)

    def test_cooldown_and_rate_limit(self) -> None:
        engine = self.engine(private="all", cooldown_seconds=5, max_per_minute=2)
        msg = make_msg()
        self.assertTrue(engine.check(msg, self.now).allowed)
        engine.note_reply(msg.session_key, self.now)
        self.assertFalse(engine.check(msg, self.now + 1).allowed)
        # 冷却结束后，另一个会话仍受全局每分钟 2 条限制
        other = make_msg(session_id="20002", sender_id="20002", message_id="m2")
        self.assertTrue(engine.check(other, self.now + 6).allowed)
        engine.note_reply(other.session_key, self.now + 6)
        third = make_msg(session_id="20003", sender_id="20003", message_id="m3")
        self.assertFalse(engine.check(third, self.now + 7).allowed)

    def test_quiet_hours_cross_midnight(self) -> None:
        engine = self.engine(private="all", quiet_hours=["23:30-08:00"])
        local = time.mktime((2026, 10, 17, 23, 40, 0, 0, 0, -1))
        self.assertFalse(engine.check(make_msg(), local).allowed)
        local_noon = time.mktime((2026, 10, 17, 12, 0, 0, 0, 0, -1))
        self.assertTrue(engine.check(make_msg(), local_noon).allowed)


class PipelineTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.dir = _reset_dir("pipeline")
        self.cfg = Config()
        self.cfg.policy.private = "all"
        self.cfg.policy.group = "mention"
        self.cfg.policy.cooldown_seconds = 0
        self.cfg.reply.group_at_sender = True
        self.store = Store(self.dir / "test.db")
        self.llm = FakeLLM()
        self.adapter = MockAdapter()
        self.pipeline = Pipeline(self.cfg, self.store, self.llm, self.adapter, PolicyEngine(self.cfg.policy))

    def tearDown(self) -> None:
        self.store.close()

    async def test_private_reply_and_memory(self) -> None:
        result = await self.pipeline.handle(make_msg())
        self.assertTrue(result.accepted)
        self.assertTrue(result.sent)
        self.assertEqual(result.reply, "在的，怎么了？")
        self.assertEqual(len(self.adapter.sent), 1)
        self.assertEqual(len(self.store.history("qq:private:20001", 8)), 2)
        # 第二轮应携带上一轮上下文
        await self.pipeline.handle(make_msg(message_id="m2", text="那晚上呢"))
        sent_messages = self.llm.calls[-1]
        self.assertEqual(sent_messages[-3]["content"], "在吗")
        self.assertEqual(sent_messages[-2]["role"], "assistant")

    async def test_dedupe(self) -> None:
        await self.pipeline.handle(make_msg())
        second = await self.pipeline.handle(make_msg())
        self.assertFalse(second.accepted)
        self.assertIn("重复", second.reason)
        self.assertEqual(len(self.adapter.sent), 1)

    async def test_group_mention_and_at(self) -> None:
        msg = make_msg(session_type="group", session_id="30001", message_id="g1",
                       is_at_me=True, sender_id="20001")
        result = await self.pipeline.handle(msg)
        self.assertTrue(result.sent)
        self.assertEqual(self.adapter.sent[-1]["at_user"], "20001")

    async def test_group_without_mention_blocked(self) -> None:
        msg = make_msg(session_type="group", session_id="30001", message_id="g1", is_at_me=False)
        result = await self.pipeline.handle(msg)
        self.assertFalse(result.accepted)
        self.assertEqual(self.adapter.sent, [])

    async def test_auto_reply_off(self) -> None:
        self.pipeline.set_auto_reply(False)
        result = await self.pipeline.handle(make_msg())
        self.assertFalse(result.accepted)
        self.assertEqual(self.llm.calls, [])
        self.assertTrue(self.store.seen("qq", "m1"))
        self.pipeline.set_auto_reply(True)

    async def test_dry_run(self) -> None:
        self.cfg.reply.dry_run = True
        result = await self.pipeline.handle(make_msg())
        self.assertTrue(result.accepted)
        self.assertTrue(result.dry_run)
        self.assertEqual(self.adapter.sent, [])

    async def test_llm_failure_with_fallback(self) -> None:
        self.llm.error = LLMError("boom")
        self.cfg.reply.fallback = "稍后回你"
        result = await self.pipeline.handle(make_msg())
        self.assertTrue(result.sent)
        self.assertEqual(self.adapter.sent[-1]["text"], "稍后回你")

    async def test_llm_failure_without_fallback(self) -> None:
        self.llm.error = LLMError("boom")
        result = await self.pipeline.handle(make_msg())
        self.assertFalse(result.sent)
        self.assertEqual(self.adapter.sent, [])


class OneBotParseTest(unittest.TestCase):
    def test_segments_and_at(self) -> None:
        segments = [
            {"type": "at", "data": {"qq": "10000"}},
            {"type": "text", "data": {"text": " 你好"}},
            {"type": "image", "data": {"file": "x.jpg"}},
        ]
        text, at_me = onebot_text_from_segments(segments, "10000")
        self.assertTrue(at_me)
        self.assertIn("你好", text)
        self.assertIn("[图片]", text)

    def test_cq_string(self) -> None:
        segments = _normalize_segments("[CQ:at,qq=10000] 在吗[CQ:face,id=1]")
        self.assertEqual(len(segments), 3)
        text, at_me = onebot_text_from_segments(segments, "10000")
        self.assertTrue(at_me)
        self.assertIn("在吗", text)

    def test_cq_escape(self) -> None:
        self.assertEqual(cq_escape("[CQ:at,qq=all]"), "&#91;CQ:at&#44;qq=all&#93;")

    def test_adapter_parse_event(self) -> None:
        adapter = OneBotAdapter(OneBotConfig(self_id="10000"))
        event = {
            "post_type": "message",
            "message_type": "group",
            "group_id": 30001,
            "user_id": 20001,
            "message_id": 123,
            "self_id": 10000,
            "sender": {"nickname": "小明", "card": ""},
            "message": [{"type": "at", "data": {"qq": "10000"}},
                        {"type": "text", "data": {"text": " 在吗"}}],
        }
        msg = adapter._parse_message(event)
        self.assertIsNotNone(msg)
        assert msg is not None
        self.assertEqual(msg.session_id, "30001")
        self.assertTrue(msg.is_at_me)
        self.assertEqual(msg.sender_name, "小明")


if __name__ == "__main__":
    unittest.main()
