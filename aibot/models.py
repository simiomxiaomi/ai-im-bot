"""数据模型与协议解析工具。"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Literal

SessionType = Literal["private", "group"]


@dataclass(slots=True)
class IncomingMessage:
    platform: str
    message_id: str
    session_type: SessionType
    session_id: str
    session_name: str
    sender_id: str
    sender_name: str
    text: str
    timestamp: float = field(default_factory=time.time)
    is_at_me: bool = False
    raw: dict[str, Any] = field(default_factory=dict, repr=False)

    @property
    def session_key(self) -> str:
        return f"{self.platform}:{self.session_type}:{self.session_id}"

    @property
    def display_text(self) -> str:
        return self.text.strip() or "[非文本消息]"


@dataclass(slots=True)
class OutgoingMessage:
    platform: str
    session_type: SessionType
    session_id: str
    text: str
    at_user: str | None = None
    reply_to: str | None = None


def cq_escape(text: str) -> str:
    """转义 CQ 码特殊字符，避免回复内容被 OneBot 当成指令解析。"""
    return (
        text.replace("&", "&amp;")
        .replace("[", "&#91;")
        .replace("]", "&#93;")
        .replace(",", "&#44;")
    )


def clean_reply(text: str, max_chars: int) -> str:
    """清理模型输出：去掉多余空白、代码围栏，并限制长度。"""
    text = (text or "").strip()
    if text.startswith("```") and text.endswith("```"):
        lines = text.splitlines()
        if len(lines) >= 3:
            text = "\n".join(lines[1:-1]).strip()
    text = text.strip().strip('"').strip("「」").strip()
    if max_chars > 0 and len(text) > max_chars:
        text = text[: max_chars - 1].rstrip() + "…"
    return text


def onebot_text_from_segments(segments: list[dict[str, Any]], self_id: str) -> tuple[str, bool]:
    """把 OneBot 消息段数组拼成文本，同时判断是否 @ 了机器人。"""
    parts: list[str] = []
    at_me = False
    for seg in segments or []:
        if not isinstance(seg, dict):
            continue
        stype = seg.get("type")
        data = seg.get("data") or {}
        if stype == "text":
            parts.append(str(data.get("text", "")))
        elif stype == "at":
            qq = str(data.get("qq", ""))
            if qq == str(self_id):
                at_me = True
                parts.append("@我")
            else:
                name = data.get("name") or qq
                parts.append(f"@{name}")
        elif stype == "image":
            parts.append("[图片]")
        elif stype == "face":
            parts.append("[表情]")
        elif stype == "record":
            parts.append("[语音]")
        elif stype == "video":
            parts.append("[视频]")
        elif stype == "file":
            parts.append("[文件]")
        elif stype == "reply":
            continue
        elif stype == "forward":
            parts.append("[合并转发]")
        else:
            parts.append(f"[{stype}]")
    return "".join(parts).strip(), at_me
