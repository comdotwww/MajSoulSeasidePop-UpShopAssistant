# -*- coding: utf-8 -*-
"""自测：用参考截图验证各识别函数"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import cv2
import numpy as np
import vision

def test(path, label):
    img = cv2.imdecode(np.fromfile(path, dtype=np.uint8), cv2.IMREAD_COLOR)
    print("=" * 20, label, img.shape[1], "x", img.shape[0])
    tables = vision.find_empty_tables(img)
    print(f"空桌 {len(tables)}:", [(x, y) for x, y in tables])
    customers = vision.find_customers(img)
    print(f"顾客 {len(customers)}:", customers)
    btn = vision.find_collect_button(img)
    print("一键收集按钮:", btn)
    doors = vision.find_room_doors(img)
    print("房间门:", doors)
    print("当前房间:", vision.detect_current_room(img))
    coins = vision.find_coins(img)
    print(f"金币堆 {len(coins)}:", coins)
    # 打印第一张空桌的标签颜色
    if tables:
        t = sorted(tables)[0]
        tags = vision.read_tags_near(img, t[0], t[1])
        print("第一张空桌标签:", tags)

base = r"C:\Users\xmj\.workbuddy\clipboard-images"
test(base + r"\clipboard-2026-09-05T18-17-54-600Z-dfb398ce.jpg", "铜之间(有+空桌)")
test(base + r"\clipboard-2026-09-05T18-17-54-598Z-d3b17e2d.jpg", "铜之间(桌子未开放)")
test(r"D:\WorkBuddyData\游戏助理\1.png", "银之间")
test(r"D:\WorkBuddyData\游戏助理\2.png", "金之间")
test(r"D:\WorkBuddyData\游戏助理\3.png", "铜之间(红地毯)")
