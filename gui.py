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
import ctypes
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
from aibot import __version__ as APP_VERSION  # noqa: E402
from aibot.config import Config, load_dotenv  # noqa: E402
import installer  # noqa: E402
import napcat_config  # noqa: E402
import napcat_webui  # noqa: E402

CONFIG_PATH = APP_DIR / "config.yaml"
ENV_PATH = APP_DIR / ".env"

BG = "#0b0f12"
PANEL = "#111a1f"
FG = "#cfe6df"
MUTED = "#6f8b85"
ACCENT = "#35e0b0"
LINE = "#1d2d31"

# 常用 OpenAI 兼容服务预设：名称 -> (Base URL, 模型)
PRESETS = {
    "智谱 GLM": ("https://open.bigmodel.cn/api/paas/v4", "glm-5.3"),
    "DeepSeek": ("https://api.deepseek.com/v1", "deepseek-chat"),
    "OpenAI": ("https://api.openai.com/v1", "gpt-4o-mini"),
    "通义千问": ("https://dashscope.aliyuncs.com/compatible-mode/v1", "qwen-plus"),
    "Kimi": ("https://api.moonshot.cn/v1", "moonshot-v1-8k"),
    "硅基流动": ("https://api.siliconflow.cn/v1", "deepseek-ai/DeepSeek-V3"),
    "Ollama 本地": ("http://127.0.0.1:11434/v1", "qwen2.5"),
}


