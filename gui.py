"""AI-IM-Bot 桌面配置向导（tkinter，零额外依赖）。

功能：
- 填 API Key / 模型 / QQ 白名单，保存为 config.yaml + .env
- 检测 NapCat、一键下载安装、启动 NapCat
- 一键启动/停止机器人，实时日志
- 打开 Web 控制台

打包：
    pyinstaller --onefile --windowed --name ai-im-bot ^
        --add-data "aibot\\web\\index.html;aibot\\web" gui.py
"""

from __future__ import annotations

import asyncio
import base64
import logging
import os
import queue
import sys
import threading
import time
import tkinter as tk
import webbrowser
from pathlib import Path
from tkinter import messagebox, ttk

import yaml

if getattr(sys, "frozen", False):
    APP_DIR = Path(sys.executable).resolve().parent
else:
    APP_DIR = Path(__file__).resolve().parent
if str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))

from aibot.app import Application  # noqa: E402
from aibot.config import Config, load_dotenv  # noqa: E402
import installer  # noqa: E402
import napcat_webui  # noqa: E402

CONFIG_PATH = APP_DIR / "config.yaml"
ENV_PATH = APP_DIR / ".env"

BG = "#0b0f12"
PANEL = "#111a1f"
FG = "#cfe6df"
MUTED = "#6f8b85"
ACCENT = "#35e0b0"
LINE = "#1d2d31"


class QueueHandler(logging.Handler):
    def __init__(self, log_queue: queue.Queue) -> None:
        super().__init__()
        self.queue = log_queue
        self.setFormatter(
            logging.Formatter("%(asctime)s %(levelname)-5s %(message)s", "%H:%M:%S")
        )

    def emit(self, record: logging.LogRecord) -> None:
        try:
            self.queue.put_nowait(self.format(record))
        except Exception:  # noqa: BLE001
            pass


