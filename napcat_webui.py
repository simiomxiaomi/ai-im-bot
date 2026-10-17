"""NapCat WebUI 客户端：在 GUI 里直接获取 QQ 登录二维码。

NapCat 的 WebUI（默认 127.0.0.1:6099）提供：
    POST /api/auth/login
    POST /api/QQLogin/GetQQLoginQrcode
    POST /api/QQLogin/CheckLoginStatus

不同版本返回结构略有差异，这里做「尽力解析」：从响应 JSON 里找
第一张 base64 PNG；找不到图片就由调用方回退到打开 WebUI 页面。
"""

from __future__ import annotations

import base64
import json
import logging
import urllib.request
from pathlib import Path

from installer import napcat_dir

log = logging.getLogger("aibot.napcat_webui")

DEFAULT_PORT = 6099


def find_webui_config() -> dict | None:
    """在 NapCat 安装目录里找 webui.json，拿端口和 token。"""
    root = napcat_dir()
    if not root.exists():
        return None
    for path in root.rglob("webui.json"):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if isinstance(data, dict) and data.get("token"):
            return data
    return None


def _post(port: int, path: str, payload: dict | None = None, token: str | None = None,
          timeout: float = 8.0) -> dict:
    url = f"http://127.0.0.1:{port}{path}"
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    request = urllib.request.Request(
        url, data=json.dumps(payload or {}).encode("utf-8"),
        headers=headers, method="POST",
    )
    with urllib.request.urlopen(request, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8", "replace"))


def login(port: int, token: str) -> str:
    """登录 WebUI，返回后续请求要用的 token。"""
    try:
        data = _post(port, "/api/auth/login", {"token": token})
    except Exception as exc:  # noqa: BLE001 - 登录失败时退回原始 token
        log.warning("NapCat WebUI 登录失败：%s", exc)
        return token

    body = data.get("data")
    if isinstance(body, dict):
        for key in ("token", "access_token", "session"):
            if body.get(key):
                return str(body[key])
    if isinstance(body, str) and body:
        return body
    return token


def _iter_strings(value):
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for item in value.values():
            yield from _iter_strings(item)
    elif isinstance(value, list):
        for item in value:
            yield from _iter_strings(item)


def get_qrcode(port: int, token: str) -> bytes | None:
    """获取登录二维码 PNG 字节。"""
    try:
        data = _post(port, "/api/QQLogin/GetQQLoginQrcode", {}, token)
    except Exception as exc:  # noqa: BLE001
        log.warning("获取二维码失败：%s", exc)
        return None

    for value in _iter_strings(data):
        raw_text = value
        if raw_text.startswith("data:image") and "," in raw_text:
            raw_text = raw_text.split(",", 1)[1]
        if len(raw_text) < 100:
            continue
        try:
            raw = base64.b64decode(raw_text, validate=True)
        except Exception:  # noqa: BLE001
            continue
        if raw.startswith(b"\x89PNG"):
            return raw
    return None


def check_login(port: int, token: str) -> bool:
    """查询 QQ 是否已登录。"""
    try:
        data = _post(port, "/api/QQLogin/CheckLoginStatus", {}, token)
    except Exception:  # noqa: BLE001
        return False
    text = json.dumps(data, ensure_ascii=False).lower()
    return ('"islogin": true' in text or '"is_login": true' in text
            or '"login": true' in text or '"online": true' in text)


def quick_login(port: int, token: str, uin: str) -> bool:
    """快速登录（已登录过的账号），可选。"""
    try:
        _post(port, "/api/QQLogin/SetQQQuickLogin", {"uin": uin}, token)
        return True
    except Exception:  # noqa: BLE001
        return False
