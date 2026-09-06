# -*- coding: utf-8 -*-
"""鼠标操作模块：平滑点击与拖拽（适配游戏内拖动）

拖拽采用两阶段模式（配合游戏的标签条机制）：
  pickup()     按住顾客并拖出顾客区（此时游戏才显示空桌 + 与桌下标签条），保持按住
  drop_at()    继续移动到目标桌子并松开（入座）
  drop_cancel()松开鼠标放弃（顾客自动回到顾客区）

所有落点都带随机偏移（JITTER_PX），避免每次点击/放置位置完全一致。
"""
import math
import random
import time
import pyautogui

pyautogui.FAILSAFE = True   # 鼠标甩到左上角可紧急中止
pyautogui.PAUSE = 0         # 节奏由各动作内的 sleep/duration 精确控制，不用全局暂停

JITTER_PX = 8  # 目标点随机偏移半径（像素）


def _jitter(x, y, r=JITTER_PX):
    """在目标点附近随机偏移（圆形分布，中心密集）"""
    ang = random.uniform(0, 6.2832)
    dist = r * (random.random() ** 0.5)
    return x + dist * math.cos(ang), y + dist * math.sin(ang)


def click(x, y, duration=0.08):
    x, y = _jitter(x, y)
    pyautogui.moveTo(x, y, duration=duration)
    pyautogui.click()


def _move_smooth(x0, y0, x1, y1, duration, steps):
    step_x = (x1 - x0) / steps
    step_y = (y1 - y0) / steps
    for i in range(1, steps + 1):
        jx = step_x * i + (1 if i % 3 == 0 else 0)
        jy = step_y * i + (-1 if i % 4 == 0 else 0)
        pyautogui.moveTo(x0 + jx, y0 + jy, duration=duration / steps)


def pickup(x0, y0, x1, y1, duration=0.08, steps=5):
    """按住 (x0,y0) 并拖到 (x1,y1)，保持按住状态。起终点均带随机偏移"""
    x0, y0 = _jitter(x0, y0)
    x1, y1 = _jitter(x1, y1)
    pyautogui.moveTo(x0, y0, duration=0.03)
    time.sleep(0.02)
    pyautogui.mouseDown()
    time.sleep(0.02)
    _move_smooth(x0, y0, x1, y1, duration, steps)
    time.sleep(0.02)


def drop_at(x, y, duration=0.09, steps=6, settle=0.03):
    """保持按住状态下移动到 (x,y) 并松开（落点带随机偏移）"""
    cx, cy = pyautogui.position()
    x, y = _jitter(x, y)
    _move_smooth(cx, cy, x, y, duration, steps)
    time.sleep(settle)
    pyautogui.mouseUp()
    time.sleep(0.04)


def drop_cancel(settle=0.05):
    """原位松开鼠标，放弃本次拖拽（顾客自动回到顾客区）"""
    pyautogui.mouseUp()
    time.sleep(settle)


def drag(x0, y0, x1, y1, duration=0.25, steps=12, settle=0.05):
    """一步式拖拽（简单场景用）。起终点均带随机偏移"""
    x0, y0 = _jitter(x0, y0)
    x1, y1 = _jitter(x1, y1)
    pyautogui.moveTo(x0, y0, duration=0.05)
    time.sleep(0.03)
    pyautogui.mouseDown()
    time.sleep(0.03)
    _move_smooth(x0, y0, x1, y1, duration, steps)
    time.sleep(settle)
    pyautogui.mouseUp()
    time.sleep(0.08)
