# -*- coding: utf-8 -*-
"""校准工具：全屏半透明遮罩，拖拽框选游戏画面区域，保存到 config.json

两种用法：
- 独立运行：`python tools/calibrate.py`（写仓库根目录 config.json）
- 被主程序进程内调用：`main(config_path=..., parent=主窗口)`
  此时在主窗口所属的 Tk 解释器里创建 Toplevel 遮罩，
  避免嵌套第二个 Tk 解释器导致事件循环卡死。
"""
import json
import os
import sys
import tkinter as tk

try:
    import ctypes
    ctypes.windll.shcore.SetProcessDpiAwareness(2)
except Exception:
    try:
        ctypes.windll.user32.SetProcessDPIAware()
    except Exception:
        pass

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # 仓库根目录
CONFIG_PATH = os.path.join(ROOT, "config.json")


def main(config_path=None, parent=None):
    """框选游戏区域并保存到 config_path。

    parent 为 None 时创建独立 tk.Tk 并自跑 mainloop（命令行用法）；
    传入 parent（主程序的 tk 根窗口）时创建 Toplevel 遮罩，
    用 wait_window 阻塞直到框选结束，不新建解释器。
    """
    if config_path is None:
        config_path = CONFIG_PATH

    if parent is None:
        top = tk.Tk()
    else:
        top = tk.Toplevel(parent)
    top.attributes("-fullscreen", True)
    top.attributes("-alpha", 0.28)
    top.attributes("-topmost", True)
    top.configure(bg="black")
    top.configure(cursor="crosshair")

    cv = tk.Canvas(top, bg="black", highlightthickness=0)
    cv.pack(fill="both", expand=True)
    tip = cv.create_text(0, 0, text="按住鼠标左键拖拽，框选游戏画面区域；按 ESC 取消",
                         fill="yellow", font=("Microsoft YaHei", 22, "bold"))

    def center_text(e=None):
        cv.coords(tip, top.winfo_screenwidth() // 2, 60)
    top.bind("<Configure>", center_text)

    rect_id = None
    start = {"x": 0, "y": 0}

    def on_press(e):
        nonlocal rect_id
        start["x"], start["y"] = e.x, e.y
        rect_id = cv.create_rectangle(e.x, e.y, e.x, e.y, outline="lime", width=2)

    def on_drag(e):
        if rect_id:
            cv.coords(rect_id, start["x"], start["y"], e.x, e.y)

    def on_release(e):
        x0, y0 = min(start["x"], e.x), min(start["y"], e.y)
        x1, y1 = max(start["x"], e.x), max(start["y"], e.y)
        if x1 - x0 < 50 or y1 - y0 < 50:
            return
        cfg = {}
        if os.path.exists(config_path):
            try:
                with open(config_path, "r", encoding="utf-8") as f:
                    cfg = json.load(f)
            except Exception:
                pass
        cfg["region"] = {"left": x0, "top": y0, "width": x1 - x0, "height": y1 - y0}
        with open(config_path, "w", encoding="utf-8") as f:
            json.dump(cfg, f, ensure_ascii=False, indent=2)
        top.destroy()

    def on_esc(e):
        top.destroy()

    cv.bind("<ButtonPress-1>", on_press)
    cv.bind("<B1-Motion>", on_drag)
    cv.bind("<ButtonRelease-1>", on_release)
    top.bind("<Escape>", on_esc)

    if parent is None:
        top.mainloop()
    else:
        # 拾取焦点并等待遮罩关闭（模态），期间主程序事件循环照常运转
        top.wait_visibility()
        top.focus_force()
        try:
            top.grab_set()
        except tk.TclError:
            pass
        parent.wait_window(top)


if __name__ == "__main__":
    main()
