# -*- coding: utf-8 -*-
"""冒烟测试：模拟主程序进程内调用 calibrate.main(parent=...)，验证不卡死、可返回。"""
import importlib.util
import os
import sys
import tempfile
import tkinter as tk

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)

spec = importlib.util.spec_from_file_location("shop_calibrate", os.path.join(HERE, "tools", "calibrate.py"))
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)

root = tk.Tk()
root.withdraw()

cp = os.path.join(tempfile.gettempdir(), "cal_test_config.json")
if os.path.exists(cp):
    os.remove(cp)


def closer():
    for w in root.winfo_children():
        try:
            w.event_generate("<Escape>")
        except Exception:
            pass


root.after(800, closer)
mod.main(config_path=cp, parent=root)

leftover = len(root.winfo_children())
print("main() returned OK; leftover children:", leftover)
print("event loop alive:", root.tk.call("info", "exists", "foo") == 0)
root.destroy()
assert leftover == 0, "Toplevel 未被销毁"
print("PASS")
