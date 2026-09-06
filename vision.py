# -*- coding: utf-8 -*-
"""视觉识别模块：屏幕捕获 + 识别空桌 / 顾客 / 金币 / 按钮"""
import os
import sys
import threading

import cv2
import numpy as np
import mss

# PyInstaller 打包后资源在 sys._MEIPASS 临时解包目录；源码运行时在脚本目录
if getattr(sys, "frozen", False):
    BASE_DIR = getattr(sys, "_MEIPASS", os.path.dirname(sys.executable))
else:
    BASE_DIR = os.path.dirname(os.path.abspath(__file__))
TEMPLATES_DIR = os.path.join(BASE_DIR, "templates")

# ---------------- 风格标签 HSV 颜色表（用于识别桌台下方/顾客的偏好标签） ----------------
# 名称: (H低, H高, S低, V低)
TAG_COLORS = {
    "green":    (35, 85, 80, 80),    # 绿色（植物）
    "skyblue":  (85, 105, 60, 120),  # 天蓝（纸飞机）
    "red":      (0, 10, 90, 150),    # 红（火焰，要求较亮避免误判棕红桌沿）
    "red2":     (170, 180, 90, 150), # 红（火焰，高H区间）
    "pink":     (140, 170, 60, 120), # 粉（蝴蝶）
    "navy":     (105, 130, 120, 60), # 深蓝
}

TEMPLATE_CACHE = {}


def load_template(name):
    """加载模板（带缓存），返回 BGR 图像或 None"""
    if name in TEMPLATE_CACHE:
        return TEMPLATE_CACHE[name]
    path = os.path.join(TEMPLATES_DIR, name + ".png")
    if not os.path.exists(path):
        TEMPLATE_CACHE[name] = None
        return None
    img = cv2.imdecode(np.fromfile(path, dtype=np.uint8), cv2.IMREAD_COLOR)
    TEMPLATE_CACHE[name] = img
    return img


_MSS = None
_MSS_LOCK = threading.Lock()
_MSS_TID = None  # _MSS 实例所属线程（mss 的 GDI 资源绑定创建线程，跨线程复用会 BitBlt 失败）


def capture(region):
    """截取屏幕区域 region={left,top,width,height}，返回 BGR numpy 数组。
    复用模块级 mss 实例省去每次新建的开销（首拍 ~190ms → 稳态 ~40ms）；
    但实例必须按线程重建：停止后再次开始会开新 Worker 线程，检测到线程变化即重建，
    grab 失败（BitBlt）时也重建重试一次。"""
    global _MSS, _MSS_TID
    with _MSS_LOCK:
        tid = threading.get_ident()
        if _MSS is None or _MSS_TID != tid:
            _MSS = mss.mss()
            _MSS_TID = tid
        try:
            shot = _MSS.grab(region)
        except Exception:
            _MSS = mss.mss()  # GDI 资源失效（如显示器拓扑变化），重建后重试
            shot = _MSS.grab(region)
        img = np.array(shot, dtype=np.uint8)  # BGRA
    return cv2.cvtColor(img, cv2.COLOR_BGRA2BGR)


