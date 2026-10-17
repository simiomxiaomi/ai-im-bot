"""微信桌面版 UI 自动化适配器（实验性）。

现实情况（务必阅读）：
- 微信 3.9.x 使用原生/自绘混合窗口，UIA 树可读，本适配器按 3.9 结构实现；
- 微信 4.x 改为 MMUI 全自绘（窗口类名 Qt51514QWindowIcon），UIA 树为空，
  任何基于 uiautomation / wxauto 的方案都会失效，本适配器启动时会直接报错。
- 微信 3.9 的发送依赖剪贴板 + 键盘模拟，运行期间请不要抢占鼠标键盘。

如果你在用微信 4.x，请改用 QQ（OneBot）方案，或安装 3.9 版本单独使用。
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import re
import time

from ..config import WeChatConfig
from ..models import IncomingMessage
from .base import Adapter, AdapterError, MessageHandler

log = logging.getLogger("aibot.wechat")

_UNREAD_RE = re.compile(r"(\d+)\s*条新消息")


class WeChatUIAAdapter(Adapter):
    name = "wechat"
    self_id = ""

    def __init__(self, cfg: WeChatConfig) -> None:
        self.cfg = cfg
        self._handler: MessageHandler | None = None
        self._stop = asyncio.Event()
        self._ui = None
        self._win = None
        self._seen: dict[str, str] = {}
        self._sent: list[str] = []

    # ------------------------------------------------------------------ 生命周期

    async def start(self, on_message: MessageHandler) -> None:
        self._handler = on_message
        try:
            import uiautomation as auto  # noqa: F401
        except ImportError as exc:
            raise AdapterError(
                "未安装 uiautomation。微信适配器需要：pip install uiautomation"
            ) from exc

        self._ui = auto
        self._win = self._find_window(auto)
        self._assert_supported()

        log.warning("微信 UI 自动化已启动（实验性）。运行期间请勿抢占鼠标键盘。")
        while not self._stop.is_set():
            try:
                await asyncio.to_thread(self._poll_once)
            except Exception:  # noqa: BLE001 - 单次轮询失败不应退出
                log.exception("微信轮询失败")
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=self.cfg.poll_interval)
            except asyncio.TimeoutError:
                pass

    async def stop(self) -> None:
        self._stop.set()

    async def send_text(
        self,
        session_type: str,
        session_id: str,
        text: str,
        at_user: str | None = None,
        reply_to: str | None = None,
    ) -> bool:
        if self._win is None:
            return False
        try:
            await asyncio.to_thread(self._send_sync, session_id, text)
            self._sent.append(session_id)
            return True
        except Exception:  # noqa: BLE001
            log.exception("微信发送失败")
            return False

    # ------------------------------------------------------------------ 内部实现

    def _find_window(self, auto):
        for kwargs in (
            {"ClassName": "WeChatMainWndForPC", "searchDepth": 1},
            {"ClassName": "Qt51514QWindowIcon", "searchDepth": 1},
            {"Name": self.cfg.window_name, "searchDepth": 1},
        ):
            win = auto.WindowControl(**kwargs)
            if win.Exists(1, 0.3):
                return win
        raise AdapterError("未找到微信窗口，请先启动并登录微信")

    def _assert_supported(self) -> None:
        """微信 4.x 的 UIA 树为空，提前给出明确诊断。"""
        name = self._win.Name or ""
        children = self._win.GetChildren()
        if not children:
            raise AdapterError(
                "检测到微信 4.x（UIA 控件树为空）。微信 4.x 改用 MMUI 自绘，"
                "已无法通过 UI 自动化读写消息。请改用 QQ（OneBot）方案，"
                "或安装微信 3.9.x 后重试。"
            )
        log.info("微信窗口：%s（控件 %d 个）", name, len(children))

    def _poll_once(self) -> None:
        """扫描会话列表，处理有未读的会话。"""
        auto = self._ui
        session_list = self._win.ListControl(Name="会话")
        if not session_list.Exists(1, 0.2):
            return
        for item in session_list.GetChildren():
            name = item.Name or ""
            match = _UNREAD_RE.search(name)
            if not match:
                continue
            display = name.split("\n", 1)[0].strip()
            item.Click(simulateMove=False)
            time.sleep(0.4)
            self._read_current_chat(display)
            time.sleep(0.2)

    def _read_current_chat(self, display: str) -> None:
        msg_list = self._win.ListControl(Name="消息")
        if not msg_list.Exists(1, 0.2):
            return
        items = msg_list.GetChildren()
        if not items:
            return
        last = items[-1]
        content = (last.Name or "").strip()
        if not content:
            return
        fingerprint = hashlib.md5(f"{display}|{content}".encode("utf-8")).hexdigest()[:16]
        if self._seen.get(display) == fingerprint:
            return
        self._seen[display] = fingerprint

        sender, text = _split_sender(content)
        # 自己发的消息通常以「我」或昵称开头，简单跳过
        if sender in {"我", "自己", "微信"}:
            return
        msg = IncomingMessage(
            platform="wechat",
            message_id=f"wx-{fingerprint}",
            session_type="private",
            session_id=display,
            session_name=display,
            sender_id=display,
            sender_name=sender or display,
            text=text,
        )
        log.info("微信收到 [%s] %s：%s", display, msg.sender_name, text[:60])
        if self._handler:
            asyncio.run_coroutine_threadsafe(self._handler(msg), self._loop())

    def _send_sync(self, target: str, text: str) -> None:
        session_list = self._win.ListControl(Name="会话")
        for item in session_list.GetChildren():
            if (item.Name or "").split("\n", 1)[0].strip() == target:
                item.Click(simulateMove=False)
                time.sleep(0.3)
                break
        else:
            raise AdapterError(f"会话列表中找不到：{target}")

        edit = self._win.EditControl(AutomationId="chat_input_field")
        if not edit.Exists(1, 0.2):
            edits = self._win.GetChildren()
            edit = next((e for e in edits if e.ControlTypeName == "EditControl"), None)
        if edit is None:
            raise AdapterError("找不到微信输入框")
        edit.Click(simulateMove=False)
        self._ui.SetClipboardText(text)
        time.sleep(0.1)
        self._ui.SendKeys("{Ctrl}v", waitTime=0.05)
        time.sleep(0.15)
        self._ui.SendKeys("{Enter}" if self.cfg.send_key == "enter" else "{Ctrl}{Enter}")

    def _loop(self):
        return asyncio.get_event_loop()

    def _probe(self) -> str:
        name = self._win.Name if self._win else ""
        return f"微信窗口 {name}，已发送 {len(self._sent)} 条"


def _split_sender(content: str) -> tuple[str, str]:
    parts = content.split("\n", 1)
    if len(parts) == 2:
        return parts[0].strip(), parts[1].strip()
    return "", content


def diagnose() -> str:
    """供 doctor 命令使用：只做检测，不启动。"""
    try:
        import uiautomation as auto
    except ImportError:
        return "未安装 uiautomation（可选依赖），微信适配器不可用"
    for kwargs in (
        {"ClassName": "WeChatMainWndForPC", "searchDepth": 1},
        {"ClassName": "Qt51514QWindowIcon", "searchDepth": 1},
    ):
        win = auto.WindowControl(**kwargs)
        if win.Exists(1, 0.3):
            if not win.GetChildren():
                return (
                    f"检测到微信窗口（{win.ClassName}），但 UIA 控件树为空 → 微信 4.x，"
                    "不支持 UI 自动化；请改用 QQ(OneBot) 或微信 3.9"
                )
            return f"检测到微信窗口（{win.ClassName}），UIA 可用，可尝试微信适配器"
    return "未找到微信窗口（微信未运行或未登录）"
