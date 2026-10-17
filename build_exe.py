"""打包 GUI 为单文件 exe（PyInstaller）。

用法：
    $env:PYTHONPATH = "vendor"
    python build_exe.py

产物：dist/ai-im-bot.exe
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent


def main() -> int:
    if not (ROOT / "aibot" / "web" / "index.html").exists():
        print("找不到 aibot/web/index.html")
        return 1

    sep = ";" if os.name == "nt" else ":"
    args = [
        sys.executable, "-m", "PyInstaller",
        "--noconfirm", "--clean",
        "--onefile", "--windowed",
        "--name", "ai-im-bot",
        "--icon", str(ROOT / "icon.ico"),
        "--add-data", f"{ROOT / 'icon.png'}{sep}.",
        "--add-data", f"{ROOT / 'aibot' / 'web' / 'index.html'}{sep}aibot/web",
        "--hidden-import", "aibot.adapters.onebot",
        "--hidden-import", "aibot.adapters.mock",
        "--hidden-import", "aibot.web.server",
        "--hidden-import", "yaml",
        "--hidden-import", "websockets",
        "--hidden-import", "httpx",
        str(ROOT / "gui.py"),
    ]
    print("运行：", " ".join(args[:8]), "…")
    result = subprocess.run(args, cwd=str(ROOT))
    if result.returncode != 0:
        return result.returncode

    exe = ROOT / "dist" / "ai-im-bot.exe"
    if exe.exists():
        print(f"\n打包完成：{exe}（{exe.stat().st_size / 1024 / 1024:.1f} MB）")
        # 把配置样例放到 exe 旁边，方便首次运行参考
        sample = ROOT / "config.example.yaml"
        if sample.exists():
            shutil.copy2(sample, exe.parent / "config.example.yaml")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