class BotGUI(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title("AI-IM-Bot · QQ 助手配置向导")
        self.geometry("980x700")
        self.minsize(900, 640)
        self.configure(bg=BG)

        self.log_queue: queue.Queue[str] = queue.Queue()
        self.bot_app: Application | None = None
        self.bot_thread: threading.Thread | None = None
        self.napcat_proc = None

        self._build_style()
        self._build_ui()
        self._setup_logging()
        self._load_settings()
        self.after(200, self._drain_logs)
        self.after(2000, self._refresh_status)
        self.protocol("WM_DELETE_WINDOW", self._on_close)

    # ------------------------------------------------------------------ 界面

    def _build_style(self) -> None:
        style = ttk.Style(self)
        style.theme_use("clam")
        style.configure(".", background=BG, foreground=FG, fieldbackground="#0a1114")
        style.configure("TFrame", background=BG)
        style.configure("Panel.TFrame", background=PANEL)
        style.configure("TLabel", background=BG, foreground=FG)
        style.configure("Muted.TLabel", background=BG, foreground=MUTED)
        style.configure("Title.TLabel", background=BG, foreground=ACCENT,
                        font=("Microsoft YaHei UI", 16, "bold"))
        style.configure("TButton", background="#132024", foreground=FG, borderwidth=0, padding=6)
        style.map("TButton", background=[("active", "#1b3036")])
        style.configure("Accent.TButton", background="#17453a", foreground=ACCENT)
        style.map("Accent.TButton", background=[("active", "#1f5a4a")])
        style.configure("TEntry", fieldbackground="#0a1114", foreground=FG,
                        insertcolor=FG, borderwidth=1)
        style.configure("TCombobox", fieldbackground="#0a1114", foreground=FG)
        style.configure("Horizontal.TProgressbar", background=ACCENT, troughcolor="#0a1114")

    def _build_ui(self) -> None:
        header = ttk.Frame(self)
        header.pack(fill="x", padx=18, pady=(16, 8))
        ttk.Label(header, text="AI-IM-BOT", style="Title.TLabel").pack(side="left")
        ttk.Label(header, text="  QQ 自动回复配置向导", style="Muted.TLabel").pack(side="left", pady=(8, 0))
        self.status_label = ttk.Label(header, text="● 未启动", style="Muted.TLabel")
        self.status_label.pack(side="right")

        body = ttk.Frame(self)
        body.pack(fill="both", expand=True, padx=18, pady=8)
        body.columnconfigure(0, weight=3)
        body.columnconfigure(1, weight=4)
        body.rowconfigure(0, weight=1)

        self._build_form(body)
        self._build_logs(body)

        footer = ttk.Frame(self)
        footer.pack(fill="x", padx=18, pady=(0, 14))
        self.start_btn = ttk.Button(footer, text="启动机器人", style="Accent.TButton",
                                    command=self._start_bot)
        self.start_btn.pack(side="left")
        self.stop_btn = ttk.Button(footer, text="停止", command=self._stop_bot, state="disabled")
        self.stop_btn.pack(side="left", padx=8)
        ttk.Button(footer, text="保存配置", command=self._save_settings).pack(side="left", padx=8)
        ttk.Button(footer, text="打开 Web 控制台", command=self._open_web).pack(side="right")

    def _build_form(self, parent: ttk.Frame) -> None:
        left = ttk.Frame(parent, style="Panel.TFrame", padding=14)
        left.grid(row=0, column=0, sticky="nsew", padx=(0, 10))
        left.columnconfigure(1, weight=1)

        row = 0

        def label(text: str) -> None:
            nonlocal row
            ttk.Label(left, text=text, style="Muted.TLabel").grid(
                row=row, column=0, sticky="w", pady=(8, 2))

        def entry(value: str = "", show: str = "") -> ttk.Entry:
            nonlocal row
            var = tk.StringVar(value=value)
            widget = ttk.Entry(left, textvariable=var, show=show)
            widget.grid(row=row, column=1, sticky="ew", padx=(10, 0), pady=(8, 2))
            row += 1
            return widget, var

        tk.Label(left, text="大模型", bg=PANEL, fg=ACCENT,
                  font=("Microsoft YaHei UI", 11, "bold")).grid(row=row, column=0, columnspan=2, sticky="w")
        row += 1

        label("Base URL")
        _, self.var_base_url = entry("https://api.deepseek.com/v1")
        label("API Key")
        _, self.var_api_key = entry("", show="*")
        label("模型名")
        _, self.var_model = entry("deepseek-chat")

        tk.Label(left, text="机器人", bg=PANEL, fg=ACCENT,
                  font=("Microsoft YaHei UI", 11, "bold")).grid(row=row, column=0, columnspan=2,
                                                                sticky="w", pady=(14, 0))
        row += 1
        label("机器人 QQ（可留空）")
        _, self.var_self_id = entry("")
        label("私聊白名单（QQ 号，逗号分隔）")
        _, self.var_whitelist = entry("")
        label("OneBot Token")
        _, self.var_token = entry("aibot-token")

        tk.Label(left, text="NapCat（QQ 协议端）", bg=PANEL, fg=ACCENT,
                  font=("Microsoft YaHei UI", 11, "bold")).grid(row=row, column=0, columnspan=2,
                                                                sticky="w", pady=(14, 0))
        row += 1
        self.napcat_state = tk.Label(left, text="检测中…", bg=PANEL, fg=MUTED)
        self.napcat_state.grid(row=row, column=0, columnspan=2, sticky="w", pady=(4, 4))
        row += 1

        napcat_btns = ttk.Frame(left, style="Panel.TFrame")
        napcat_btns.grid(row=row, column=0, columnspan=2, sticky="ew", pady=(2, 0))
        row += 1
        ttk.Button(napcat_btns, text="检测", command=self._check_napcat).pack(side="left")
        ttk.Button(napcat_btns, text="一键下载安装", command=self._download_napcat).pack(side="left", padx=6)

        napcat_btns2 = ttk.Frame(left, style="Panel.TFrame")
        napcat_btns2.grid(row=row, column=0, columnspan=2, sticky="ew", pady=(6, 0))
        row += 1
        ttk.Button(napcat_btns2, text="启动 NapCat", command=self._launch_napcat).pack(side="left")
        ttk.Button(napcat_btns2, text="扫码登录", style="Accent.TButton",
                   command=self._scan_qr).pack(side="left", padx=6)

        self.progress = ttk.Progressbar(left, mode="determinate")
        self.progress.grid(row=row, column=0, columnspan=2, sticky="ew", pady=(8, 0))
        row += 1

        tk.Label(left, text="首次使用：先点「一键下载安装」，启动 NapCat 扫码登录小号，\n"
                             "再在 NapCat WebUI（127.0.0.1:6099）加 WebSocket 服务器，端口 3001。",
                  bg=PANEL, fg=MUTED, justify="left").grid(
            row=row, column=0, columnspan=2, sticky="w", pady=(10, 0))

    def _build_logs(self, parent: ttk.Frame) -> None:
        right = ttk.Frame(parent, style="Panel.TFrame", padding=10)
        right.grid(row=0, column=1, sticky="nsew")
        right.rowconfigure(1, weight=1)
        right.columnconfigure(0, weight=1)

        head = ttk.Frame(right, style="Panel.TFrame")
        head.grid(row=0, column=0, sticky="ew", pady=(0, 6))
        tk.Label(head, text="运行日志", bg=PANEL, fg=FG).pack(side="left")
        ttk.Button(head, text="清空", command=lambda: self.log_text.delete("1.0", "end")).pack(side="right")

        self.log_text = tk.Text(right, bg="#0a1114", fg=MUTED, insertbackground=FG,
                                relief="flat", wrap="word", font=("Consolas", 9))
        self.log_text.grid(row=1, column=0, sticky="nsew")
        scroll = ttk.Scrollbar(right, orient="vertical", command=self.log_text.yview)
        scroll.grid(row=1, column=1, sticky="ns")
        self.log_text.configure(yscrollcommand=scroll.set)

    # ------------------------------------------------------------------ 配置

    def _load_settings(self) -> None:
        if ENV_PATH.exists():
            for raw in ENV_PATH.read_text(encoding="utf-8").splitlines():
                if raw.startswith("LLM_API_KEY="):
                    self.var_api_key.set(raw.split("=", 1)[1].strip())
                elif raw.startswith("ONEBOT_ACCESS_TOKEN="):
                    self.var_token.set(raw.split("=", 1)[1].strip() or "aibot-token")

        if not CONFIG_PATH.exists():
            return
        try:
            data = yaml.safe_load(CONFIG_PATH.read_text(encoding="utf-8")) or {}
        except yaml.YAMLError:
            return
        llm = data.get("llm") or {}
        self.var_base_url.set(llm.get("base_url", self.var_base_url.get()))
        self.var_model.set(llm.get("model", self.var_model.get()))
        platform = (data.get("platform") or {}).get("onebot") or {}
        self.var_self_id.set(str(platform.get("self_id", "") or ""))
        self.var_token.set(platform.get("access_token", self.var_token.get()) or "aibot-token")
        policy = data.get("policy") or {}
        self.var_whitelist.set(", ".join(policy.get("allow_users") or []))
        self._check_napcat()

    def _collect_config(self) -> dict:
        whitelist = [x.strip() for x in self.var_whitelist.get().replace("，", ",").split(",") if x.strip()]
        return {
            "platform": {
                "type": "onebot",
                "onebot": {
                    "mode": "forward",
                    "ws_url": "ws://127.0.0.1:3001",
                    "access_token": self.var_token.get().strip(),
                    "self_id": self.var_self_id.get().strip(),
                },
            },
            "llm": {
                "base_url": self.var_base_url.get().strip(),
                "api_key": "${LLM_API_KEY}",
                "model": self.var_model.get().strip(),
                "history_turns": 8,
                "max_reply_chars": 500,
                "system_prompt": (
                    "你是我的私人助理，正在用我的 QQ 号替我回复消息。\n"
                    "要求：口语化中文，1~3 句，不要暴露 AI；"
                    "涉及转账、验证码、密码、陌生链接一律说稍后确认。"
                ),
            },
            "policy": {
                "private": "whitelist",
                "group": "mention",
                "allow_users": whitelist,
                "cooldown_seconds": 3,
                "max_per_minute": 6,
            },
            "web": {
                "enabled": True,
                "host": "127.0.0.1",
                "port": 8787,
                "token": "change-me",
            },
            "storage": {"db_path": "data/bot.db", "log_path": "data/bot.log"},
            "greeting": {"enabled": False, "items": []},
            "group": {"enabled": True, "weather_enabled": True, "hitokoto_enabled": True},
            "log_level": "INFO",
        }

    def _save_settings(self) -> bool:
        api_key = self.var_api_key.get().strip()
        if not api_key:
            messagebox.showwarning("缺少 API Key", "请先填写大模型 API Key")
            return False
        try:
            CONFIG_PATH.write_text(
                yaml.safe_dump(self._collect_config(), allow_unicode=True, sort_keys=False),
                encoding="utf-8",
            )
            ENV_PATH.write_text(
                "LLM_API_KEY={}\nONEBOT_ACCESS_TOKEN={}\nWEB_TOKEN=change-me\n".format(
                    api_key, self.var_token.get().strip()
                ),
                encoding="utf-8",
            )
        except OSError as exc:
            messagebox.showerror("保存失败", str(exc))
            return False
        self._log("配置已保存到 {}".format(CONFIG_PATH))
        return True

    # ------------------------------------------------------------------ 机器人

    def _start_bot(self) -> None:
        if self.bot_thread and self.bot_thread.is_alive():
            return
        if not self._save_settings():
            return
        load_dotenv(ENV_PATH)
        try:
            cfg = Config.load(CONFIG_PATH)
        except Exception as exc:  # noqa: BLE001
            messagebox.showerror("配置错误", str(exc))
            return

        self.bot_app = Application(cfg)
        self.bot_thread = threading.Thread(target=self._run_bot, daemon=True)
        self.bot_thread.start()
        self.start_btn.configure(state="disabled")
        self.stop_btn.configure(state="normal")
        self._log("机器人已启动，等待 NapCat 连接…")

    def _run_bot(self) -> None:
        assert self.bot_app is not None
        try:
            asyncio.run(self.bot_app.run())
        except Exception:  # noqa: BLE001
            logging.getLogger("aibot.gui").exception("机器人异常退出")

    def _stop_bot(self) -> None:
        if self.bot_app:
            self.bot_app.request_stop()
        self._log("正在停止…")
        self.start_btn.configure(state="normal")
        self.stop_btn.configure(state="disabled")

    def _refresh_status(self) -> None:
        try:
            if self.bot_app:
                status = self.bot_app.status()
                connected = bool((status.get("adapter") or {}).get("connected"))
                self.status_label.configure(
                    text="● 运行中" if connected else "● 等待 NapCat…",
                    foreground=ACCENT if connected else "#ffc857",
                )
            else:
                self.status_label.configure(text="● 未启动", foreground=MUTED)
        except Exception:  # noqa: BLE001
            pass
        self.after(2000, self._refresh_status)

    # ------------------------------------------------------------------ NapCat

    def _check_napcat(self) -> None:
        installed = installer.is_installed()
        connected = installer.port_open()
        if connected:
            text, color = "NapCat 已安装，3001 端口已连接", ACCENT
        elif installed:
            text, color = "NapCat 已安装，但 3001 未监听（先启动并在 WebUI 配置 WS 服务器）", "#ffc857"
        else:
            text, color = "未检测到 NapCat，请点「一键下载安装」", MUTED
        self.napcat_state.configure(text=text, foreground=color)

    def _download_napcat(self) -> None:
        if installer.is_installed():
            if not messagebox.askyesno("已安装", "检测到 NapCat 已存在，要重新下载覆盖吗？"):
                return

        def worker() -> None:
            try:
                def progress(done: int, total: int) -> None:
                    if total:
                        self.after(0, lambda: self.progress.configure(maximum=total, value=done))

                launcher = installer.download_napcat(progress=progress)
                self.after(0, lambda: self._log("NapCat 安装完成：{}".format(launcher)))
                self.after(0, lambda: messagebox.showinfo(
                    "安装完成",
                    "NapCat 已解压完成。\n接下来点「启动 NapCat」，用手机 QQ 扫码登录小号。",
                ))
                self.after(0, self._check_napcat)
            except Exception as exc:  # noqa: BLE001
                self.after(0, lambda: messagebox.showerror("下载失败", str(exc)))
                self.after(0, installer.open_release_page)

        threading.Thread(target=worker, daemon=True).start()
        self._log("开始下载 NapCat…（约 100MB，取决于网速）")

    def _launch_napcat(self) -> None:
        try:
            self.napcat_proc = installer.launch_napcat()
            self._log("NapCat 已启动，请扫码登录小号；WebUI 默认 127.0.0.1:6099")
        except Exception as exc:  # noqa: BLE001
            messagebox.showerror("启动失败", str(exc))

    def _scan_qr(self) -> None:
        """从 NapCat WebUI 拉取 QQ 登录二维码，弹窗显示。"""
        def worker():
            cfg = napcat_webui.find_webui_config() or {}
            try:
                port = int(cfg.get("port") or napcat_webui.DEFAULT_PORT)
            except (TypeError, ValueError):
                port = napcat_webui.DEFAULT_PORT
            token = str(cfg.get("token") or "")

            if not installer.port_open("127.0.0.1", port):
                self.after(0, lambda: messagebox.showinfo(
                    "NapCat 未运行",
                    "先点「启动 NapCat」，等 QQ 登录窗口出现后再点「扫码登录」。",
                ))
                return
            if not token:
                self.after(0, lambda: messagebox.showinfo(
                    "没有 WebUI token",
                    "没找到 napcat/config/webui.json，将打开 WebUI 页面手动扫码。",
                ))
                self.after(0, lambda: webbrowser.open(f"http://127.0.0.1:{port}"))
                return

            session = napcat_webui.login(port, token)
            png = napcat_webui.get_qrcode(port, session)
            if png:
                self.after(0, lambda: self._show_qr(png, port, session))
            else:
                self.after(0, lambda: webbrowser.open(f"http://127.0.0.1:{port}"))
                self.after(0, lambda: messagebox.showinfo(
                    "扫码登录",
                    "没能直接取到二维码，已打开 NapCat WebUI，请在那里扫码登录。",
                ))

        self._log("正在从 NapCat WebUI 获取登录二维码…")
        threading.Thread(target=worker, daemon=True).start()

    def _show_qr(self, png: bytes, port: int, session: str) -> None:
        win = tk.Toplevel(self)
        win.title("扫码登录 QQ")
        win.configure(bg=BG)
        try:
            image = tk.PhotoImage(data=base64.b64encode(png).decode("ascii"))
        except tk.TclError as exc:
            messagebox.showerror("二维码显示失败", str(exc))
            return
        label = tk.Label(win, image=image, bg=BG)
        label.image = image
        label.pack(padx=14, pady=14)
        tk.Label(win, text="用手机 QQ 扫描二维码", bg=BG, fg=MUTED).pack(pady=(0, 12))

        def done() -> None:
            self._log("QQ 扫码登录成功")
            try:
                win.destroy()
            except tk.TclError:
                pass
            self._check_napcat()

        def poll() -> None:
            def check() -> None:
                for _ in range(150):
                    try:
                        if napcat_webui.check_login(port, session):
                            self.after(0, done)
                            return
                    except Exception:  # noqa: BLE001
                        pass
                    time.sleep(2)

            threading.Thread(target=check, daemon=True).start()

        win.after(1000, poll)

    def _open_web(self) -> None:
        port = 8787
        if CONFIG_PATH.exists():
            try:
                data = yaml.safe_load(CONFIG_PATH.read_text(encoding="utf-8")) or {}
                port = int((data.get("web") or {}).get("port", 8787))
            except Exception:  # noqa: BLE001
                pass
        webbrowser.open("http://127.0.0.1:{}/?token=change-me".format(port))

    # ------------------------------------------------------------------ 日志 / 退出

    def _setup_logging(self) -> None:
        root = logging.getLogger()
        root.setLevel(logging.INFO)
        root.addHandler(QueueHandler(self.log_queue))

    def _log(self, text: str) -> None:
        self.log_text.insert("end", text + "\n")
        self.log_text.see("end")

    def _drain_logs(self) -> None:
        while True:
            try:
                line = self.log_queue.get_nowait()
            except queue.Empty:
                break
            self._log(line)
        self.after(200, self._drain_logs)

    def _on_close(self) -> None:
        if self.bot_app:
            self.bot_app.request_stop()
        self.destroy()


def main() -> None:
    app = BotGUI()
    app.mainloop()


if __name__ == "__main__":
    main()
