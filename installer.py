"""NapCat 一键安装 / 启动辅助（给 GUI 用）。

NapCat 是 QQ 的 OneBot 协议端，需要单独下载。这里做三件事：
- 检测 3001 端口是否已连上；
- 把官方 Windows 一键包下载并解压到用户目录；
- 启动 NapCat 启动器。
"""

from __future__ import annotations

import concurrent.futures
import json
import os
import shutil
import subprocess
import threading
import time
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


SHELL_ZIP_MIN_BYTES = 20 * 1024 * 1024  # 完整包约 34MB，低于这个值视为损坏


def shell_zip_path() -> Path:
    return napcat_dir() / "NapCat.Shell.zip"


def shell_zip_ok() -> bool:
    """NapCat.Shell.zip 是否完整（安装器自带下载经常被截断）。"""
    path = shell_zip_path()
    try:
        return path.exists() and path.stat().st_size >= SHELL_ZIP_MIN_BYTES
    except OSError:
        return False


def release_sources(timeout: float = 30) -> list[tuple[str, str]]:
    """返回 NapCat.Shell.zip 的下载源列表（名称, URL）。"""
    api = "https://api.github.com/repos/NapNeko/NapCatQQ/releases/latest"
    request = urllib.request.Request(api, headers={"User-Agent": "ai-im-bot-installer/1.0"})
    with urllib.request.urlopen(request, timeout=timeout) as resp:
        data = json.loads(resp.read().decode("utf-8"))

    asset = next(
        (a for a in data.get("assets", []) if a.get("name") == "NapCat.Shell.zip"),
        None,
    )
    if not asset:
        raise RuntimeError("没有在最新 release 里找到 NapCat.Shell.zip")

    direct = asset["browser_download_url"]
    return [
        ("GitHub 直连", direct),
        ("ghproxy.net", "https://ghproxy.net/" + direct),
        ("mirror.ghproxy.com", "https://mirror.ghproxy.com/" + direct),
        ("ghfast.top", "https://ghfast.top/" + direct),
        ("gh-proxy.com", "https://gh-proxy.com/" + direct),
    ]


def measure(url: str, sample: int = 512 * 1024, timeout: float = 10.0) -> float:
    """用 Range 请求拉 512KB，返回 MB/s（失败返回 0）。"""
    request = urllib.request.Request(
        url,
        headers={"User-Agent": "Mozilla/5.0", "Range": f"bytes=0-{sample - 1}"},
    )
    started = time.time()
    with urllib.request.urlopen(request, timeout=timeout) as resp:
        data = resp.read(sample)
    elapsed = max(0.001, time.time() - started)
    return len(data) / elapsed / 1024 / 1024


def measure_sources(sources, sample: int = 512 * 1024) -> list[tuple[str, str, float]]:
    """逐个测速，返回 [(名称, URL, MB/s)]。"""
    results = []
    for name, url in sources:
        try:
            speed = measure(url, sample)
        except Exception:  # noqa: BLE001
            speed = 0.0
        results.append((name, url, speed))
    return results


def _probe_range(url: str) -> tuple[int, bool]:
    """探测文件大小以及是否支持 Range。"""
    request = urllib.request.Request(
        url, headers={"User-Agent": "Mozilla/5.0", "Range": "bytes=0-0"}
    )
    with urllib.request.urlopen(request, timeout=20) as resp:
        content_range = resp.headers.get("Content-Range") or ""
        total = 0
        if "/" in content_range:
            try:
                total = int(content_range.rsplit("/", 1)[1])
            except ValueError:
                total = 0
        if not total:
            total = int(resp.headers.get("Content-Length") or 0)
        return total, resp.status == 206


def _download_single(url: str, dest: Path, progress=None) -> None:
    request = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(request, timeout=60) as resp, dest.open("wb") as fh:
        total = int(resp.headers.get("Content-Length") or 0)
        done = 0
        while True:
            chunk = resp.read(1024 * 128)
            if not chunk:
                break
            fh.write(chunk)
            done += len(chunk)
            if progress:
                progress(done, total)


