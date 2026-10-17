"""NapCat 配置自动写入：不用进 WebUI 手动加 WebSocket 服务器。

NapCat 的 OneBot 配置：
- 账户级：<napcat>/config/onebot11_<QQ号>.json
- 默认级：<napcat>/config/onebot11.json（v4.5.3+）

本模块扫描这些文件，把 3001 端口的 WebSocket 服务器合并进去，
原文件先备份为 .bak。修改后需要重启 NapCat 生效。
"""

from __future__ import annotations

import json
import logging
import os
import shutil
from pathlib import Path

from installer import napcat_dir

log = logging.getLogger("aibot.napcat_config")

SERVER_NAME = "aibot"
DEFAULT_PORT = 3001
DEFAULT_TOKEN = "aibot-token"


def _candidate_roots() -> list[Path]:
    roots: list[Path] = []
    local = napcat_dir()
    if local.exists():
        roots.append(local)

    for env in ("ProgramFiles", "ProgramFiles(x86)", "LOCALAPPDATA"):
        base = os.environ.get(env)
        if not base:
            continue
        for name in ("Tencent/QQNT", "Tencent/QQ", "QQNT"):
            path = Path(base) / name
            if path.exists():
                roots.append(path)

    # NapCat 有时装在别处，从启动器反推
    launcher = None
    try:
        from installer import find_launcher

        launcher = find_launcher()
    except Exception:  # noqa: BLE001
        pass
    if launcher:
        for parent in launcher.parents:
            if parent.name.lower() in {"napcat", "config", "app_launcher"}:
                roots.append(parent)
                break

    seen: set[Path] = set()
    unique = []
    for root in roots:
        if root not in seen:
            seen.add(root)
            unique.append(root)
    return unique


def iter_config_files() -> list[Path]:
    """列出所有可能的 onebot11 配置文件（去重）。"""
    seen: set[Path] = set()
    result: list[Path] = []
    for root in _candidate_roots():
        try:
            paths = list(root.rglob("onebot11*.json"))
        except OSError:
            continue
        for path in paths:
            if path in seen or not path.is_file():
                continue
            if "napcat" not in str(path).lower():
                continue
            seen.add(path)
            result.append(path)
    return result


def _server_entry(port: int, token: str) -> dict:
    return {
        "name": SERVER_NAME,
        "enable": True,
        "host": "127.0.0.1",
        "port": port,
        "messagePostFormat": "array",
        "reportSelfMessage": False,
        "token": token,
        "enableForcePushEvent": True,
        "debug": False,
        "heartInterval": 30000,
    }


def _merge(data: dict, port: int, token: str) -> dict:
    if not isinstance(data, dict):
        data = {}
    network = data.get("network")
    if not isinstance(network, dict):
        network = {}
        data["network"] = network

    servers = network.get("websocketServers")
    if not isinstance(servers, list):
        servers = []
    servers = [s for s in servers if not (isinstance(s, dict) and s.get("name") == SERVER_NAME)]
    servers.append(_server_entry(port, token))
    network["websocketServers"] = servers

    data.setdefault("musicSignUrl", "")
    data.setdefault("enableLocalFile2Url", False)
    data.setdefault("parseMultMsg", False)
    return data


def apply_websocket_config(port: int = DEFAULT_PORT, token: str = DEFAULT_TOKEN) -> list[Path]:
    """把 WebSocket 服务器配置写入所有找到的 onebot11*.json，返回修改的文件。"""
    changed: list[Path] = []
    for path in iter_config_files():
        try:
            text = path.read_text(encoding="utf-8")
        except OSError as exc:
            log.warning("读取配置失败 %s：%s", path, exc)
            continue

        try:
            data = json.loads(text)
        except ValueError:
            # JSON5（带注释）无法解析：备份后重建，避免破坏原文件
            log.warning("配置含注释无法解析，将备份并重建：%s", path)
            data = {}

        try:
            shutil.copy2(path, path.with_suffix(path.suffix + ".bak"))
        except OSError:
            pass

        merged = _merge(data, port, token)
        try:
            path.write_text(
                json.dumps(merged, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            changed.append(path)
        except OSError as exc:
            log.warning("写入配置失败 %s：%s", path, exc)

    return changed


def create_default_config(port: int = DEFAULT_PORT, token: str = DEFAULT_TOKEN) -> Path | None:
    """没有找到任何配置时，在 NapCat 目录下创建 config/onebot11.json。"""
    root = napcat_dir()
    if not root.exists():
        return None
    config_dir = root / "config"
    config_dir.mkdir(parents=True, exist_ok=True)
    path = config_dir / "onebot11.json"
    path.write_text(
        json.dumps(_merge({}, port, token), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return path
