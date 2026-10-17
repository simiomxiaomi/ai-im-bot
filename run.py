"""AI-IM-Bot 命令行入口。

用法：
    python run.py run                 # 启动机器人 + Web 控制台
    python run.py mock                # 本地模拟模式（不连 QQ）
    python run.py doctor              # 环境与连通性自检
    python run.py send --type private --to 10001 --text "你好"
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from aibot.app import Application, build_adapter  # noqa: E402
from aibot.config import Config, ConfigError, load_dotenv  # noqa: E402
from aibot.logs import setup_logging  # noqa: E402

log = logging.getLogger("aibot.cli")


def load_config(path: str) -> Config:
    load_dotenv(ROOT / ".env")
    cfg = Config.load(Path(path))
    for warning in cfg.warnings:
        print(f"⚠ 配置提醒：{warning}")
    return cfg


# --------------------------------------------------------------------------
# run / mock
# --------------------------------------------------------------------------

async def _run(cfg: Config) -> None:
    app = Application(cfg)
    try:
        await app.run()
    except KeyboardInterrupt:
        await app.shutdown()


def cmd_run(args: argparse.Namespace) -> int:
    cfg = load_config(args.config)
    setup_logging(cfg.log_level, cfg.log_path)
    try:
        asyncio.run(_run(cfg))
    except KeyboardInterrupt:
        pass
    except ConfigError as exc:
        print(f"❌ {exc}")
        return 2
    return 0


def cmd_mock(args: argparse.Namespace) -> int:
    cfg = load_config(args.config)
    cfg.platform_type = "mock"
    setup_logging(cfg.log_level, cfg.log_path)
    print("🧪 模拟模式：不连接 QQ。打开 Web 控制台，用「测试」按钮发消息。")
    try:
        asyncio.run(_run(cfg))
    except KeyboardInterrupt:
        pass
    return 0


# --------------------------------------------------------------------------
# doctor
# --------------------------------------------------------------------------

async def _probe_onebot(cfg: Config) -> tuple[bool, str]:
    import websockets

    headers = {}
    if cfg.onebot.access_token:
        headers["Authorization"] = f"Bearer {cfg.onebot.access_token}"
    url = cfg.onebot.ws_url
    try:
        try:
            ws = await websockets.connect(
                url, open_timeout=4, additional_headers=headers or None, max_size=None
            )
        except TypeError:
            ws = await websockets.connect(
                url, open_timeout=4, extra_headers=headers or None, max_size=None
            )
        async with ws:
            await ws.send(json.dumps({"action": "get_login_info", "params": {}, "echo": "doctor"}))
            deadline = asyncio.get_running_loop().time() + 5
            while asyncio.get_running_loop().time() < deadline:
                raw = await asyncio.wait_for(ws.recv(), timeout=deadline - asyncio.get_running_loop().time())
                data = json.loads(raw)
                if data.get("echo") == "doctor":
                    info = data.get("data") or {}
                    if data.get("status") == "ok" and info.get("user_id"):
                        return True, f"已连接 {url}，登录账号 {info.get('nickname')}（{info.get('user_id')}）"
                    return False, f"已连接但 get_login_info 失败：{data}"
            return False, "已连接，但 5 秒内没有收到 get_login_info 响应"
    except Exception as exc:  # noqa: BLE001
        return False, f"连接 {url} 失败：{exc.__class__.__name__}: {exc}"


def cmd_doctor(args: argparse.Namespace) -> int:
    checks: list[tuple[str, str, str]] = []  # (状态, 名称, 说明)

    def add(state: str, name: str, detail: str) -> None:
        checks.append((state, name, detail))

    # Python
    version = sys.version_info
    if version >= (3, 11):
        add("OK", "Python", f"{version.major}.{version.minor}.{version.micro}")
    else:
        add("FAIL", "Python", f"{version.major}.{version.minor} 过低，需要 3.11+")

    # 依赖
    for module, label in (("websockets", "websockets"), ("httpx", "httpx"), ("yaml", "PyYAML")):
        try:
            mod = __import__(module)
            add("OK", f"依赖 {label}", getattr(mod, "__version__", "已安装"))
        except ImportError:
            add("FAIL", f"依赖 {label}", "未安装，运行 pip install -r requirements.txt")

    # 配置
    try:
        cfg = load_config(args.config)
        add("OK", "配置文件", str(Path(args.config).resolve()))
    except ConfigError as exc:
        add("FAIL", "配置文件", str(exc).replace("\n", " "))
        cfg = None  # type: ignore[assignment]

    if cfg is None:
        _print_checks(checks)
        return 1

    # 密钥与模型
    if cfg.llm.api_key:
        add("OK", "LLM api_key", f"已配置（{len(cfg.llm.api_key)} 字符）")
    else:
        add("FAIL", "LLM api_key", "为空：在 .env 里设置 LLM_API_KEY")

    # 数据目录
    try:
        cfg.db_path.parent.mkdir(parents=True, exist_ok=True)
        probe = cfg.db_path.parent / ".write_probe"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
        add("OK", "数据目录", str(cfg.db_path.parent))
    except OSError as exc:
        add("FAIL", "数据目录", f"不可写：{exc}")

    # 微信
    if cfg.platform_type == "wechat":
        try:
            from aibot.adapters.wechat_uia import diagnose

            add("OK", "微信检测", diagnose())
        except Exception as exc:  # noqa: BLE001
            add("WARN", "微信检测", str(exc))

    # 连接与模型
    async def online_checks() -> None:
        if cfg.platform_type == "onebot":
            ok, detail = await _probe_onebot(cfg)
            add("OK" if ok else "FAIL", "OneBot 连接", detail)
        if not args.offline:
            from aibot.llm import LLMClient

            client = LLMClient(cfg.llm)
            try:
                ok, detail = await client.ping()
                add("OK" if ok else "FAIL", "LLM 连通性", detail)
            finally:
                await client.close()

    asyncio.run(online_checks())
    _print_checks(checks)
    return 1 if any(state == "FAIL" for state, _, _ in checks) else 0


def _print_checks(checks: list[tuple[str, str, str]]) -> None:
    symbols = {"OK": "✅", "WARN": "⚠️ ", "FAIL": "❌"}
    print("\n═══ 自检结果 ═══")
    for state, name, detail in checks:
        print(f"{symbols.get(state, '•')} {name:<16} {detail}")
    failed = sum(1 for state, _, _ in checks if state == "FAIL")
    warned = sum(1 for state, _, _ in checks if state == "WARN")
    print(f"\n共 {len(checks)} 项：{len(checks) - failed - warned} 通过，{warned} 警告，{failed} 失败")
    if failed:
        print("按上面的 ❌ 逐项处理后重试。")


# --------------------------------------------------------------------------
# send
# --------------------------------------------------------------------------

async def _send_once(cfg: Config, session_type: str, target: str, text: str) -> bool:
    app = Application(cfg)

    async def noop(_msg) -> None:  # 只发送，不处理收到的消息
        return None

    task = asyncio.create_task(app.adapter.start(noop))
    try:
        for _ in range(75):
            if app.adapter.connected or cfg.platform_type == "mock":
                break
            await asyncio.sleep(0.2)
        else:
            log.error("15 秒内没有连上适配器")
            return False
        return await app.adapter.send_text(session_type, target, text)
    finally:
        await app.adapter.stop()
        task.cancel()
        await app.llm.close()
        app.store.close()


def cmd_send(args: argparse.Namespace) -> int:
    cfg = load_config(args.config)
    setup_logging("INFO", None)
    ok = asyncio.run(_send_once(cfg, args.type, args.to, args.text))
    print("✅ 已发送" if ok else "❌ 发送失败，检查日志")
    return 0 if ok else 1


# --------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="ai-im-bot", description="QQ/微信 AI 自动回复机器人")
    sub = parser.add_subparsers(dest="command", required=True)

    p_run = sub.add_parser("run", help="启动机器人 + Web 控制台")
    p_run.add_argument("--config", default="config.yaml")
    p_run.set_defaults(func=cmd_run)

    p_mock = sub.add_parser("mock", help="本地模拟模式")
    p_mock.add_argument("--config", default="config.yaml")
    p_mock.set_defaults(func=cmd_mock)

    p_doctor = sub.add_parser("doctor", help="环境与连通性自检")
    p_doctor.add_argument("--config", default="config.yaml")
    p_doctor.add_argument("--offline", action="store_true", help="跳过 LLM 在线测试")
    p_doctor.set_defaults(func=cmd_doctor)

    p_send = sub.add_parser("send", help="手动发送一条消息")
    p_send.add_argument("--config", default="config.yaml")
    p_send.add_argument("--type", choices=["private", "group"], default="private")
    p_send.add_argument("--to", required=True, help="QQ 号或群号")
    p_send.add_argument("--text", required=True)
    p_send.set_defaults(func=cmd_send)

    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except ConfigError as exc:
        print(f"❌ 配置错误：{exc}")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
