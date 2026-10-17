"""不依赖 pip 的依赖引导脚本：直接下载 wheel 并解压到 vendor/。

用途：本机路径含中文时，pip 的临时目录在 Windows 上会出现编码错误。
用法：python tools/bootstrap_deps.py [--vendor vendor] [--index https://pypi.org/pypi]
然后：set PYTHONPATH=vendor   （PowerShell: $env:PYTHONPATH="vendor"）
"""

from __future__ import annotations

import argparse
import contextlib
import json
import platform
import sys
import urllib.request
import zipfile
from pathlib import Path

PACKAGES = [
    "websockets",
    "httpx",
    "httpcore",
    "h11",
    "certifi",
    "anyio",
    "idna",
    "sniffio",
    "typing_extensions",
    "PyYAML",
    # 打包 exe 用（平时运行不需要）
    "pyinstaller",
    "pyinstaller-hooks-contrib",
    "altgraph",
    "pefile",
    "pywin32-ctypes",
    "packaging",
]


def _score(filename: str, py_tag: str) -> int:
    """选出与当前解释器兼容的 wheel：本版本 cp 标签 > 纯 Python > abi3。"""
    name = filename.lower()
    if f"cp{py_tag}" in name and "win_amd64" in name:
        return 50
    if "py3-none-any" in name:
        return 40
    # PyInstaller 等纯 Python + 平台限定的 wheel，例如 pyinstaller-6.x-py3-none-win_amd64.whl
    if "py3-none-win_amd64" in name or "py2.py3-none-win_amd64" in name:
        return 45
    if "abi3" in name and "win_amd64" in name:
        return 30
    return 0


def pick_wheel(files: list[dict], package: str, py_tag: str) -> dict | None:
    candidates = [f for f in files if f["filename"].endswith(".whl")]
    tagged = [(f, _score(f["filename"], py_tag)) for f in candidates]
    tagged = [(f, s) for f, s in tagged if s > 0]
    if not tagged:
        return None
    tagged.sort(key=lambda item: item[1], reverse=True)
    return tagged[0][0]


def download(url: str, dest: Path) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    request = urllib.request.Request(url, headers={"User-Agent": "ai-im-bot-bootstrap/1.0"})
    with urllib.request.urlopen(request, timeout=60) as resp, dest.open("wb") as fh:
        while True:
            chunk = resp.read(1024 * 256)
            if not chunk:
                break
            fh.write(chunk)


def main() -> int:
    with contextlib.suppress(Exception):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser()
    parser.add_argument("--vendor", default="vendor")
    parser.add_argument("--index", default="https://pypi.org/pypi")
    parser.add_argument("--python", default=f"{sys.version_info.major}.{sys.version_info.minor}")
    parser.add_argument("--only", default="", help="逗号分隔，只处理这些包")
    args = parser.parse_args()

    packages = PACKAGES
    if args.only:
        wanted = {p.strip().lower() for p in args.only.split(",") if p.strip()}
        packages = [p for p in PACKAGES if p.lower() in wanted]

    vendor = Path(args.vendor).resolve()
    wheels = vendor / "_wheels"
    wheels.mkdir(parents=True, exist_ok=True)
    print(f"[*] 目标目录：{vendor}")
    print(f"Python {args.python} / {platform.machine()}")

    ok = 0
    for package in packages:
        try:
            with urllib.request.urlopen(f"{args.index}/{package}/json", timeout=30) as resp:
                meta = json.load(resp)
        except Exception as exc:  # noqa: BLE001
            print(f"[FAIL] {package}: 获取元数据失败 {exc}")
            continue
        wheel = pick_wheel(meta.get("urls", []), package, args.python.replace(".", ""))
        if wheel is None:
            print(f"[WARN] {package}: 没有找到可用 wheel，跳过")
            continue
        target = wheels / wheel["filename"]
        if not target.exists():
            download(wheel["url"], target)
        with zipfile.ZipFile(target) as zf:
            zf.extractall(vendor)
        ok += 1
        print(f"[ OK ] {package:<18} {wheel['filename']}")

    print(f"\n完成 {ok}/{len(packages)} 个包。设置 PYTHONPATH={vendor} 后即可运行。")
    return 0 if ok == len(packages) else 1


if __name__ == "__main__":
    raise SystemExit(main())