def _match_multi_scale(img, tmpl, scales=(0.55, 0.7, 0.85, 1.0, 1.15, 1.3, 1.45, 1.6, 1.8), threshold=0.80):
    """多尺度模板匹配，返回 [(cx, cy, score, scale), ...] 已按距离去重、得分降序"""
    results = []
    th, tw = tmpl.shape[:2]
    for s in scales:
        nw, nh = int(tw * s), int(th * s)
        if nw < 8 or nh < 8 or nw >= img.shape[1] or nh >= img.shape[0]:
            continue
        t = cv2.resize(tmpl, (nw, nh), interpolation=cv2.INTER_AREA)
        res = cv2.matchTemplate(img, t, cv2.TM_CCOEFF_NORMED)
        ys, xs = np.where(res >= threshold)
        for x, y in zip(xs, ys):
            results.append((x + nw // 2, y + nh // 2, float(res[y, x]), s))
    # 按得分降序做简单的距离去重
    results.sort(key=lambda r: -r[2])
    kept = []
    for cx, cy, sc, s in results:
        if all((cx - kx) ** 2 + (cy - ky) ** 2 > 30 ** 2 for kx, ky, _, _ in kept):
            kept.append((cx, cy, sc, s))
    return kept


def _match_at_scale(roi, tmpl, scale, threshold=0.80):
    """单尺度模板匹配，返回 [(cx, cy, score, scale), ...]（中心坐标，未去重）"""
    tw, th = int(tmpl.shape[1] * scale), int(tmpl.shape[0] * scale)
    if tw < 8 or th < 8 or tw >= roi.shape[1] or th >= roi.shape[0]:
        return []
    t = cv2.resize(tmpl, (tw, th), interpolation=cv2.INTER_AREA)
    res = cv2.matchTemplate(roi, t, cv2.TM_CCOEFF_NORMED)
    ys, xs = np.where(res >= threshold)
    return [(int(x) + tw // 2, int(y) + th // 2, float(res[y, x]), scale) for x, y in zip(xs, ys)]


_PREFIX_CACHE = {}


def _load_all_prefix(prefix):
    """加载 templates 目录下所有 prefix*.png 模板（如 table_plus*.png），带列表缓存"""
    if prefix in _PREFIX_CACHE:
        return _PREFIX_CACHE[prefix]
    out = []
    try:
        for f in sorted(os.listdir(TEMPLATES_DIR)):
            if f.startswith(prefix) and f.endswith(".png"):
                t = load_template(f[:-4])
                if t is not None:
                    out.append(t)
    except OSError:
        pass
    _PREFIX_CACHE[prefix] = out
    return out


# 各模板上次命中的匹配尺度缓存（会话内分辨率不变，命中后跳过全尺度扫描）
_SCALE_CACHE = {}


def _load_plus_templates():
    """空桌『+』模板列表 [(name, tmpl), ...]，name 如 'table_plus'/'table_plus_gold'"""
    if "table_plus" in _PREFIX_CACHE:
        return _PREFIX_CACHE["table_plus"]
    out = []
    try:
        for f in sorted(os.listdir(TEMPLATES_DIR)):
            if f.startswith("table_plus") and f.endswith(".png"):
                t = load_template(f[:-4])
                if t is not None:
                    out.append((f[:-4], t))
    except OSError:
        pass
    _PREFIX_CACHE["table_plus"] = out
    return out


# 各模板上次命中的匹配尺度缓存（会话内分辨率不变，命中后跳过全尺度扫描）
_SCALE_CACHE = {}
# 各房间命中过的『+』模板样式（每个房间『+』样式固定：金房间粉、铜/银白色）。
# 房间内某样式命中过一次后，本会话该房间跳过其余样式，省掉全尺度扫描。
_ROOM_PLUS_HIT = {}
# 各房间桌位缓存 {room: {'name','scale','pts'}}（房间内桌子位置固定）：
# 扫描时只对缓存桌位做局部验证（~10ms/桌），每 5 次全图刷新一次防漏检。
_TABLE_CACHE = {}
_TABLE_SCAN_N = {}


def find_empty_tables(img, fast=True, room=None):
    """识别空桌（'+' 标记）。room 传当前房间键可启用桌位缓存 + 模板样式筛选。
    返回 [(cx, cy), ...]
    fast 模式提速链：① 房间桌位缓存 -> 只做局部验证；
    ② 需要全图扫描时，优先用上次命中的尺度 + 半分辨率粗筛再局部确认。"""
    h, w = img.shape[:2]
    y0, y1 = int(h * 0.10), int(h * 0.75)
    x0, x1 = int(w * 0.03), int(w * 0.97)
    roi = img[y0:y1, x0:x1]

    tmap = dict(_load_plus_templates())
    tlist = list(tmap.items())
    hit_name = _ROOM_PLUS_HIT.get(room) if room else None
    if hit_name:  # 本房间已确认样式 -> 只用该模板
        tlist = [(n, t) for n, t in tlist if n == hit_name]
    elif room == "gold":  # 按房间优先级排序（金房间优先粉『+』）
        tlist = sorted(tlist, key=lambda nt: 0 if nt[0].endswith("gold") else 1)

    # ---- 快路径：桌位缓存局部验证（满员桌验证自然失败即被剔除） ----
    cache = _TABLE_CACHE.get(room) if (fast and room) else None
    verified = []
    refresh = False
    if cache and cache["name"] in tmap:
        tmpl = tmap[cache["name"]]
        scale = cache["scale"]
        t2 = cv2.resize(tmpl, (max(8, int(tmpl.shape[1] * scale)),
                               max(8, int(tmpl.shape[0] * scale))),
                        interpolation=cv2.INTER_AREA)
        off = int(tmpl.shape[0] * 0.2)
        n = _TABLE_SCAN_N.get(room, 0) + 1
        _TABLE_SCAN_N[room] = n
        refresh = (n % 5 == 0)  # 每 5 次做一次全图刷新（防拖拽卡片遮挡导致漏检）
        for cx, cy in cache["pts"]:
            bx, by = cx - x0, cy - y0 - off  # roi 内模板命中中心
            hw, hh = t2.shape[1] // 2, t2.shape[0] // 2
            wx0 = max(0, bx - hw - 24)
            wy0 = max(0, by - hh - 24)
            wx1 = min(roi.shape[1], bx + hw + 24)
            wy1 = min(roi.shape[0], by + hh + 24)
            if wx1 - wx0 < t2.shape[1] or wy1 - wy0 < t2.shape[0]:
                continue
            res2 = cv2.matchTemplate(roi[wy0:wy1, wx0:wx1], t2, cv2.TM_CCOEFF_NORMED)
            _, mx, _, mxl = cv2.minMaxLoc(res2)
            if mx >= 0.72:
                verified.append((wx0 + mxl[0] + hw + x0,
                                 wy0 + mxl[1] + hh + y0 + off))
        if not refresh:
            return verified  # 非刷新轮：缓存验证结果即为答案（空列表=都满员）

    # ---- 全图扫描路径 ----
    small = cv2.resize(roi, (max(1, roi.shape[1] // 2), max(1, roi.shape[0] // 2)),
                       interpolation=cv2.INTER_AREA)
    pts = []
    seen = []
    best = None  # (命中数, name, scale, pts)
    for name, tmpl in tlist:
        hits = []
        cached = _SCALE_CACHE.get(name)
        if fast and cached is not None:
            # 半分辨率粗筛 -> 原分辨率局部确认
            coarse = _match_at_scale(small, tmpl, cached, 0.70)
            t2 = cv2.resize(tmpl, (max(8, int(tmpl.shape[1] * cached)),
                                   max(8, int(tmpl.shape[0] * cached))),
                            interpolation=cv2.INTER_AREA)
            for ccx, ccy, _, _ in coarse:
                fx, fy = ccx * 2, ccy * 2
                wx0 = max(0, fx - t2.shape[1] // 2 - 16)
                wy0 = max(0, fy - t2.shape[0] // 2 - 16)
                wx1 = min(roi.shape[1], fx + t2.shape[1] // 2 + 16)
                wy1 = min(roi.shape[0], fy + t2.shape[0] // 2 + 16)
                if wx1 - wx0 < t2.shape[1] or wy1 - wy0 < t2.shape[0]:
                    continue
                res2 = cv2.matchTemplate(roi[wy0:wy1, wx0:wx1], t2, cv2.TM_CCOEFF_NORMED)
                _, mx, _, mxl = cv2.minMaxLoc(res2)
                if mx >= 0.72:
                    hits.append((wx0 + mxl[0] + t2.shape[1] // 2,
                                 wy0 + mxl[1] + t2.shape[0] // 2, float(mx), cached))
            if not hits:  # 半分辨率没抓到（模板可能太小）-> 回退原分辨率单尺度
                hits = _match_at_scale(roi, tmpl, cached, 0.72)
            # 同尺度多命中去重
            hits.sort(key=lambda r: -r[2])
            dedup = []
            for x, y, sc, s in hits:
                if all((x - kx) ** 2 + (y - ky) ** 2 > 40 ** 2 for kx, ky, _, _ in dedup):
                    dedup.append((x, y, sc, s))
            hits = dedup
        if not hits:  # 缓存未命中或未缓存 -> 全尺度扫描
            hits = _match_multi_scale(roi, tmpl, threshold=0.72)
        if hits:
            _SCALE_CACHE[name] = hits[0][3]  # 记录最高分命中的尺度
            if room:
                _ROOM_PLUS_HIT[room] = name  # 本房间确认使用该『+』样式
        tpts = []
        for x, y, sc, s in hits:
            if all((x - kx) ** 2 + (y - ky) ** 2 > 40 ** 2 for kx, ky in seen):
                seen.append((x, y))
                tpts.append((x + x0, y + y0 + int(tmpl.shape[0] * 0.2)))
        pts.extend(tpts)
        if hits and (best is None or len(hits) > best[0]):
            best = (len(hits), name, hits[0][3], tpts)
    if room and best:
        # 刷新轮：全图结果与缓存验证结果合并（验证过的桌位最可靠）
        merged = list(verified)
        for p in best[3]:
            if all((p[0] - q[0]) ** 2 + (p[1] - q[1]) ** 2 > 40 ** 2 for q in merged):
                merged.append(p)
        _TABLE_CACHE[room] = {"name": best[1], "scale": best[2], "pts": merged}
        _TABLE_SCAN_N[room] = 0
        pts = merged
    return pts


def _color_present(hsv_roi, spec, min_pixels=12):
    h0, h1, s0, v0 = spec
    h, s, v = hsv_roi[:, :, 0], hsv_roi[:, :, 1], hsv_roi[:, :, 2]
    if h0 <= h1:
        mask = (h >= h0) & (h <= h1) & (s >= s0) & (v >= v0)
    else:  # 跨越 0/180
        mask = ((h >= h0) | (h <= h1)) & (s >= s0) & (v >= v0)
    return int(mask.sum()) >= min_pixels


def read_tags_near(img, cx, cy, half_w=62, y0off=50, y1off=120, min_pixels=12):
    """读取 (cx,cy) 下方 [y0off, y1off] 像素条带内的标签颜色集合。
    用于空桌下方的风格标签条（以 '+' 中心为基准）。"""
    h, w = img.shape[:2]
    x0 = max(0, cx - half_w); x1 = min(w, cx + half_w)
    y0 = max(0, cy + y0off); y1 = min(h, cy + y1off)
    if x1 <= x0 or y1 <= y0:
        return set()
    roi = img[y0:y1, x0:x1]
    hsv = cv2.cvtColor(roi, cv2.COLOR_BGR2HSV)
    found = set()
    for name, spec in TAG_COLORS.items():
        base = "red" if name == "red2" else name
        if _color_present(hsv, spec, min_pixels):
            found.add(base)
    return found


# 标签图标色相范围（只在标签条/偏好胶囊内部采样，无地毯背景干扰）
# 实测图标主色: 火焰=红橙(H0-28), 植物=绿(H32-88), 纸飞机=天蓝(H88-108),
#               蓝卷轴=深蓝(H108-140), 蝴蝶=粉(H140-168)
TAG_HUES = {
    "red":     [(0, 28), (168, 180)],
    "green":   [(32, 88)],
    "skyblue": [(88, 108)],
    "navy":    [(108, 140)],
    "pink":    [(140, 168)],
}


def _tags_from_region(img, cx, cy, half_w, half_h, red_cap=4500):
    """统计 (cx,cy)±(half_w,half_h) 内各标签色相像素，返回 {标签名: 像素数}。
    双饱和度档规则（S>=80 宽档 / S>=110 严档），按各房间实测标定：
      red:    1200<=cnt80<=4500（无火焰桌沿红棕~800、纯地毯>4500）
      green:  cnt80>=500
      navy:   cnt80>=900 或 cnt110>=400（银房间灰蓝凳子 FP 仅 415/36）
      skyblue: cnt80>=1900 或 cnt110>=600（银房间凳子 FP 仅 1650/0；金房间粉彩纸飞机 1995）
      pink:   (cnt80>=1500 或 cnt110>=400) 且非地毯（red cnt80>red_cap 时整窗是地毯，pink 一并压制）"""
    h, w = img.shape[:2]
    x0, x1 = max(0, cx - half_w), min(w, cx + half_w)
    y0, y1 = max(0, cy - half_h), min(h, cy + half_h)
    if x1 <= x0 or y1 <= y0:
        return {}
    hsv = cv2.cvtColor(img[y0:y1, x0:x1], cv2.COLOR_BGR2HSV)
    hh, ss, vv = hsv[:, :, 0], hsv[:, :, 1], hsv[:, :, 2]

    def count(ranges, smin):
        cnt = 0
        for a, b in ranges:
            m = (hh >= a) & (hh <= b) if a <= b else (hh >= a) | (hh <= b)
            cnt += int((m & (ss >= smin) & (vv >= 130)).sum())
        return cnt

    c80 = {name: count(ranges, 80) for name, ranges in TAG_HUES.items()}
    c110 = {name: count(ranges, 110) for name, ranges in TAG_HUES.items()}
    out = {}
    if 1200 <= c80["red"] <= red_cap:
        out["red"] = c80["red"]
    if c80["green"] >= 500:
        out["green"] = c80["green"]
    if c80["navy"] >= 900 or c110["navy"] >= 400:
        out["navy"] = c80["navy"]
    if c80["skyblue"] >= 1900 or c110["skyblue"] >= 600:
        out["skyblue"] = c80["skyblue"]
    carpet = c80["red"] > red_cap
    if not carpet and (c80["pink"] >= 1500 or c110["pink"] >= 400):
        out["pink"] = c80["pink"]
    return out


def read_table_tags(img, cx, cy):
    """读取空桌下方标签条内的风格标签集合（拖拽时才显示，每局固定）。
    (cx,cy) 为 find_empty_tables 返回的桌点，标签条中心在其下方约 113px。
    窗口 ±95x32 基本落在白色条内部（实测 bar 210x72），地毯漏入极少。"""
    return set(_tags_from_region(img, cx, cy + 113, 95, 32).keys())


def read_customer_pref(img, cx, cy):
    """读取被按住顾客头顶的偏好图标（cx,cy = 按住顾客的拖拽点）。
    偏好胶囊中心在拖拽点上方约 180px（水平偏移 ±15 内波动）。
    窗口 ±32x32 只取图标中心（排除胶囊的浅蓝圆环），避免天蓝/深蓝混淆。
    胶囊图标是粉彩画风（S~50-70），用 smin=45；取像素最多的色相。"""
    h, w = img.shape[:2]
    px, py = cx + 8, cy - 180
    x0, x1 = max(0, px - 32), min(w, px + 32)
    y0, y1 = max(0, py - 32), min(h, py + 32)
    if x1 <= x0 or y1 <= y0:
        return None
    hsv = cv2.cvtColor(img[y0:y1, x0:x1], cv2.COLOR_BGR2HSV)
    hh, ss, vv = hsv[:, :, 0], hsv[:, :, 1], hsv[:, :, 2]
    sat = (ss >= 45) & (vv >= 130)
    best, best_n = None, 150
    for name, ranges in TAG_HUES.items():
        cnt = 0
        for a, b in ranges:
            m = (hh >= a) & (hh <= b) if a <= b else (hh >= a) | (hh <= b)
            cnt += int((m & sat).sum())
        if cnt > best_n:
            best, best_n = name, cnt
    return best


def find_glow_tables(img, min_glow_px=600):
    """拖拽状态下，从绿色荧光描边 blob 直接定位匹配桌（发光桌的『+』会被绿光
    干扰而漏检，用 blob 质心补充）。返回 [(cx, cy), ...] 质心即桌心。"""
    h, w = img.shape[:2]
    hsv = cv2.cvtColor(img, cv2.COLOR_BGR2HSV)
    glow = cv2.inRange(hsv, (35, 120, 200), (75, 255, 255))
    glow[: int(h * 0.10), :] = 0
    glow[int(h * 0.78):, :] = 0
    n, labels, stats, cen = cv2.connectedComponentsWithStats(glow, 8)
    out = []
    for i in range(1, n):
        if stats[i][4] >= min_glow_px:
            out.append((int(cen[i][0]), int(cen[i][1])))
    return out


def find_matching_tables(img, tables, min_glow_px=800):
    """拖拽状态下，找出被绿色荧光描边高亮的桌子（游戏对『顾客偏好匹配』的官方提示）。
    实测：匹配桌周边荧光绿约 9000+ 像素，未匹配桌 <300 像素。
    返回 [(cx, cy), ...]（输入 tables 的子集，保持原顺序）"""
    hsv = cv2.cvtColor(img, cv2.COLOR_BGR2HSV)
    glow = cv2.inRange(hsv, (35, 120, 200), (75, 255, 255))  # 荧光绿 H35-75
    out = []
    for cx, cy in tables:
        x0, x1 = max(0, cx - 220), min(glow.shape[1], cx + 220)
        y0, y1 = max(0, cy - 140), min(glow.shape[0], cy + 180)
        if int(glow[y0:y1, x0:x1].sum() / 255) >= min_glow_px:
            out.append((cx, cy))
    return out


def find_customers(img):
    """在画面底部顾客区识别待客顾客。
    顾客区是固定 UI：8 个卡片槽均宽分布（实测 2555x1494 下首槽 x=364、间距 241、
    耐心条 y=1442、拖拽点 y=1405）。按比例换算槽位，槽内耐心条有彩色填充即有人
    （填充绿→黄/橙→红随耐心变化，红温卡片也带红色残段，全状态稳健）。
    返回 [(cx, cy), ...] 拖拽起点（卡片中心），按剩余耐心升序（最急的先安排）。"""
    h, w = img.shape[:2]
    y_top = int(h * 0.90)
    hsv = cv2.cvtColor(img[y_top:], cv2.COLOR_BGR2HSV)  # 只转顾客区条带
    bar_y = int(h * 0.9654) - y_top  # 耐心条中心（条带内坐标）
    grab_y = int(h * 0.9405)         # 卡片中心（拖拽起点，全帧坐标）
    out = []
    for k in range(8):
        cx = int(w * (0.1425 + 0.0945 * k))
        # 只采样耐心条左段（填充从左侧开始），75x14 窗口
        inner = hsv[bar_y - 7:bar_y + 7, max(0, cx - 85):max(0, cx - 10)]
        hue_ok = (inner[:, :, 0] <= 60) | (inner[:, :, 0] >= 168)
        fill = int((hue_ok & (inner[:, :, 1] >= 95) & (inner[:, :, 2] >= 150)).sum())
        if fill >= 50:
            out.append((cx, grab_y, fill))
    out.sort(key=lambda c: c[2])  # 填充越少越急，优先安排
    return [(cx, cy) for cx, cy, _ in out]


def find_coins(img):
    """识别场内金币堆（金黄色块），限制在中央游戏区域内避免误识别 UI 元素。
    返回 [(cx, cy), ...]"""
    h, w = img.shape[:2]
    hsv = cv2.cvtColor(img, cv2.COLOR_BGR2HSV)
    mask = cv2.inRange(hsv, (18, 160, 170), (35, 255, 255))
    # 排除顶部 UI 条、右下顾客区、左右边缘、左上角店员徽章区
    mask[: int(h * 0.16), :] = 0
    mask[int(h * 0.75):, :] = 0
    mask[:, : int(w * 0.08)] = 0
    mask[:, int(w * 0.82):] = 0
    mask[: int(h * 0.24), : int(w * 0.14)] = 0
    mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, np.ones((4, 4), np.uint8))
    n, labels, stats, cen = cv2.connectedComponentsWithStats(mask, 8)
    coins = []
    for i in range(1, n):
        x, y, bw, bh, area = stats[i]
        if area < 300 or bw > w * 0.2 or bh > h * 0.2:
            continue
        if not (0.4 < bw / max(1, bh) < 2.5):  # 金币堆接近方形/横向椭圆
            continue
        cx, cy = int(cen[i][0]), int(cen[i][1])
        if any((cx - px) ** 2 + (cy - py) ** 2 < 40 ** 2 for px, py in coins):
            continue
        coins.append((cx, cy))
    return coins


def find_collect_button(img):
    """右下角『收集场内所有金币』按钮（多套模板取最优）。返回 (cx, cy) 或 None"""
    h, w = img.shape[:2]
    roi = img[int(h * 0.75):, int(w * 0.80):]
    best = None
    for tmpl in _load_all_prefix("collect_btn"):
        pts = _match_multi_scale(roi, tmpl, scales=(0.7, 0.85, 1.0, 1.15, 1.3, 1.45), threshold=0.70)
        if pts and (best is None or pts[0][2] > best[2]):
            best = pts[0]
    if best is None:
        return None
    return (best[0] + int(w * 0.80), best[1] + int(h * 0.75))


_END_SCALE_KEY = "end_business"

# ------- 游戏语言（主程序界面选择，决定加载哪套按钮模板） -------
GAME_LANG = "cn"  # "cn"=简体中文 | "jp"=日语
_END_TMPLS = {"cn": ("end_business",), "jp": ("end_business_jp",)}
_START_TMPLS = {"cn": ("start_biz_home", "start_biz_prep"),
                "jp": ("start_biz_home_jp", "start_biz_prep_jp")}


def find_end_business(img):
    """检测『结束营业』按钮（游戏结束结账界面，出现即本局已结束）。
    按钮位于底部中央；只在底部中央 ROI 半分辨率下匹配（真命中实测得分 0.97，
    阈值 0.85 余量充足），命中后回原分辨率局部窗口确认。单次 ~10ms。
    按所选游戏语言加载对应模板（中文/日语各一套）。
    命中返回 (cx, cy)，未命中返回 None。"""
    h, w = img.shape[:2]
    x0, x1 = int(w * 0.25), int(w * 0.75)
    y0, y1 = int(h * 0.70), h
    roi = img[y0:y1, x0:x1]
    small = cv2.resize(roi, (max(1, roi.shape[1] // 2), max(1, roi.shape[0] // 2)),
                       interpolation=cv2.INTER_AREA)

    # 模板按 2555 宽实机分辨率预放大；按当前画面宽度直接算出精确尺度（±5% 邻域兜底），
    # 不做多尺度盲扫。1px 级分辨率变化也能覆盖。
    rel = w / 2555.0
    scales = tuple(sorted({round(rel, 3), round(rel * 0.95, 3), round(rel * 1.05, 3)}))
    cached = _SCALE_CACHE.get(_END_SCALE_KEY)
    if cached is not None:
        scales = (cached,) + tuple(s for s in scales if s != cached)

    for tname in _END_TMPLS.get(GAME_LANG, _END_TMPLS["cn"]):
        tmpl = load_template(tname)
        if tmpl is None:
            continue
        for s in scales:
            coarse = _match_at_scale(small, tmpl, s * 0.5, 0.85)
            if not coarse:
                continue
            # 半分辨率命中 -> 原分辨率局部窗口确认（防误报）
            t2 = cv2.resize(tmpl, (int(tmpl.shape[1] * s), int(tmpl.shape[0] * s)),
                            interpolation=cv2.INTER_AREA)
            for ccx, ccy, _, _ in coarse[:2]:
                fx, fy = ccx * 2, ccy * 2
                wx0 = max(0, fx - t2.shape[1] // 2 - 24)
                wy0 = max(0, fy - t2.shape[0] // 2 - 24)
                wx1 = min(roi.shape[1], fx + t2.shape[1] // 2 + 24)
                wy1 = min(roi.shape[0], fy + t2.shape[0] // 2 + 24)
                if wx1 - wx0 < t2.shape[1] or wy1 - wy0 < t2.shape[0]:
                    continue
                res2 = cv2.matchTemplate(roi[wy0:wy1, wx0:wx1], t2, cv2.TM_CCOEFF_NORMED)
                _, mx, _, mxl = cv2.minMaxLoc(res2)
                if mx >= 0.80:
                    _SCALE_CACHE[_END_SCALE_KEY] = s
                    return (wx0 + mxl[0] + t2.shape[1] // 2 + x0,
                            wy0 + mxl[1] + t2.shape[0] // 2 + y0)
    return None


def find_start_business(img):
    """右下角「开始营业」按钮（游戏首页 / 开业准备页，两个页面样式几乎相同，
    两套模板取最优）。尺度自适应（rel=图宽/2555 ±5%），半分辨率粗筛(0.85)
    → 原分辨率局部确认(0.80)。命中返回 (cx, cy, 模板名)，未命中 None。"""
    h, w = img.shape[:2]
    x0, x1 = int(w * 0.60), w
    y0, y1 = int(h * 0.75), h
    roi = img[y0:y1, x0:x1]
    small = cv2.resize(roi, (max(1, roi.shape[1] // 2), max(1, roi.shape[0] // 2)),
                       interpolation=cv2.INTER_AREA)
    rel = w / 2555.0
    scales = tuple(sorted({round(rel, 3), round(rel * 0.95, 3), round(rel * 1.05, 3)}))

    best = None  # (cx, cy, score, name)
    for name in _START_TMPLS.get(GAME_LANG, _START_TMPLS["cn"]):
        tmpl = load_template(name)
        if tmpl is None:
            continue
        for s in scales:
            coarse = _match_at_scale(small, tmpl, s * 0.5, 0.85)
            if not coarse:
                continue
            t2 = cv2.resize(tmpl, (int(tmpl.shape[1] * s), int(tmpl.shape[0] * s)),
                            interpolation=cv2.INTER_AREA)
            for ccx, ccy, _, _ in coarse[:2]:
                fx, fy = ccx * 2, ccy * 2
                wx0 = max(0, fx - t2.shape[1] // 2 - 24)
                wy0 = max(0, fy - t2.shape[0] // 2 - 24)
                wx1 = min(roi.shape[1], fx + t2.shape[1] // 2 + 24)
                wy1 = min(roi.shape[0], fy + t2.shape[0] // 2 + 24)
                if wx1 - wx0 < t2.shape[1] or wy1 - wy0 < t2.shape[0]:
                    continue
                res2 = cv2.matchTemplate(roi[wy0:wy1, wx0:wx1], t2, cv2.TM_CCOEFF_NORMED)
                _, mx, _, mxl = cv2.minMaxLoc(res2)
                if mx >= 0.80 and (best is None or mx > best[2]):
                    best = (wx0 + mxl[0] + t2.shape[1] // 2 + x0,
                            wy0 + mxl[1] + t2.shape[0] // 2 + y0, mx, name)
    if best is None:
        return None
    return (best[0], best[1], best[3])


def find_event_popup(img):
    """游戏中的事件弹窗（全息弹窗/美味时间等，标题文案各不相同）：弹窗中下部有
    2~3 个蓝色圆角选项按钮（竖排、居中）。用与文案无关的颜色几何特征识别——
    中下部 ROI（x 20%~80%、y 50%~95%）内 HSV 蓝色组件，按尺寸过滤：
    宽 0.15~0.55w、高 0.04~0.12h、填充率 >0.5。检出 ≥2 个即认定弹窗
    （单按钮场景如「结束营业」「开始营业」按钮被此规则排除）。
    命中返回选项按钮中心列表（按 y 升序），未命中返回 None。"""
    h, w = img.shape[:2]
    x0, x1 = int(w * 0.20), int(w * 0.80)
    y0, y1 = int(h * 0.50), int(h * 0.95)
    hsv = cv2.cvtColor(img[y0:y1, x0:x1], cv2.COLOR_BGR2HSV)
    mask = cv2.inRange(hsv, (95, 80, 150), (112, 220, 245))
    n, labels, stats, _ = cv2.connectedComponentsWithStats(mask, 8)
    btns = []
    for i in range(1, n):
        x, y, bw, bh, area = stats[i]
        if (w*0.15 < bw < w*0.55 and h*0.04 < bh < h*0.12
                and area > bw * bh * 0.5):
            btns.append((int(x + bw/2 + x0), int(y + bh/2 + y0)))
    if len(btns) < 2:
        return None
    btns.sort(key=lambda b: b[1])
    return btns


def detect_current_room(img):
    """通过右上角房间牌（铜之间/银之间/金之间）判断当前房间。
    返回 (room_key|None, score)。"""
    h, w = img.shape[:2]
    roi = img[int(h * 0.08): int(h * 0.35), int(w * 0.55):]
    best_key, best_score = None, 0.0
    for key in ("bronze", "silver", "gold"):
        for tmpl in _load_all_prefix("room_sign_" + key):
            pts = _match_multi_scale(roi, tmpl, scales=(0.7, 0.85, 1.0, 1.15, 1.3, 1.45), threshold=0.60)
            if pts and pts[0][2] > best_score:
                best_key, best_score = key, pts[0][2]
    return best_key, best_score


# ---------------- 房间空位数字（左上角门图标下方 "已入座/容量"） ----------------
ROOM_DOOR_RX = {"bronze": 0.048, "silver": 0.112, "gold": 0.182}
COUNT_BAND_Y = (0.252, 0.29)   # 数字带高度范围（占画面高比例）
GLYPH_W, GLYPH_H = 24, 32      # 字形归一化尺寸

_digit_templates = None


def _load_digit_templates():
    """加载 templates/digit_<类>_<序号>.png（24x32 白字掩码）。类: 0-9 / slash"""
    global _digit_templates
    if _digit_templates is None:
        out = {}
        try:
            for f in sorted(os.listdir(TEMPLATES_DIR)):
                if f.startswith("digit_") and f.endswith(".png"):
                    d = f[len("digit_"):].rsplit("_", 1)[0]
                    p = os.path.join(TEMPLATES_DIR, f)
                    g = cv2.imdecode(np.fromfile(p, dtype=np.uint8), cv2.IMREAD_GRAYSCALE)
                    if g is not None:
                        out.setdefault(d, []).append(g)
        except OSError:
            pass
        _digit_templates = out
    return _digit_templates


def _segment_count_glyphs(band, min_h, max_w):
    n, labels, stats, _ = cv2.connectedComponentsWithStats(band, 8)
    boxes = []
    for i in range(1, n):
        x, y, bw, bh, area = stats[i]
        if bh >= min_h and area >= 60 and bw <= max_w:
            boxes.append((x, y, bw, bh))
    boxes.sort()
    return boxes


def _classify_glyph(g, min_score=0.55):
    """g: 归一化字形掩码 -> (类别, 得分)。与所有模板逐一比对取最高分"""
    best, bs = None, -1.0
    for d, tmpls in _load_digit_templates().items():
        for t in tmpls:
            s = float(cv2.matchTemplate(g, t, cv2.TM_CCOEFF_NORMED)[0, 0])
            if s > bs:
                bs, best = s, d
    return (best if bs >= min_score else None), bs


def read_room_counts(img):
    """读左上角三个门下方的数字 "已入座/容量"（常显，不需拖拽）。
    返回 {room: (n, m) | None}，读不出（字形缺失/遮挡）的房间为 None。
    注：数字字形模板来自实测截图，3/4/7/9 尚无样本（识别为 None，保守处理）。"""
    h, w = img.shape[:2]
    scale = h / 1494.0
    min_h = max(12, int(20 * scale))
    max_w = int(40 * scale)
    hsv = cv2.cvtColor(img, cv2.COLOR_BGR2HSV)
    white = ((hsv[:, :, 2] >= 200) & (hsv[:, :, 1] <= 70)).astype(np.uint8) * 255
    y0, y1 = int(h * COUNT_BAND_Y[0]), int(h * COUNT_BAND_Y[1])
    out = {}
    for room, rx in ROOM_DOOR_RX.items():
        cx = int(w * rx)
        band = np.zeros_like(white)
        band[y0:y1, max(0, cx - 70):cx + 70] = white[y0:y1, max(0, cx - 70):cx + 70]
        boxes = _segment_count_glyphs(band, min_h, max_w)
        if not 3 <= len(boxes) <= 5:
            out[room] = None
            continue
        classes = []
        for x, y, bw, bh in boxes:
            g = cv2.resize(band[y:y + bh, x:x + bw], (GLYPH_W, GLYPH_H),
                           interpolation=cv2.INTER_AREA)
            c, _ = _classify_glyph(g)
            classes.append(c)
        if any(c is None for c in classes) or "slash" not in classes:
            out[room] = None
            continue
        si = classes.index("slash")
        try:
            n = int("".join(classes[:si]))
            m = int("".join(classes[si + 1:]))
        except ValueError:
            out[room] = None
            continue
        out[room] = (n, m) if 0 <= n <= m else None
    return out


def find_room_doors(img):
    """左上角三个房间门按钮（多套模板取最优 + 比例位置兜底）。
    返回 {"bronze": (cx,cy), "silver": (cx,cy), "gold": (cx,cy)}"""
    out = {}
    h, w = img.shape[:2]
    roi = img[: int(h * 0.35), : int(w * 0.30)]
    # 每个门的横向搜索带（占画面宽度比例）：门排列固定为 铜/银/金 从左到右
    bands = {"bronze": (0.02, 0.08), "silver": (0.085, 0.15), "gold": (0.15, 0.22)}
    for key in ("bronze", "silver", "gold"):
        x_lo, x_hi = int(w * bands[key][0]), int(w * bands[key][1])
        best = None
        for tmpl in _load_all_prefix("door_" + key):
            pts = _match_multi_scale(roi, tmpl, scales=(0.7, 0.85, 1.0, 1.15, 1.3, 1.45), threshold=0.68)
            for x, y, sc, s in pts:
                if x_lo <= x <= x_hi and (best is None or sc > best[2]):
                    best = (x, y, sc)
        if best is not None:
            out[key] = (best[0], best[1])
    # 兜底：门按钮位置固定为 左→右 铜/银/金，相对游戏区域比例约 4.5%/11%/18% 宽、21% 高
    fallback = {"bronze": 0.048, "silver": 0.112, "gold": 0.182}
    for key, rx in fallback.items():
        if out.get(key) is None:
            out[key] = (int(w * rx), int(h * 0.21))
    return out
