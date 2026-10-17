"""零依赖 Web 控制台：状态、消息流、开关、测试发送。

使用标准库 ThreadingHTTPServer，运行在独立线程；通过 Application.submit
把需要异步执行的操作投递回主事件循环。
"""

from __future__ import annotations

import json
import logging
import threading
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

from ..config import Config

log = logging.getLogger("aibot.web")

STATIC_DIR = Path(__file__).resolve().parent.parent / "web"


class WebServer:
    def __init__(self, cfg: Config, app) -> None:
        self.cfg = cfg
        self.app = app
        self.token = (cfg.web.token or "").strip()
        self._server: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if not self.token and self.cfg.web.host not in {"127.0.0.1", "localhost", "::1"}:
            raise RuntimeError(
                "Web 控制台绑定在非本机地址时必须设置 web.token，否则任何人都能操作你的机器人"
            )

        handler = _make_handler(self)
        self._server = ThreadingHTTPServer((self.cfg.web.host, self.cfg.web.port), handler)
        self._server.daemon_threads = True
        self._thread = threading.Thread(target=self._server.serve_forever, name="aibot-web", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        if self._server:
            self._server.shutdown()
            self._server.server_close()
        if self._thread:
            self._thread.join(timeout=3)


def _make_handler(server: WebServer):
    class Handler(BaseHTTPRequestHandler):
        server_version = "ai-im-bot"
        protocol_version = "HTTP/1.1"

        # -------------------------------------------------------------- 路由

        def do_GET(self) -> None:  # noqa: N802
            try:
                parsed = urlparse(self.path)
                if parsed.path in {"/", "/index.html"}:
                    self._serve_index()
                elif parsed.path == "/api/status":
                    self._guard(self._json, server.app.status())
                elif parsed.path == "/api/messages":
                    qs = parse_qs(parsed.query)
                    since = _int(qs.get("since", ["0"])[0], 0)
                    limit = _int(qs.get("limit", ["80"])[0], 80)
                    self._guard(self._json, {"messages": server.app.store.recent_messages(limit, since)})
                elif parsed.path == "/api/sessions":
                    self._guard(self._json, {"sessions": _sessions(server.app)})
                elif parsed.path == "/api/config":
                    self._guard(self._json, _public_config(server.cfg))
                elif parsed.path == "/api/logs":
                    qs = parse_qs(parsed.query)
                    lines = min(_int(qs.get("lines", ["120"])[0], 120), 500)
                    self._guard(self._json, {"lines": _tail(server.cfg.log_path, lines)})
                elif parsed.path == "/favicon.ico":
                    self._send(HTTPStatus.NO_CONTENT, b"", "image/x-icon")
                else:
                    self._json({"error": "not found"}, HTTPStatus.NOT_FOUND)
            except Exception:  # noqa: BLE001
                log.exception("GET %s 失败", self.path)
                self._json({"error": "internal error"}, HTTPStatus.INTERNAL_SERVER_ERROR)

        def do_POST(self) -> None:  # noqa: N802
            try:
                parsed = urlparse(self.path)
                body = self._read_body()
                if parsed.path == "/api/control":
                    self._guard(self._control, body)
                elif parsed.path == "/api/test":
                    self._guard(self._test, body)
                elif parsed.path == "/api/send":
                    self._guard(self._send_manual, body)
                elif parsed.path == "/api/reset":
                    self._guard(self._reset, body)
                elif parsed.path == "/api/shutdown":
                    self._guard(self._shutdown, body)
                else:
                    self._json({"error": "not found"}, HTTPStatus.NOT_FOUND)
            except Exception:  # noqa: BLE001
                log.exception("POST %s 失败", self.path)
                self._json({"error": "internal error"}, HTTPStatus.INTERNAL_SERVER_ERROR)

        # -------------------------------------------------------------- 业务

        def _control(self, body: dict[str, Any]) -> None:
            if "auto_reply" in body:
                server.app.pipeline.set_auto_reply(bool(body["auto_reply"]))
            self._json({"ok": True, "status": server.app.status()})

        def _test(self, body: dict[str, Any]) -> None:
            text = str(body.get("text", "")).strip()
            if not text:
                self._json({"ok": False, "error": "内容不能为空"}, HTTPStatus.BAD_REQUEST)
                return
            fut = server.app.submit(
                server.app.inject_message(
                    session_type=str(body.get("session_type", "private")),
                    session_id=str(body.get("session_id", "10001")),
                    text=text,
                    sender_name=str(body.get("sender_name", "测试用户")),
                    sender_id=str(body.get("sender_id", "")),
                    is_at_me=bool(body.get("is_at_me", True)),
                )
            )
            try:
                self._json(fut.result(timeout=120))
            except Exception as exc:  # noqa: BLE001
                self._json({"ok": False, "error": str(exc)}, HTTPStatus.INTERNAL_SERVER_ERROR)

        def _send_manual(self, body: dict[str, Any]) -> None:
            text = str(body.get("text", "")).strip()
            if not text:
                self._json({"ok": False, "error": "内容不能为空"}, HTTPStatus.BAD_REQUEST)
                return
            fut = server.app.submit(
                server.app.send_manual(
                    str(body.get("session_type", "private")),
                    str(body.get("session_id", "")),
                    text,
                )
            )
            try:
                self._json(fut.result(timeout=30))
            except Exception as exc:  # noqa: BLE001
                self._json({"ok": False, "error": str(exc)}, HTTPStatus.INTERNAL_SERVER_ERROR)

        def _reset(self, body: dict[str, Any]) -> None:
            if body.get("all"):
                count = server.app.store.clear_all_memory()
            else:
                key = str(body.get("session_key", "")).strip()
                if not key:
                    self._json({"ok": False, "error": "session_key 不能为空"}, HTTPStatus.BAD_REQUEST)
                    return
                count = server.app.store.reset_session(key)
            self._json({"ok": True, "deleted": count})

        def _shutdown(self, _body: dict[str, Any]) -> None:
            server.app.request_stop()
            self._json({"ok": True})

        # -------------------------------------------------------------- 基础

        def _guard(self, fn, *args) -> None:
            if not self._auth_ok():
                self._json({"error": "unauthorized"}, HTTPStatus.UNAUTHORIZED)
                return
            fn(*args)

        def _auth_ok(self) -> bool:
            if not server.token:
                return True
            query = parse_qs(urlparse(self.path).query)
            supplied = self.headers.get("X-Admin-Token", "") or (query.get("token", [""])[0])
            return supplied == server.token

        def _serve_index(self) -> None:
            index = STATIC_DIR / "index.html"
            if not index.exists():
                self._send(HTTPStatus.NOT_FOUND, b"index.html missing", "text/plain; charset=utf-8")
                return
            self._send(HTTPStatus.OK, index.read_bytes(), "text/html; charset=utf-8")

        def _read_body(self) -> dict[str, Any]:
            length = _int(self.headers.get("Content-Length", "0"), 0)
            if length > 1024 * 1024:
                raise ValueError("body too large")
            raw = self.rfile.read(length) if length else b""
            if not raw:
                return {}
            data = json.loads(raw.decode("utf-8"))
            return data if isinstance(data, dict) else {}

        def _json(self, data: Any, status: int = HTTPStatus.OK) -> None:
            body = json.dumps(data, ensure_ascii=False).encode("utf-8")
            self._send(status, body, "application/json; charset=utf-8")

        def _send(self, status: int, body: bytes, content_type: str) -> None:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            if body:
                self.wfile.write(body)

        def log_message(self, fmt: str, *args: Any) -> None:  # noqa: A003
            log.debug("web %s - %s", self.address_string(), fmt % args)

    return Handler


# --------------------------------------------------------------------------
# 工具
# --------------------------------------------------------------------------

def _int(value: str, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _tail(path: Path, lines: int) -> list[str]:
    if not path.exists():
        return []
    try:
        content = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return []
    return content[-lines:]


def _sessions(app) -> list[dict[str, Any]]:
    rows = app.store.recent_messages(500)
    seen: dict[str, dict[str, Any]] = {}
    for row in reversed(rows):
        key = f"{row['session_type']}:{row['session_id']}"
        seen.setdefault(
            key,
            {
                "session_type": row["session_type"],
                "session_id": row["session_id"],
                "session_name": row["session_name"],
                "last_ts": row["ts"],
                "last_text": row["text"],
            },
        )
    return list(seen.values())[:50]


def _public_config(cfg: Config) -> dict[str, Any]:
    return {
        "platform": cfg.platform_type,
        "onebot": {"mode": cfg.onebot.mode, "ws_url": cfg.onebot.ws_url},
        "llm": {"base_url": cfg.llm.base_url, "model": cfg.llm.model,
                "history_turns": cfg.llm.history_turns},
        "policy": {
            "private": cfg.policy.private,
            "group": cfg.policy.group,
            "allow_users": cfg.policy.allow_users,
            "allow_groups": cfg.policy.allow_groups,
            "cooldown_seconds": cfg.policy.cooldown_seconds,
        },
        "reply": {"dry_run": cfg.reply.dry_run, "fallback": cfg.reply.fallback},
        "web": {"token_set": bool(cfg.web.token)},
    }
