# -*- coding: utf-8 -*-
"""单次真实入座测试：执行一轮「拖出顾客 → 绿光匹配 → 入座」后立即停止"""
import json
import os
import sys
import time

APP_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, APP_DIR)

from main import Worker, mss_fullscreen


import vision

def log(msg):
    print("[助理]", msg)


with open(os.path.join(APP_DIR, "config.json"), encoding="utf-8") as f:
    cfg = json.load(f)

w = Worker(cfg, log)
time.sleep(3)  # 给用户 3 秒移开鼠标
print("=== 开始单次入座测试 ===")
region = cfg.get("region")
img = vision.capture(region) if region else vision.capture(mss_fullscreen())
n = w._assign_customers(img)
print("=== 测试完成，本轮入座 %d 位 ===" % n)
