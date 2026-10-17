"""OneBot v11 适配器（QQ / NapCat / Lagrange）。

支持两种连接方式：
- forward：主动连接 NapCat 的 WebSocket 服务（默认 ws://127.0.0.1:3001）
- reverse：自己起 WebSocket 服务，等待 NapCat 反向连接

事件与 API 都走同一连接，用 echo 字段做请求/响应配对。
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import re
import time
from typing import Any

import websockets

from ..config import OneBotConfig
from ..models import IncomingMessage, onebot_text_from_segments
from .base import Adapter, AdapterError, MessageHandler

log = logging.getLogger("aibot.onebot")

_CQ_RE = re.compile(r"\[CQ:([a-zA-Z0-9_.-]+)((?:,[^\]]*)?)\]")


class OneBotAdapter(Adapter):
    name = "onebot"

    def __init__(self, cfg: OneBotConfig) -> None:
        self.cfg = cfg
        self.self_id = str(cfg.self_id or "")
        self.connected = False
        self.mode = cfg.mode
        self._handler: MessageHandler | None = None
        self._ws: Any = None
        self._stop = asyncio.Event()
        self._seq = 0
        self._pending: dict[str, asyncio.Future] = {}
        self._tasks: set[asyncio.Task] = set()
        self._send_lock = asyncio.Lock()
        self._last_event = 0.0
        self._stats = {"received": 0, "sent": 0, "errors": 0, "reconnects": 0}

    # ------------------------------------------------------------------ 生命周期

    async def start(self, on_message: MessageHandler) -> None:
        self._handler = on_message
        self._stop.clear()
        if self.mode == "reverse":
            await self._run_reverse()
        else:
            await self._run_forward()

    async def stop(self) -> None:
        self._stop.set()
        for task in list(self._tasks):
            task.cancel()
        if self._ws is not None:
            with contextlib.suppress(Exception):
                await self._ws.close()
        self._fail_pending(AdapterError("已停止"))

    async def health(self) -> dict:
        return {
            "name": self.name,
            "connected": self.connected,
            "self_id": self.self_id,
            "mode": self.mode,
            "url": self.cfg.ws_url if self.mode == "forward" else
            f"ws://{self.cfg.reverse_host}:{self.cfg.reverse_port}{self.cfg.reverse_path}",
            "last_event_age": round(time.time() - self._last_event, 1) if self._last_event else None,
            **self._stats,
        }

    # ------------------------------------------------------------------ 连接

    async def _run_forward(self) -> None:
        backoff = 1.0
        url = self.cfg.ws_url
        headers = self._auth_headers(self.cfg.access_token)
        while not self._stop.is_set():
            try:
                ws = await self._connect(url, headers)
                async with ws:
                    self._ws = ws
                    self.connected = True
                    self._stats["reconnects"] += 1
                    log.info("已连接 OneBot：%s（机器人 QQ：%s）", url, self.self_id or "待识别")
                    backoff = 1.0
                    await self._pump(ws)
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 - 断线原因是外部状态，需要记录并重连
                log.warning("OneBot 连接异常：%s", exc)
                self._stats["errors"] += 1
            finally:
                self.connected = False
                self._ws = None
                self._fail_pending(AdapterError("连接断开"))
            if self._stop.is_set():
                break
            log.info("%.1fs 后重连 OneBot…", backoff)
            with contextlib.suppress(asyncio.TimeoutError):
                await asyncio.wait_for(self._stop.wait(), timeout=backoff)
            backoff = min(backoff * 2, 30.0)

    async def _run_reverse(self) -> None:
        host, port = self.cfg.reverse_host, self.cfg.reverse_port
        path = self.cfg.reverse_path if self.cfg.reverse_path.startswith("/") else "/" + self.cfg.reverse_path

        async def handler(websocket: Any, *_: Any) -> None:
            if not self._check_reverse_token(websocket):
                log.warning("反向 WS 连接被拒绝：token 不匹配（来自 %s）", _peer(websocket))
                await websocket.close(code=4003, reason="invalid token")
                return
            self._ws = websocket
            self.connected = True
            log.info("NapCat 已反向连接到本程序（来自 %s）", _peer(websocket))
            try:
                await self._pump(websocket)
            except Exception as exc:  # noqa: BLE001
                log.warning("反向 WS 断开：%s", exc)
            finally:
                self.connected = False
                self._ws = None
                self._fail_pending(AdapterError("反向连接断开"))

        async with websockets.serve(handler, host, port, max_size=None, ping_interval=20) as server:
            log.info("OneBot 反向 WS 已启动：ws://%s:%d%s", host, port, path)
            await self._stop.wait()

    async def _connect(self, url: str, headers: dict[str, str]) -> Any:
        kwargs: dict[str, Any] = dict(
            ping_interval=20, ping_timeout=20, close_timeout=5, max_size=None
        )
        if headers:
            try:
                return await websockets.connect(url, additional_headers=headers, **kwargs)
            except TypeError:  # websockets < 13 的旧参数名
                return await websockets.connect(url, extra_headers=headers, **kwargs)
        return await websockets.connect(url, **kwargs)

    @staticmethod
    def _auth_headers(token: str) -> dict[str, str]:
        token = (token or "").strip()
        if not token:
            return {}
        return {"Authorization": f"Bearer {token}", "X-Self-ID": ""}

    def _check_reverse_token(self, websocket: Any) -> bool:
        expected = (self.cfg.reverse_token or "").strip()
        if not expected:
            return True
        token = ""
        with contextlib.suppress(Exception):
            token = websocket.request.headers.get("Authorization", "")
        if not token:
            with contextlib.suppress(Exception):
                token = websocket.request.headers.get("X-Client-Role", "")
        if token.startswith("Bearer "):
            token = token[7:]
        if not token:
            with contextlib.suppress(Exception):
                query = websocket.request.path.split("?", 1)
                if len(query) == 2:
                    for part in query[1].split("&"):
                        if part.startswith("access_token="):
                            token = part.split("=", 1)[1]
        return token == expected

    # ------------------------------------------------------------------ 收包

    async def _pump(self, ws: Any) -> None:
        async for raw in ws:
            try:
                data = json.loads(raw)
            except (TypeError, ValueError):
                log.debug("忽略非 JSON 帧：%r", str(raw)[:120])
                continue
            self._last_event = time.time()
            echo = data.get("echo")
            if echo is not None and str(echo) in self._pending:
                fut = self._pending.pop(str(echo))
                if not fut.done():
                    fut.set_result(data)
                continue
            self._handle_event(data)

    def _handle_event(self, data: dict[str, Any]) -> None:
        if data.get("self_id"):
            self.self_id = str(data["self_id"])
        if data.get("post_type") != "message":
            return
        msg = self._parse_message(data)
        if msg is None:
            return
        self._stats["received"] += 1
        task = asyncio.create_task(self._dispatch(msg))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def _dispatch(self, msg: IncomingMessage) -> None:
        if self._handler is None:
            return
        try:
            await self._handler(msg)
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 - 单条消息失败不应终止接收
            log.exception("处理消息失败：%s", msg.message_id)
            self._stats["errors"] += 1

    def _parse_message(self, event: dict[str, Any]) -> IncomingMessage | None:
        message_type = event.get("message_type")
        if message_type not in {"private", "group"}:
            return None
        sender = event.get("sender") or {}
        raw_message = event.get("message")
        segments = _normalize_segments(raw_message)
        text, at_me = onebot_text_from_segments(segments, self.self_id)
        if not text:
            return None

        if message_type == "group":
            session_id = str(event.get("group_id", ""))
            session_name = str(event.get("group_name") or session_id)
        else:
            session_id = str(event.get("user_id", ""))
            session_name = str(sender.get("nickname") or session_id)
        if not session_id:
            return None

        return IncomingMessage(
            platform="qq",
            message_id=str(event.get("message_id", "")),
            session_type=message_type,  # type: ignore[arg-type]
            session_id=session_id,
            session_name=session_name,
            sender_id=str(event.get("user_id", "")),
            sender_name=str(sender.get("card") or sender.get("nickname") or sender.get("user_id", "")),
            text=text,
            is_at_me=at_me,
            raw=event,
        )

    # ------------------------------------------------------------------ 发送

    async def send_text(
        self,
        session_type: str,
        session_id: str,
        text: str,
        at_user: str | None = None,
        reply_to: str | None = None,
    ) -> bool:
        if not text.strip():
            return False
        segments: list[dict[str, Any]] = []
        if session_type == "group" and at_user:
            segments.append({"type": "at", "data": {"qq": str(at_user)}})
            segments.append({"type": "text", "data": {"text": " " + text}})
        else:
            segments.append({"type": "text", "data": {"text": text}})

        if session_type == "group":
            action, params = "send_group_msg", {"group_id": _to_int(session_id), "message": segments}
        else:
            action, params = "send_private_msg", {"user_id": _to_int(session_id), "message": segments}
        try:
            data = await self.call_api(action, params, timeout=15.0)
        except AdapterError as exc:
            log.error("发送失败：%s", exc)
            self._stats["errors"] += 1
            return False
        if str(data.get("retcode", 0)) not in {"0", "1"}:
            log.error("发送失败：retcode=%s msg=%s", data.get("retcode"), data.get("message") or data.get("wording"))
            self._stats["errors"] += 1
            return False
        self._stats["sent"] += 1
        return True

    async def call_api(self, action: str, params: dict[str, Any], timeout: float = 10.0) -> dict[str, Any]:
        ws = self._ws
        if ws is None or not self.connected:
            raise AdapterError("OneBot 未连接")
        self._seq += 1
        echo = f"aibot-{self._seq}"
        loop = asyncio.get_running_loop()
        fut: asyncio.Future = loop.create_future()
        self._pending[echo] = fut
        payload = {"action": action, "params": params, "echo": echo}
        try:
            async with self._send_lock:
                await ws.send(json.dumps(payload, ensure_ascii=False))
        except Exception as exc:  # noqa: BLE001
            self._pending.pop(echo, None)
            raise AdapterError(f"发送请求失败：{exc}") from exc

        try:
            resp = await asyncio.wait_for(fut, timeout=timeout)
        except asyncio.TimeoutError as exc:
            self._pending.pop(echo, None)
            raise AdapterError(f"等待 {action} 响应超时（{timeout:g}s），检查 NapCat 是否正常") from exc
        finally:
            self._pending.pop(echo, None)

        if int(resp.get("retcode", 0)) != 0:
            raise AdapterError(f"{action} 失败：retcode={resp.get('retcode')} {resp.get('message') or resp.get('wording') or ''}")
        return resp

    def _fail_pending(self, exc: Exception) -> None:
        for fut in list(self._pending.values()):
            if not fut.done():
                fut.set_exception(exc)
        self._pending.clear()


# --------------------------------------------------------------------------
# 工具
# --------------------------------------------------------------------------

def _normalize_segments(message: Any) -> list[dict[str, Any]]:
    if isinstance(message, list):
        return [seg for seg in message if isinstance(seg, dict)]
    if isinstance(message, str):
        return _parse_cq_string(message)
    return []


def _parse_cq_string(text: str) -> list[dict[str, Any]]:
    """把 CQ 码字符串转成消息段；未识别的部分按纯文本处理。"""
    segments: list[dict[str, Any]] = []
    pos = 0
    for match in _CQ_RE.finditer(text):
        if match.start() > pos:
            segments.append({"type": "text", "data": {"text": _cq_unescape(text[pos:match.start()])}})
        seg_type = match.group(1)
        params: dict[str, str] = {}
        for pair in match.group(2).split(",")[1:]:
            if "=" in pair:
                k, _, v = pair.partition("=")
                params[k] = _cq_unescape(v)
        segments.append({"type": seg_type, "data": params})
        pos = match.end()
    if pos < len(text):
        segments.append({"type": "text", "data": {"text": _cq_unescape(text[pos:])}})
    return segments


def _cq_unescape(text: str) -> str:
    return (
        text.replace("&#91;", "[").replace("&#93;", "]")
        .replace("&#44;", ",").replace("&amp;", "&")
    )


def _to_int(value: str) -> int | str:
    try:
        return int(value)
    except (TypeError, ValueError):
        return value


def _peer(websocket: Any) -> str:
    try:
        return str(websocket.remote_address)
    except Exception:  # noqa: BLE001
        return "unknown"