def _download_multithread(url: str, dest: Path, progress=None, threads: int = 8) -> None:
    """HTTP Range 分块多线程下载；不支持 Range 的源回退单线程。"""
    total, supports_range = _probe_range(url)
    if not supports_range or total < 2 * 1024 * 1024:
        _download_single(url, dest, progress)
        return

    threads = max(1, min(threads, 16))
    chunk_size = total // threads
    ranges = []
    for index in range(threads):
        start = index * chunk_size
        end = total - 1 if index == threads - 1 else (index + 1) * chunk_size - 1
        ranges.append((start, end))

    parts = [dest.parent / f"{dest.name}.part{index}" for index in range(threads)]
    counter = [0]
    lock = threading.Lock()

    def worker(index: int, start: int, end: int) -> None:
        request = urllib.request.Request(
            url,
            headers={"User-Agent": "Mozilla/5.0", "Range": f"bytes={start}-{end}"},
        )
        with urllib.request.urlopen(request, timeout=60) as resp, parts[index].open("wb") as fh:
            while True:
                chunk = resp.read(64 * 1024)
                if not chunk:
                    break
                fh.write(chunk)
                with lock:
                    counter[0] += len(chunk)
                    done = counter[0]
                if progress:
                    progress(done, total)

    with concurrent.futures.ThreadPoolExecutor(max_workers=threads) as pool:
        futures = [pool.submit(worker, i, s, e) for i, (s, e) in enumerate(ranges)]
        for future in futures:
            future.result()

    with dest.open("wb") as out:
        for part in parts:
            with part.open("rb") as fh:
                shutil.copyfileobj(fh, out)
            part.unlink(missing_ok=True)


def download_shell_zip(progress=None, urls=None) -> Path:
    """下载完整的 NapCat.Shell.zip；urls 为空时自动测速选最快。"""
    if urls is None:
        urls = [url for _, url in release_sources()]

    dest = shell_zip_path()
    dest.parent.mkdir(parents=True, exist_ok=True)

    last_error: Exception | None = None
    for url in urls:
        try:
            _download_multithread(url, dest, progress)
            if shell_zip_ok():
                return dest
            last_error = RuntimeError(f"下载不完整（{dest.stat().st_size} 字节）")
        except Exception as exc:  # noqa: BLE001 - 换下一个镜像
            last_error = exc

    raise RuntimeError(f"下载失败：{last_error}")


def install_napcat() -> subprocess.Popen:
    """运行 NapCatInstaller.exe（新开控制台，按提示完成安装）。"""
    exe = napcat_dir() / "NapCatInstaller.exe"
    if not exe.exists():
        raise RuntimeError("没有找到 NapCatInstaller.exe，请先点「一键下载」")
    # 清理上次解压残留，否则安装器移动 versions 目录会报「拒绝访问」
    for residue in napcat_dir().glob("NapCat.*.Shell"):
        if residue.is_dir():
            shutil.rmtree(residue, ignore_errors=True)
    flags = subprocess.CREATE_NEW_CONSOLE if os.name == "nt" else 0
    return subprocess.Popen([str(exe)], cwd=str(exe.parent), creationflags=flags)


def launch_napcat(launcher: Path | None = None) -> subprocess.Popen:
    """启动 NapCat（会拉起 QQ）。没安装时自动打开安装器。"""
    launcher = launcher or find_launcher()
    if launcher is None:
        if (napcat_dir() / "NapCatInstaller.exe").exists():
            return install_napcat()
        raise RuntimeError("没有找到 NapCat 启动器，请先点「一键下载」")

    if launcher.suffix.lower() == ".bat":
        return subprocess.Popen(["cmd", "/c", str(launcher)], cwd=str(launcher.parent),
                                creationflags=subprocess.CREATE_NEW_CONSOLE if os.name == "nt" else 0)
    flags = subprocess.CREATE_NEW_CONSOLE if os.name == "nt" else 0
    return subprocess.Popen([str(launcher)], cwd=str(launcher.parent), creationflags=flags)


def open_release_page() -> None:
    import webbrowser

    webbrowser.open(NAPCAT_RELEASE_PAGE)


def restart_napcat(close_qq: bool = True, wait: float = 2.0) -> subprocess.Popen:
    """重启 NapCat：停掉启动器（可选连 QQ 一起），等待后重新启动。"""
    for name in ("NapCatWinBootMain.exe", "NapCatWinBootMain"):
        subprocess.run(
            ["taskkill", "/F", "/IM", name],
            capture_output=True, text=True, check=False,
        )
    if close_qq:
        subprocess.run(
            ["taskkill", "/F", "/IM", "QQ.exe"],
            capture_output=True, text=True, check=False,
        )
    time.sleep(max(0.0, wait))
    return launch_napcat()


def uninstall_napcat() -> None:
    """停止 NapCat 启动器进程并删除安装目录。不会动 QQ 本身。"""
    for name in ("NapCatWinBootMain.exe", "NapCatWinBootMain", "NapCat.Shell.exe"):
        subprocess.run(
            ["taskkill", "/F", "/IM", name],
            capture_output=True, text=True, check=False,
        )
    target = napcat_dir()
    if not target.exists():
        return
    shutil.rmtree(target)


def clean_temp() -> None:
    """清理下载残留。"""
    archive = napcat_dir() / "napcat.zip"
    if archive.exists():
        try:
            archive.unlink()
        except OSError:
            pass
