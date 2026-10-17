"""AI-IM-Bot 安装程序。

功能：
- 默认安装到 C:\\Ai-im-bot（没有就创建），也可自定义路径
- 复制主程序 ai-im-bot.exe 和配置模板
- 在桌面创建快捷方式 Aibot
- 支持静默安装：python setup.py --silent --dir D:\\path

打包：
    python build_setup.py
产物：dist/ai-im-bot-setup.exe
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

APP_NAME = "AI-IM-Bot"
EXE_NAME = "ai-im-bot.exe"
SHORTCUT_NAME = "Aibot"
DEFAULT_DIR = r"C:\Ai-im-bot"

BG = "#0b0f12"
PANEL = "#111a1f"
FG = "#cfe6df"
MUTED = "#6f8b85"
ACCENT = "#35e0b0"


def resource(name: str) -> Path:
    """打包后从 _MEIPASS 找资源，源码运行时从当前目录或 dist/ 找。"""
    base = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
    path = base / name
    if path.exists():
        return path
    fallback = base / "dist" / name
    return fallback if fallback.exists() else path


def desktop_path() -> Path:
    """获取真实桌面路径（兼容 OneDrive 重定向）。"""
    try:
        output = subprocess.check_output(
            ["powershell", "-NoProfile", "-Command", "[Environment]::GetFolderPath('Desktop')"],
            text=True, timeout=10,
        ).strip()
        if output and Path(output).exists():
            return Path(output)
    except Exception:  # noqa: BLE001
        pass
    fallback = Path(os.path.expanduser("~")) / "Desktop"
    fallback.mkdir(parents=True, exist_ok=True)
    return fallback


def create_shortcut(target: Path, link: Path, workdir: Path) -> None:
    """用 WScript.Shell 创建 .lnk 快捷方式。"""
    script = (
        "$ws = New-Object -ComObject WScript.Shell; "
        f"$sc = $ws.CreateShortcut('{link}'); "
        f"$sc.TargetPath = '{target}'; "
        f"$sc.WorkingDirectory = '{workdir}'; "
        f"$sc.IconLocation = '{target}'; "
        "$sc.Save()"
    )
    subprocess.run(
        ["powershell", "-NoProfile", "-Command", script],
        check=True, capture_output=True, timeout=30,
    )


def install(target_dir: Path, log=print, make_shortcut: bool = True) -> tuple[Path, Path | None]:
    """执行安装，返回 (安装目录, 快捷方式路径)。"""
    source_exe = resource(EXE_NAME)
    if not source_exe.exists():
        raise RuntimeError(f"安装包损坏：缺少 {EXE_NAME}")

    target_dir.mkdir(parents=True, exist_ok=True)
    log(f"安装目录：{target_dir}")

    target_exe = target_dir / EXE_NAME
    shutil.copy2(source_exe, target_exe)
    log(f"已复制主程序：{target_exe}")

    for name in ("config.example.yaml", "README.md", "LICENSE"):
        source = resource(name)
        if source.exists():
            shutil.copy2(source, target_dir / name)
            log(f"已复制 {name}")

    if not make_shortcut:
        return target_dir, None

    link = desktop_path() / f"{SHORTCUT_NAME}.lnk"
    create_shortcut(target_exe, link, target_dir)
    log(f"已创建桌面快捷方式：{link}")
    return target_dir, link


def is_installed(target_dir: Path) -> bool:
    return (target_dir / EXE_NAME).exists()


def uninstall(target_dir: Path, log=print) -> None:
    """关闭主程序，删除桌面快捷方式和安装目录。"""
    subprocess.run(
        ["taskkill", "/F", "/IM", EXE_NAME],
        capture_output=True, text=True, check=False, timeout=20,
    )
    link = desktop_path() / f"{SHORTCUT_NAME}.lnk"
    if link.exists():
        try:
            link.unlink()
            log(f"已删除快捷方式：{link}")
        except OSError:
            log(f"快捷方式删除失败（可手动删除）：{link}")
    if target_dir.exists():
        shutil.rmtree(target_dir)
        log(f"已删除安装目录：{target_dir}")


class SetupGUI(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title(f"{APP_NAME} 安装程序")
        self.geometry("620x480")
        self.resizable(False, False)
        self.configure(bg=BG)
        self._set_style()
        self._build()
        self.protocol("WM_DELETE_WINDOW", self.destroy)

    def _set_style(self) -> None:
        style = ttk.Style(self)
        style.theme_use("clam")
        style.configure(".", background=BG, foreground=FG)
        style.configure("TFrame", background=BG)
        style.configure("Panel.TFrame", background=PANEL)
        style.configure("TLabel", background=BG, foreground=FG)
        style.configure("Muted.TLabel", background=PANEL, foreground=MUTED)
        style.configure("Title.TLabel", background=BG, foreground=ACCENT,
                        font=("Microsoft YaHei UI", 16, "bold"))
        style.configure("TButton", background="#132024", foreground=FG, padding=6)
        style.map("TButton", background=[("active", "#1b3036")])
        style.configure("Accent.TButton", background="#17453a", foreground=ACCENT)
        style.configure("Danger.TButton", background="#45201f", foreground="#ff8a80")
        style.map("Danger.TButton", background=[("active", "#5a2a28")])
        style.configure("TEntry", fieldbackground="#0a1114", foreground=FG, insertcolor=FG)

    def _build(self) -> None:
        tk.Label(self, text=APP_NAME, bg=BG, fg=ACCENT,
                 font=("Microsoft YaHei UI", 18, "bold")).pack(anchor="w", padx=24, pady=(20, 2))
        tk.Label(self, text="QQ 智能助手 · 安装向导", bg=BG, fg=MUTED).pack(anchor="w", padx=24)

        panel = ttk.Frame(self, style="Panel.TFrame", padding=16)
        panel.pack(fill="x", padx=24, pady=16)

        ttk.Label(panel, text="安装路径", style="Muted.TLabel").pack(anchor="w")
        row = ttk.Frame(panel, style="Panel.TFrame")
        row.pack(fill="x", pady=(6, 0))
        self.var_path = tk.StringVar(value=DEFAULT_DIR)
        entry = ttk.Entry(row, textvariable=self.var_path)
        entry.pack(side="left", fill="x", expand=True)
        ttk.Button(row, text="浏览…", command=self._browse).pack(side="left", padx=(8, 0))

        ttk.Label(panel, text="会在该目录创建 ai-im-bot.exe，并在桌面生成 Aibot 快捷方式。",
                  style="Muted.TLabel").pack(anchor="w", pady=(10, 0))

        footer = ttk.Frame(self)
        footer.pack(side="bottom", fill="x", padx=24, pady=(0, 18))
        self.install_btn = ttk.Button(footer, text="安装", style="Accent.TButton",
                                      command=self._install)
        self.install_btn.pack(side="left")
        ttk.Button(footer, text="取消", command=self.destroy).pack(side="left", padx=8)

        self.status = tk.Text(self, height=5, bg="#0a1114", fg=MUTED, relief="flat",
                              font=("Consolas", 9))
        self.status.pack(fill="both", expand=True, padx=24, pady=(0, 10))

        self._mode = "install"
        self._refresh_mode()
        self.var_path.trace_add("write", lambda *_: self._refresh_mode())

    def _refresh_mode(self) -> None:
        try:
            target = Path(self.var_path.get().strip() or DEFAULT_DIR)
        except Exception:  # noqa: BLE001
            return
        installed = is_installed(target)
        if installed and self._mode != "uninstall":
            self._mode = "uninstall"
            self.install_btn.configure(text="卸载", style="Danger.TButton", command=self._uninstall)
            self._log(f"检测到已安装：{target}，点击「卸载」可移除。")
        elif not installed and self._mode != "install":
            self._mode = "install"
            self.install_btn.configure(text="安装", style="Accent.TButton", command=self._install)

    def _uninstall(self) -> None:
        target = Path(self.var_path.get().strip() or DEFAULT_DIR)
        if not messagebox.askyesno(
            "卸载",
            "将删除：\n{}\n桌面快捷方式 Aibot\n\n配置和聊天记录也会一起删除，确定吗？".format(target),
        ):
            return
        self.install_btn.configure(state="disabled")
        try:
            uninstall(target, log=self._log)
            messagebox.showinfo("卸载完成", "已卸载。")
            self._refresh_mode()
        except Exception as exc:  # noqa: BLE001
            messagebox.showerror("卸载失败", "请先关闭 Ai-im-bot 程序后重试。\n\n{}".format(exc))
        finally:
            self.install_btn.configure(state="normal")

    def _browse(self) -> None:
        chosen = filedialog.askdirectory(initialdir=self.var_path.get() or DEFAULT_DIR)
        if chosen:
            self.var_path.set(chosen)

    def _log(self, text: str) -> None:
        self.status.insert("end", text + "\n")
        self.status.see("end")
        self.update_idletasks()

    def _install(self) -> None:
        self.install_btn.configure(state="disabled")
        try:
            target = Path(self.var_path.get().strip() or DEFAULT_DIR)
            _, link = install(target, log=self._log)
            if messagebox.askyesno("安装完成", f"已安装到：\n{target}\n\n桌面快捷方式：{link.name}\n\n现在启动吗？"):
                subprocess.Popen([str(target / EXE_NAME)], cwd=str(target))
            self.destroy()
        except PermissionError:
            messagebox.showerror("权限不足", "无法写入目标目录。\n请右键安装程序 →「以管理员身份运行」。")
        except Exception as exc:  # noqa: BLE001
            messagebox.showerror("安装失败", str(exc))
        finally:
            self.install_btn.configure(state="normal")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--silent", action="store_true", help="静默安装，不弹界面")
    parser.add_argument("--dir", default=DEFAULT_DIR, help="安装目录")
    parser.add_argument("--no-shortcut", action="store_true", help="不创建桌面快捷方式（测试用）")
    args = parser.parse_args()

    if args.silent:
        try:
            target, link = install(Path(args.dir), make_shortcut=not args.no_shortcut)
            print(f"OK: {target}")
            print(f"SHORTCUT: {link}")
            return 0
        except Exception as exc:  # noqa: BLE001
            print(f"FAIL: {exc}")
            return 1

    app = SetupGUI()
    app.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
