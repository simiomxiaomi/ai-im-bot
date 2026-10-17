"""微信窗口 UIA 结构探测工具。

用途：
1. 判断当前微信是否具备可读的 UI Automation 树；
2. 微信 3.9 下打印控件层级，便于调整 wechat_uia.py 里的查找条件。

用法：
    pip install uiautomation
    python tools/probe_wechat.py            # 打印 4 层控件树
    python tools/probe_wechat.py --depth 6  # 更深
"""

from __future__ import annotations

import argparse
import sys


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--depth", type=int, default=4)
    parser.add_argument("--limit", type=int, default=40, help="每个节点最多打印多少个子节点")
    args = parser.parse_args()

    try:
        import uiautomation as auto
    except ImportError:
        print("未安装 uiautomation：pip install uiautomation")
        return 2

    candidates = [
        {"ClassName": "WeChatMainWndForPC", "searchDepth": 1},
        {"ClassName": "Qt51514QWindowIcon", "searchDepth": 1},
        {"Name": "微信", "searchDepth": 1},
    ]
    win = None
    for kwargs in candidates:
        candidate = auto.WindowControl(**kwargs)
        if candidate.Exists(1, 0.3):
            win = candidate
            break

    if win is None:
        print("未找到微信窗口：请先启动并登录微信。")
        return 1

    print(f"窗口：Name={win.Name!r} Class={win.ClassName!r} PID={win.ProcessId}")
    children = win.GetChildren()
    print(f"直接子控件：{len(children)} 个")

    if not children:
        print("\n>>> 结论：UIA 树为空，这是微信 4.x（MMUI 自绘）。")
        print(">>> uiautomation / wxauto / 本项目 wechat 适配器都无法工作。")
        print(">>> 建议改用 QQ + OneBot（见 README）。")
        return 1

    count = 0

    def walk(control, depth: int) -> None:
        nonlocal count
        if depth > args.depth or count > 2000:
            return
        for child in control.GetChildren()[: args.limit]:
            count += 1
            name = (child.Name or "").replace("\n", " / ")[:60]
            print(
                f"{'  ' * depth}[{child.ControlTypeName}] Name={name!r} "
                f"AutoId={child.AutomationId!r} Class={child.ClassName!r}"
            )
            walk(child, depth + 1)

    walk(win, 0)
    print(f"\n共打印 {count} 个控件。若结构可用，把 ClassName/AutomationId 填进 wechat_uia.py。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
