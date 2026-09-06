# -*- coding: utf-8 -*-
"""从历史截图提取房间数字字形，生成 digit_<数字>_<序号>.png 模板（24x32 白字掩码）"""
import cv2
import numpy as np
import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # 仓库根目录
TEMPLATES_DIR = os.path.join(ROOT, "templates")
GLYPH_W, GLYPH_H = 24, 32

# 每张截图三个门的数字文本（铜/银/金）
LABELS = {
    "1.png": ["0/20", "5/16", "0/12"],
    "2.png": ["0/20", "8/16", "0/12"],
    "3.png": ["0/20", "0/16", "0/12"],
}

BAND_Y = (0.252, 0.29)   # 数字带高度范围（占画面高比例）
DOOR_RX = (0.048, 0.112, 0.182)


def band_mask(img, cx):
    h, w = img.shape[:2]
    hsv = cv2.cvtColor(img, cv2.COLOR_BGR2HSV)
    white = ((hsv[:, :, 2] >= 200) & (hsv[:, :, 1] <= 70)).astype(np.uint8) * 255
    y0, y1 = int(h * BAND_Y[0]), int(h * BAND_Y[1])
    m = np.zeros_like(white)
    m[y0:y1, max(0, cx - 70):cx + 70] = white[y0:y1, max(0, cx - 70):cx + 70]
    return m


def segment(mask):
    n, labels, stats, _ = cv2.connectedComponentsWithStats(mask, 8)
    boxes = []
    for i in range(1, n):
        x, y, bw, bh, area = stats[i]
        if bh >= 20 and area >= 60 and bw <= 40:  # 数字高~24、宽~17；滤掉横向噪声条
            boxes.append((x, y, bw, bh))
    boxes.sort()
    return boxes


def norm_glyph(img_mask, box):
    x, y, bw, bh = box
    g = img_mask[y:y + bh, x:x + bw]
    return cv2.resize(g, (GLYPH_W, GLYPH_H), interpolation=cv2.INTER_AREA)


def main():
    saved = {}
    for f, texts in LABELS.items():
        # 样本截图在仓库上级目录（游戏助理/1.png 等）
        path = os.path.normpath(os.path.join(ROOT, "..", f))
        img = cv2.imdecode(np.fromfile(path, dtype=np.uint8), cv2.IMREAD_COLOR)
        h, w = img.shape[:2]
        for rx, text in zip(DOOR_RX, texts):
            cx = int(w * rx)
            mask = band_mask(img, cx)
            boxes = segment(mask)
            chars = [c for c in text if c != "/"]
            if len(boxes) != len(text):
                print(f"! {f} @{rx:.3f} 期望 {len(text)} 字符, 分割出 {len(boxes)}: {boxes}")
                continue
            # 文本中 '/' 位置：宽度窄的通常是 '/'，直接按文本序对齐（'/'两侧字符对应）
            # 更稳妥：用 '/' 在文本中的索引跳过
            slash_idx = text.index("/")
            pairs = []
            for i, box in enumerate(boxes):
                label = text[i] if i < len(text) else "?"
                pairs.append((label, box))
            for label, box in pairs:
                key = "slash" if label == "/" else label
                g = norm_glyph(mask, box)
                idx = saved.get(key, 0)
                out = os.path.join(TEMPLATES_DIR, "digit_%s_%d.png" % (key, idx))
                cv2.imencode(".png", g)[1].tofile(out)  # 中文路径须用 tofile
                saved[key] = idx + 1
        print(f, "done")
    print("模板统计:", saved)


if __name__ == "__main__":
    main()
