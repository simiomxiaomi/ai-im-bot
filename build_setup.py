"""打包安装程序（setup.exe）。

前置：先运行 build_exe.py 生成 dist/ai-im-bot.exe。
产物：dist/ai-im-bot-setup.exe
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent


def main() -> int:
    main_exe = ROOT / "dist" / "ai-im-bot.exe"
    if not main_exe.exists():
        print("缺少 dist/ai-im-bot.exe，请先运行 build_exe.py")
        return 1

    sep = ";" if os.name == "nt" else ":"
    args = [
        sys.executable, "-m", "PyInstaller",
        "--noconfirm", "--clean",
        "--onefile", "--windowed",
        "--name", "ai-im-bot-setup",
        "--icon", str(ROOT / "icon.ico"),
        "--manifest", str(ROOT / "app.manifest"),
        "--add-data", f"{main_exe}{sep}.",
        "--add-data", f"{ROOT / 'config.example.yaml'}{sep}.",
        "--add-data", f"{ROOT / 'README.md'}{sep}.",
        "--add-data", f"{ROOT / 'LICENSE'}{sep}.",
        str(ROOT / "setup.py"),
    ]
    print("运行：", " ".join(args[:8]), "…")
    result = subprocess.run(args, cwd=str(ROOT))
    if result.returncode != 0:
        return result.returncode

    setup_exe = ROOT / "dist" / "ai-im-bot-setup.exe"
    if setup_exe.exists():
        print(f"\n打包完成：{setup_exe}（{setup_exe.stat().st_size / 1024 / 1024:.1f} MB）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
