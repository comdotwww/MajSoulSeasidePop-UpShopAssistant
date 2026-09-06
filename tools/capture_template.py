# -*- coding: utf-8 -*-
"""模板补录工具：全屏遮罩上拖拽框选，把选中区域保存为识别模板。

用法：
  python capture_template.py 模板名
例如银房间出现了带 + 的桌子：
  python capture_template.py table_plus_silver2
保存后重启助理即可生效。
"""
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

import cv2
import numpy as np
import mss

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # 仓库根目录


def main():
    if len(sys.argv) < 2:
        print("用法: python capture_template.py 模板名")
        sys.exit(1)
    name = sys.argv[1]

    # 先截全屏
    with mss.mss() as sct:
        mon = sct.monitors[1]
        shot = np.array(sct.grab(mon), dtype=np.uint8)
    img = cv2.cvtColor(shot, cv2.COLOR_BGRA2BGR)

    root = tk.Tk()
    root.attributes("-fullscreen", True)
    root.attributes("-topmost", True)
    root.configure(cursor="crosshair")
    cv = tk.Canvas(root, bg="black", highlightthickness=0)
    cv.pack(fill="both", expand=True)
    photo = tk.PhotoImage(width=mon["width"], height=mon["height"])  # 占位，遮罩半透明
    tip = cv.create_text(0, 0, text=f"拖拽框选『{name}』的范围，松开即保存；ESC 取消",
                         fill="yellow", font=("Microsoft YaHei", 20, "bold"))
    cv.coords(tip, mon["width"] // 2, 50)

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
        if x1 - x0 < 10 or y1 - y0 < 10:
            return
        crop = img[y0:y1, x0:x1]
        ok, buf = cv2.imencode(".png", crop)
        path = os.path.join(ROOT, "templates", name + ".png")
        buf.tofile(path)
        print("已保存模板:", path)
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
