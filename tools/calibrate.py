# -*- coding: utf-8 -*-
"""校准工具：全屏半透明遮罩，拖拽框选游戏画面区域，保存到 config.json"""
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


def main():
    root = tk.Tk()
    root.attributes("-fullscreen", True)
    root.attributes("-alpha", 0.28)
    root.attributes("-topmost", True)
    root.configure(bg="black")
    root.configure(cursor="crosshair")

    cv = tk.Canvas(root, bg="black", highlightthickness=0)
    cv.pack(fill="both", expand=True)
    tip = cv.create_text(0, 0, text="按住鼠标左键拖拽，框选游戏画面区域；按 ESC 取消",
                         fill="yellow", font=("Microsoft YaHei", 22, "bold"))

    def center_text(e=None):
        cv.coords(tip, root.winfo_screenwidth() // 2, 60)
    root.bind("<Configure>", center_text)

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
        if os.path.exists(CONFIG_PATH):
            try:
                with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                    cfg = json.load(f)
            except Exception:
                pass
        cfg["region"] = {"left": x0, "top": y0, "width": x1 - x0, "height": y1 - y0}
        with open(CONFIG_PATH, "w", encoding="utf-8") as f:
            json.dump(cfg, f, ensure_ascii=False, indent=2)
        root.destroy()

    def on_esc(e):
        root.destroy()

    cv.bind("<ButtonPress-1>", on_press)
    cv.bind("<B1-Motion>", on_drag)
    cv.bind("<ButtonRelease-1>", on_release)
    root.bind("<Escape>", on_esc)
    root.mainloop()


if __name__ == "__main__":
    main()
