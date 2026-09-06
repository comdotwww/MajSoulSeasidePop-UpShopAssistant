# -*- coding: utf-8 -*-
"""拖拽诊断脚本（不影响游戏存档，顾客松开后自动回到顾客区）

流程：
  1. 识别顾客区第一位顾客
  2. 按住并拖出顾客区（触发『+』与桌下标签条）
  3. 连拍 3 张截图保存到 _test_shots/
  4. 输出空桌识别结果 + 每张桌下的标签颜色 + 顾客卡片周边颜色采样
  5. 原位松开（顾客回到顾客区）

用法： python test_drag.py [顾客序号]   （默认 0 = 最急的那位）
"""
import json
import os
import sys
import time

APP_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, APP_DIR)

import cv2
import numpy as np
import mss

import actions
import vision

OUT = os.path.join(APP_DIR, "_test_shots")
os.makedirs(OUT, exist_ok=True)

with open(os.path.join(APP_DIR, "config.json"), encoding="utf-8") as f:
    cfg = json.load(f)
region = cfg.get("region")


def fullscreen():
    with mss.mss() as s:
        mon = s.monitors[1]
    return {"left": mon["left"], "top": mon["top"], "width": mon["width"], "height": mon["height"]}


def cap():
    return vision.capture(region) if region else vision.capture(fullscreen())


def save(img, name):
    ok, buf = cv2.imencode(".png", img)
    buf.tofile(os.path.join(OUT, name))
    print("  已保存截图:", name)


def hsv_sample(img, cx, cy, r=90):
    """打印 (cx,cy) 周边区域的主要颜色簇，用于人工判断标签样式"""
    h, w = img.shape[:2]
    x0, x1 = max(0, cx - r), min(w, cx + r)
    y0, y1 = max(0, cy - r), min(h, cy + r)
    roi = img[y0:y1, x0:x1]
    hsv = cv2.cvtColor(roi, cv2.COLOR_BGR2HSV).reshape(-1, 3)
    sat = hsv[:, 1] > 90
    bright = hsv[:, 2] > 90
    m = hsv[sat & bright]
    if len(m) == 0:
        return "  (周边无高饱和颜色)"
    hist, _ = np.histogram(m[:, 0], bins=18, range=(0, 180))
    top = np.argsort(hist)[::-1][:4]
    names = []
    for b in top:
        if hist[b] < 30:
            continue
        hc = int((b + 0.5) * 10)
        if hc < 12 or hc >= 165:
            nm = "红"
        elif hc < 25:
            nm = "橙"
        elif hc < 35:
            nm = "黄"
        elif hc < 80:
            nm = "绿"
        elif hc < 100:
            nm = "青"
        elif hc < 130:
            nm = "蓝"
        else:
            nm = "紫/粉"
        names.append("%s(H~%d,%dpx)" % (nm, hc, hist[b]))
    return "  颜色簇: " + ", ".join(names) if names else "  (颜色簇太少)"


def glow_counts(img, tables):
    """输出每张桌子的绿光像素数（诊断用）"""
    hsv = cv2.cvtColor(img, cv2.COLOR_BGR2HSV)
    glow = cv2.inRange(hsv, (35, 120, 200), (75, 255, 255))
    out = []
    for cx, cy in tables:
        x0, x1 = max(0, cx - 220), min(glow.shape[1], cx + 220)
        y0, y1 = max(0, cy - 140), min(glow.shape[0], cy + 180)
        n = int(glow[y0:y1, x0:x1].sum() / 255)
        out.append(((cx, cy), n))
    return out


def main():
    idx = int(sys.argv[1]) if len(sys.argv) > 1 else 0
    print("=== 拖拽诊断开始（3 秒后动手）===")
    time.sleep(3)

    img = cap()
    save(img, "00_before.png")
    customers = vision.find_customers(img)
    print("识别到顾客:", customers)
    if len(customers) <= idx:
        print("!! 顾客数量不足，退出")
        return
    cx, cy = customers[idx]
    h = (region or fullscreen())["height"]

    # 按住并拖出顾客区
    actions.pickup(cx, cy, cx, cy - int(h * 0.28), duration=0.12)
    time.sleep(0.18)
    img1 = cap()
    save(img1, "01_held_0p18s.png")
    tables1 = vision.find_empty_tables(img1)
    print("拖出后 0.18s 空桌:", tables1)
    print("  绿光像素:", glow_counts(img1, tables1))
    print("  绿光匹配:", vision.find_matching_tables(img1, tables1))

    time.sleep(0.4)
    img2 = cap()
    save(img2, "02_held_0p6s.png")
    held = (cx, cy - int(h * 0.28))
    pref = vision.read_customer_pref(img2, *held)
    print("顾客偏好:", pref)
    tables = vision.find_empty_tables(img2)
    print("空桌:", tables)
    for i, (tx, ty) in enumerate(tables):
        tags = vision.read_table_tags(img2, tx, ty)
        mark = " ★标签匹配" if (pref and pref in tags) else ""
        print("  桌%d (%d,%d) 标签: %s%s" % (i, tx, ty, sorted(tags) if tags else "无", mark))

    time.sleep(0.6)
    img3 = cap()
    save(img3, "03_held_1p2s.png")
    tables3 = vision.find_empty_tables(img3)
    print("拖出后 1.2s 空桌:", tables3)
    print("  绿光像素:", glow_counts(img3, tables3))

    actions.drop_cancel()
    time.sleep(0.5)
    save(cap(), "04_after_cancel.png")
    print("=== 诊断结束，顾客已放回顾客区 ===")


if __name__ == "__main__":
    main()
