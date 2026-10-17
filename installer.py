"""NapCat 一键安装 / 启动辅助（给 GUI 用）。

NapCat 是 QQ 的 OneBot 协议端，需要单独下载。这里做三件事：
- 检测 3001 端口是否已连上；
- 把官方 Windows 一键包下载并解压到用户目录；
- 启动 NapCat 启动器。
"""

from __future__ import annotations

import os
import shutil
import subprocess
import urllib.request
import zipfile
from pathlib import Path

NAPCAT_URL = (
    "https://github.com/NapNeko/NapCatQQ/releases/latest/download/"
    "NapCat.Shell.Windows.OneKey.zip"
)
NAPCAT_RELEASE_PAGE = "https://github.com/NapNeko/NapCatQQ/releases"
LAUNCHER_NAMES = ("NapCatWinBootMain.exe", "launcher.bat", "NapCatWinBootMain.bat")


def napcat_dir() -> Path:
    base = os.environ.get("LOCALAPPDATA") or str(Path.home())
    return Path(base) / "ai-im-bot" / "napcat"


def find_launcher(root: Path | None = None) -> Path | None:
    root = root or napcat_dir()
    if not root.exists():
        return None
    for name in LAUNCHER_NAMES:
        direct = root / name
        if direct.exists():
            return direct
    for path in root.rglob("*"):
        if path.is_file() and path.name in LAUNCHER_NAMES:
            return path
    return None


def is_installed() -> bool:
    return find_launcher() is not None


def port_open(host: str = "127.0.0.1", port: int = 3001, timeout: float = 1.0) -> bool:
    import socket

    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def download_napcat(progress=None, target: Path | None = None) -> Path:
    """下载并解压 NapCat 一键包，返回解压目录。progress(done, total) 用于进度条。"""
    target = target or napcat_dir()
    target.mkdir(parents=True, exist_ok=True)
    archive = target / "napcat.zip"

    request = urllib.request.Request(NAPCAT_URL, headers={"User-Agent": "ai-im-bot-installer/1.0"})
    with urllib.request.urlopen(request, timeout=60) as resp:
        total = int(resp.headers.get("Content-Length") or 0)
        done = 0
        with archive.open("wb") as fh:
            while True:
                chunk = resp.read(1024 * 256)
                if not chunk:
                    break
                fh.write(chunk)
                done += len(chunk)
                if progress:
                    progress(done, total)

    with zipfile.ZipFile(archive) as zf:
        zf.extractall(target)
    archive.unlink(missing_ok=True)

    launcher = find_launcher(target)
    if launcher is None:
        raise RuntimeError("下载完成但没有找到启动器，请到 releases 页面手动下载")
    return launcher


def launch_napcat(launcher: Path | None = None) -> subprocess.Popen:
    """启动 NapCat（会拉起 QQ）。"""
    launcher = launcher or find_launcher()
    if launcher is None:
        raise RuntimeError("没有找到 NapCat 启动器")
    if launcher.suffix.lower() == ".bat":
        return subprocess.Popen(["cmd", "/c", str(launcher)], cwd=str(launcher.parent))
    return subprocess.Popen([str(launcher)], cwd=str(launcher.parent))


def open_release_page() -> None:
    import webbrowser

    webbrowser.open(NAPCAT_RELEASE_PAGE)


def clean_temp() -> None:
    """清理下载残留。"""
    archive = napcat_dir() / "napcat.zip"
    if archive.exists():
        try:
            archive.unlink()
        except OSError:
            pass