def persona_names() -> list[str]:
    """读取 personas/*.md 作为可选人格。"""
    for base in (APP_DIR / "personas", Path(getattr(sys, "_MEIPASS", APP_DIR)) / "personas"):
        if base.exists():
            names = sorted(path.stem for path in base.glob("*.md"))
            if names:
                return names
    return ["default"]


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
        self.title(f"AI-IM-Bot · QQ 助手配置向导 v{APP_VERSION}")
        self._scale = self._detect_scale()
        self.geometry(self._window_size(980, 700))
        self.minsize(int(900 * self._scale), int(640 * self._scale))
        self.configure(bg=BG)
        self._set_icon()

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

    def _detect_scale(self) -> float:
        """按系统 DPI 计算缩放比例，并把 Tk 的字体缩放同步过去。"""
        try:
            dpi = self.winfo_fpixels("1i")
            self.tk.call("tk", "scaling", dpi / 72.0)
            return max(1.0, dpi / 96.0)
        except Exception:  # noqa: BLE001
            return 1.0

    def _window_size(self, width: int, height: int) -> str:
        screen_w = self.winfo_screenwidth()
        screen_h = self.winfo_screenheight()
        w = min(int(width * self._scale), int(screen_w * 0.92))
        h = min(int(height * self._scale), int(screen_h * 0.9))
        return f"{w}x{h}"

    def _set_icon(self) -> None:
        try:
            base = Path(getattr(sys, "_MEIPASS", APP_DIR))
            self.iconphoto(True, tk.PhotoImage(file=str(base / "icon.png")))
        except Exception:  # noqa: BLE001 - 图标失败不影响使用
            pass

    def _build_style(self) -> None:
        style = ttk.Style(self)
        style.theme_use("clam")
        style.configure(".", background=BG, foreground=FG, fieldbackground="#0a1114")
        style.configure("TFrame", background=BG)
        style.configure("Panel.TFrame", background=PANEL)
        style.configure("TLabel", background=BG, foreground=FG)
        style.configure("Muted.TLabel", background=BG, foreground=MUTED)
        style.configure("PanelMuted.TLabel", background=PANEL, foreground=MUTED)
        style.configure("PanelTitle.TLabel", background=PANEL, foreground=ACCENT,
                        font=("Microsoft YaHei UI", 11, "bold"))
        style.configure("Title.TLabel", background=BG, foreground=ACCENT,
                        font=("Microsoft YaHei UI", 16, "bold"))
        style.configure("TButton", background="#132024", foreground=FG, borderwidth=0, padding=6)
        style.map("TButton", background=[("active", "#1b3036")])
        style.configure("Accent.TButton", background="#17453a", foreground=ACCENT)
        style.map("Accent.TButton", background=[("active", "#1f5a4a")])
        style.configure("Danger.TButton", background="#45201f", foreground="#ff8a80")
        style.map("Danger.TButton", background=[("active", "#5a2a28")])
        style.configure("TEntry", fieldbackground="#0a1114", foreground=FG,
                        insertcolor=FG, borderwidth=1)
        style.configure("TCombobox", fieldbackground="#0a1114", foreground=FG)

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
        container = ttk.Frame(parent)
        container.grid(row=0, column=0, sticky="nsew", padx=(0, 10))
        container.rowconfigure(0, weight=1)
        container.columnconfigure(0, weight=1)

        canvas = tk.Canvas(container, bg=PANEL, highlightthickness=0, bd=0)
        canvas.grid(row=0, column=0, sticky="nsew")
        scrollbar = ttk.Scrollbar(container, orient="vertical", command=canvas.yview)
        scrollbar.grid(row=0, column=1, sticky="ns")
        canvas.configure(yscrollcommand=scrollbar.set)

        left = ttk.Frame(canvas, style="Panel.TFrame", padding=14)
        window = canvas.create_window((0, 0), window=left, anchor="nw")
        left.bind("<Configure>", lambda _event: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.bind("<Configure>", lambda event: canvas.itemconfigure(window, width=event.width))

        def _on_wheel(event) -> None:
            canvas.yview_scroll(int(-event.delta / 120), "units")

        canvas.bind("<Enter>", lambda _event: canvas.bind_all("<MouseWheel>", _on_wheel))
        canvas.bind("<Leave>", lambda _event: canvas.unbind_all("<MouseWheel>"))

        left.columnconfigure(1, weight=1)

        row = 0

        def label(text: str) -> None:
            nonlocal row
            ttk.Label(left, text=text, style="PanelMuted.TLabel").grid(
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

        label("预设模型")
        self.preset_box = ttk.Combobox(left, state="readonly", values=list(PRESETS))
        self.preset_box.grid(row=row, column=1, sticky="ew", padx=(10, 0), pady=(8, 2))
        self.preset_box.bind("<<ComboboxSelected>>", self._apply_preset)
        row += 1

        label("人格")
        self.var_persona = tk.StringVar(value="default")
        self.persona_box = ttk.Combobox(left, textvariable=self.var_persona, state="readonly",
                                        values=persona_names())
        self.persona_box.grid(row=row, column=1, sticky="ew", padx=(10, 0), pady=(8, 2))
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
        ttk.Button(napcat_btns, text="快速安装", style="Accent.TButton",
                   command=self._quick_install).pack(side="left")
        ttk.Button(napcat_btns, text="启动 NapCat", command=self._launch_napcat).pack(side="left", padx=6)
        ttk.Button(napcat_btns, text="扫码登录", command=self._scan_qr).pack(side="left")

        napcat_btns2 = ttk.Frame(left, style="Panel.TFrame")
        napcat_btns2.grid(row=row, column=0, columnspan=2, sticky="ew", pady=(6, 0))
        row += 1
        ttk.Button(napcat_btns2, text="一键下载", command=self._download_napcat).pack(side="left")
        ttk.Button(napcat_btns2, text="修复下载", command=self._repair_download).pack(side="left", padx=6)
        ttk.Button(napcat_btns2, text="测速下载", command=self._speedtest_download).pack(side="left")
        ttk.Button(napcat_btns2, text="安装", command=self._install_napcat).pack(side="left", padx=6)

        napcat_btns3 = ttk.Frame(left, style="Panel.TFrame")
        napcat_btns3.grid(row=row, column=0, columnspan=2, sticky="ew", pady=(6, 0))
        row += 1
        ttk.Button(napcat_btns3, text="自动配置", command=self._auto_config).pack(side="left")
        ttk.Button(napcat_btns3, text="重启 NapCat", command=self._restart_napcat).pack(side="left", padx=6)
        ttk.Button(napcat_btns3, text="卸载", command=self._uninstall_napcat).pack(side="left")
        ttk.Button(napcat_btns3, text="WebUI", command=self._open_napcat_webui).pack(side="left", padx=6)

        tk.Label(left, text="首次使用：测速下载 → 安装 NapCat → 启动 → 扫码 → 自动配置\n"
                             "下载进度会显示在右侧「运行日志」里。",
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

        self.log_text = tk.Text(right, bg="#0a1114", fg="#9fb3ad", insertbackground=FG,
                                relief="flat", wrap="word", font=("Consolas", 10))
        self.log_text.grid(row=1, column=0, sticky="nsew")
        scroll = ttk.Scrollbar(right, orient="vertical", command=self.log_text.yview)
        scroll.grid(row=1, column=1, sticky="ns")
        self.log_text.configure(yscrollcommand=scroll.set)
        self.log_text.tag_configure("INFO", foreground="#9fb3ad")
        self.log_text.tag_configure("WARNING", foreground="#ffc857")
        self.log_text.tag_configure("ERROR", foreground="#ff6b6b")

    # ------------------------------------------------------------------ 配置

    def _apply_preset(self, event=None) -> None:
        name = self.preset_box.get()
        base, model = PRESETS.get(name, ("", ""))
        if base:
            self.var_base_url.set(base)
        if model:
            self.var_model.set(model)
        if name:
            self._log("已套用预设：{}（{} / {}）".format(name, base, model))

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
        self.var_persona.set(str(data.get("persona") or "default"))
        if self.var_persona.get() not in persona_names():
            self.var_persona.set("default")
        for name, (base, _model) in PRESETS.items():
            if self.var_base_url.get() == base:
                self.preset_box.set(name)
                break
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
            "persona": self.var_persona.get().strip() or "default",
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
                launcher = installer.download_napcat(progress=self._progress_logger("OneKey 包"))
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

    def _progress_logger(self, prefix: str = "下载中"):
        """把下载进度按 1% 打到运行日志（替代进度条）。"""
        state = {"pct": -1}

        def log(done: int, total: int) -> None:
            if not total:
                return
            pct = int(done * 100 / total)
            if pct > state["pct"]:
                state["pct"] = pct
                self.after(0, lambda p=pct, d=done, t=total: self._log(
                    "{} {}%（{:.1f}/{:.1f} MB）".format(
                        prefix, p, d / 1024 / 1024, t / 1024 / 1024
                    )
                ))

        return log

    def _repair_download(self) -> None:
        """重新下载完整的 NapCat.Shell.zip（安装器自带下载经常被截断）。"""
        def worker() -> None:
            try:
                self.after(0, lambda: self._log("正在下载完整的 NapCat.Shell.zip（约 34MB）…"))
                path = installer.download_shell_zip(progress=self._progress_logger("NapCat.Shell"))
                self.after(0, lambda: self._log("NapCat.Shell.zip 已修复：{}".format(path)))
                self.after(0, lambda: messagebox.showinfo(
                    "修复完成",
                    "NapCat.Shell.zip 已替换为完整包。\n现在点「安装 NapCat」。",
                ))
            except Exception as exc:  # noqa: BLE001
                self.after(0, lambda: messagebox.showerror("修复失败", str(exc)))

        self._log("开始修复 NapCat.Shell.zip…")
        threading.Thread(target=worker, daemon=True).start()

    def _speedtest_download(self) -> None:
        """测速所有下载源，选最快的下载 NapCat.Shell.zip。"""
        def worker() -> None:
            try:
                self.after(0, lambda: self._log("正在测速下载源（每个源拉 512KB）…"))
                sources = installer.release_sources()
                results = installer.measure_sources(sources)
                for name, _url, speed in results:
                    self.after(0, lambda n=name, s=speed: self._log(
                        "{}: {:.2f} MB/s".format(n, s) if s > 0 else "{}: 不可用".format(n)
                    ))
                best = max(results, key=lambda item: item[2])
                if best[2] <= 0:
                    raise RuntimeError("所有下载源都不可用")
                self.after(0, lambda n=best[0]: self._log("选择「{}」下载…".format(n)))
                path = installer.download_shell_zip(
                    progress=self._progress_logger("NapCat.Shell"), urls=[best[1]]
                )
                self.after(0, lambda: self._log("下载完成：{}".format(path)))
                self.after(0, lambda: messagebox.showinfo(
                    "下载完成",
                    "NapCat.Shell.zip 已就绪。\n现在点「安装 NapCat」。",
                ))
            except Exception as exc:  # noqa: BLE001
                self.after(0, lambda: messagebox.showerror("测速下载失败", str(exc)))

        threading.Thread(target=worker, daemon=True).start()

    def _quick_install(self) -> None:
        """一键准备：下载 OneKey → 测速下载 Shell.zip → 运行安装器。"""
        if not messagebox.askyesno(
            "快速安装",
            "将自动完成：\n"
            "1. 下载 NapCat 安装器（如果没有）\n"
            "2. 测速并下载完整的 NapCat.Shell.zip\n"
            "3. 运行安装器\n\n继续？",
        ):
            return

        def worker() -> None:
            try:
                if not (installer.napcat_dir() / "NapCatInstaller.exe").exists():
                    self.after(0, lambda: self._log("下载 NapCat 安装器…"))
                    installer.download_napcat(progress=self._progress_logger("OneKey 包"))

                if not installer.shell_zip_ok():
                    self.after(0, lambda: self._log("测速下载源…"))
                    sources = installer.release_sources()
                    results = installer.measure_sources(sources)
                    for name, _url, speed in results:
                        self.after(0, lambda n=name, s=speed: self._log(
                            "{}: {:.2f} MB/s".format(n, s) if s > 0 else "{}: 不可用".format(n)
                        ))
                    best = max(results, key=lambda item: item[2])
                    if best[2] <= 0:
                        raise RuntimeError("所有下载源都不可用，请手动下载")
                    self.after(0, lambda n=best[0]: self._log("选择「{}」下载…".format(n)))
                    installer.download_shell_zip(
                        progress=self._progress_logger("NapCat.Shell"), urls=[best[1]]
                    )

                installer.install_napcat()
                self.after(0, lambda: self._log("安装器已启动，请按控制台提示完成安装"))
                self.after(0, lambda: messagebox.showinfo(
                    "快速安装",
                    "安装器已打开。\n完成安装后点「启动 NapCat」，再扫码登录。",
                ))
            except Exception as exc:  # noqa: BLE001
                self.after(0, lambda: messagebox.showerror("快速安装失败", str(exc)))

        self._log("开始快速安装…")
        threading.Thread(target=worker, daemon=True).start()

    def _install_napcat(self) -> None:
        if not installer.shell_zip_ok():
            if messagebox.askyesno(
                "NapCat 包不完整",
                "检测到 NapCat.Shell.zip 缺失或损坏（安装器自带下载经常被截断）。\n"
                "要先修复下载吗？",
            ):
                self._repair_download()
                return
        try:
            installer.install_napcat()
            self._log("NapCat 安装器已打开，按控制台提示完成安装")
            messagebox.showinfo(
                "安装 NapCat",
                "已打开安装器控制台。\n"
                "等它自动检测 QQ 并完成安装后，回到本窗口点「启动 NapCat」。",
            )
        except Exception as exc:  # noqa: BLE001
            messagebox.showerror("安装失败", str(exc))

    def _launch_napcat(self) -> None:
        try:
            self.napcat_proc = installer.launch_napcat()
            self._log("NapCat 已启动，请扫码登录小号；WebUI 默认 127.0.0.1:6099")
            cfg = napcat_webui.find_webui_config()
            if cfg:
                token = cfg.get("token", "")
                port = cfg.get("port") or 6099
                self._log(f"NapCat WebUI：http://127.0.0.1:{port}/webui?token={token}")
        except Exception as exc:  # noqa: BLE001
            messagebox.showerror("启动失败", str(exc))

    def _open_napcat_webui(self) -> None:
        cfg = napcat_webui.find_webui_config()
        if not cfg:
            messagebox.showinfo("提示", "还没找到 NapCat 的 webui.json，请先启动一次 NapCat。")
            return
        port = cfg.get("port") or 6099
        token = cfg.get("token", "")
        webbrowser.open(f"http://127.0.0.1:{port}/webui?token={token}")

    def _auto_config(self) -> None:
        """把 3001 端口的 WebSocket 服务器写进 NapCat 配置。"""
        token = self.var_token.get().strip() or "aibot-token"

        def worker() -> None:
            try:
                files = napcat_config.apply_websocket_config(3001, token)
                if not files:
                    created = napcat_config.create_default_config(3001, token)
                    if created:
                        files = [created]
                if files:
                    listing = "\n".join(str(path) for path in files)
                    self.after(0, lambda: self._log("已自动配置 NapCat：\n" + listing))
                    self.after(0, lambda: messagebox.showinfo(
                        "自动配置完成",
                        "已写入以下配置：\n{}\n\n重启 NapCat 后生效（关闭后重新点「启动 NapCat」）。".format(listing),
                    ))
                else:
                    self.after(0, lambda: messagebox.showinfo(
                        "没找到配置文件",
                        "先启动 NapCat、扫码登录一次，让它在 config 目录生成 "
                        "onebot11_<QQ号>.json，再点这个按钮。",
                    ))
            except Exception as exc:  # noqa: BLE001
                self.after(0, lambda: messagebox.showerror("自动配置失败", str(exc)))

        self._log("正在写入 NapCat WebSocket 配置（127.0.0.1:3001）…")
        threading.Thread(target=worker, daemon=True).start()

    def _restart_napcat(self) -> None:
        if not messagebox.askyesno(
            "重启 NapCat",
            "会先关闭 QQ 和 NapCat，再重新启动。\nQQ 里未发送的消息会丢失，确定吗？",
        ):
            return

        def worker() -> None:
            try:
                self.after(0, lambda: self._log("正在重启 NapCat…"))
                installer.restart_napcat(close_qq=True)
                self.after(0, lambda: self._log("NapCat 已重新启动，请扫码登录"))
                self.after(0, self._check_napcat)
            except Exception as exc:  # noqa: BLE001
                self.after(0, lambda: messagebox.showerror("重启失败", str(exc)))

        threading.Thread(target=worker, daemon=True).start()

    def _uninstall_napcat(self) -> None:
        if not installer.is_installed():
            messagebox.showinfo("未安装", "没有检测到 NapCat 安装目录。")
            return
        if not messagebox.askyesno(
            "卸载 NapCat",
            "将停止 NapCat 启动器并删除安装目录：\n{}\n\n不会影响 QQ 本身，确定吗？".format(
                installer.napcat_dir()),
        ):
            return

        def worker() -> None:
            try:
                installer.uninstall_napcat()
                self.after(0, lambda: self._log("NapCat 已卸载"))
                self.after(0, self._check_napcat)
                self.after(0, lambda: messagebox.showinfo("完成", "NapCat 已卸载。"))
            except Exception as exc:  # noqa: BLE001
                self.after(0, lambda: messagebox.showerror(
                    "卸载失败",
                    "请先完全关闭 QQ 和 NapCat，再重试。\n\n{}".format(exc),
                ))

        self._log("正在卸载 NapCat…")
        threading.Thread(target=worker, daemon=True).start()

    def _scan_qr(self) -> None:
        """显示 NapCat 登录二维码（优先本地 cache/qrcode.png）。"""
        def worker():
            local = installer.find_qrcode_png()
            if local:
                try:
                    png = local.read_bytes()
                except OSError:
                    png = None
                if png:
                    self.after(0, lambda: self._show_qr(png, 6099, ""))
                    return

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
            # 登录后配置文件名里才带 QQ 号，这时自动写 WebSocket 配置最准
            self.after(1000, self._auto_config)

        def poll() -> None:
            def check() -> None:
                for _ in range(150):
                    try:
                        if session and napcat_webui.check_login(port, session):
                            self.after(0, done)
                            return
                        if not session and installer.port_open(3001):
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
        if "ERROR" in text:
            tag = "ERROR"
        elif "WARNING" in text:
            tag = "WARNING"
        else:
            tag = "INFO"
        self.log_text.insert("end", text + "\n", tag)
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


def _enable_high_dpi() -> None:
    """让进程声明 DPI 感知，避免 Windows 把界面位图拉伸导致发糊。"""
    if os.name != "nt":
        return
    # 优先 Per-Monitor V2（Win10 1703+）
    try:
        ctypes.windll.user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))
        return
    except Exception:  # noqa: BLE001
        pass
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
    except Exception:  # noqa: BLE001
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except Exception:  # noqa: BLE001
            pass


def main() -> None:
    _enable_high_dpi()
    app = BotGUI()
    app.mainloop()


if __name__ == "__main__":
    main()
