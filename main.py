# -*- coding: utf-8 -*-
"""店铺助理 - 主程序（悬浮控制窗 + 自动化工作线程）

功能：
  1. 把顾客区的顾客拖到匹配的空桌上
  2. 定时点击右下角『收集场内所有金币』
  3. 可选：自动轮流切换 铜/银/金 房间 
"""
import json
import os
import random
import sys
import threading
import time
import tkinter as tk

import ctypes
try:
    ctypes.windll.shcore.SetProcessDpiAwareness(2)
except Exception:
    try:
        ctypes.windll.user32.SetProcessDPIAware()
    except Exception:
        pass

import vision
import actions

# 打包成 exe 后：可写文件（config.json）放在 exe 旁边；只读资源在 _MEIPASS
if getattr(sys, "frozen", False):
    APP_DIR = os.path.dirname(sys.executable)
else:
    APP_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(APP_DIR, "config.json")

DEFAULT_CONFIG = {
    "region": None,          # {"left":..,"top":..,"width":..,"height":..}，None=全屏
    "drag_duration": 0.45,
    "loop_interval": 0.3,
    "collect_interval": 6.0,
    "room_cycle": True,
    "room_cycle_interval": 20.0,
    "any_match": False,      # 任意匹配模式：有空位就放，不做标签匹配
}


def load_config():
    cfg = dict(DEFAULT_CONFIG)
    if os.path.exists(CONFIG_PATH):
        try:
            with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                cfg.update(json.load(f))
        except Exception:
            pass
    return cfg


def save_config(cfg):
    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)


class Worker(threading.Thread):
    """自动化工作线程"""

    def __init__(self, cfg, log_fn, finish_fn=None):
        super().__init__(daemon=True)
        self.cfg = cfg
        self.log = log_fn
        self.finish_fn = finish_fn  # 线程结束（含自动停止）后回调，供 App 复位按钮
        self.stop_event = threading.Event()
        self._room_idx = 0
        self._last_collect = 0.0
        self._last_room_switch = 0.0
        self._room_order = ["silver", "gold", "bronze"]  # 房间使用顺序：银→金→铜
        self._known_room = None    # 当前所在房间（开局检测一次，切房时直接更新）
        self._room_tags = {}       # 本局桌台标签缓存 {room: {(gx,gy): frozenset}}（店员开局后固定）
        self._pending_seat = None  # (槽位x, 槽位y, 拖放前顾客数, 桌x, 桌y) 待核验的入座
        self._table_fails = {}     # {room: {(kx,ky): [连续失败次数, 最近失败时间]}} 满桌临时拉黑
        self._last_end_check = 0.0 # 上次『结束营业』检测时间（每 2 秒查一次即可）

    def run(self):
        self.log("▶ 助理已开始工作")
        while not self.stop_event.is_set():
            try:
                self._tick()
            except Exception as e:
                self.log(f"⚠ 出错: {e}")
                time.sleep(1.5)
            time.sleep(self.cfg.get("loop_interval", 1.2))
        self.log("■ 助理已停止")
        if self.finish_fn:
            self.finish_fn()

    # ------- 单次循环 -------
    def _tick(self):
        region = self.cfg.get("region")
        img = vision.capture(region) if region else vision.capture(mss_fullscreen())

        # 0) 游戏结束检测：出现『结束营业』结账界面即本局已结束
        #    （结账界面会停留很久，每 2 秒查一次足够，省下每轮 ~35ms 扫描）
        now = time.time()
        if now - self._last_end_check >= 2.0:
            self._last_end_check = now
            end_btn = vision.find_end_business(img)
            if end_btn:
                self.log("🏁 检测到「结束营业」，本局游戏已结束，自动重启营业")
                self._restart_from_end(end_btn)
                return
            # 事件弹窗（全息弹窗/美味时间等）：随机选一个选项，等 ~2s 弹窗自行消失
            popup = vision.find_event_popup(img)
            if popup:
                choice = random.choice(popup)
                self.log(f"🪟 检测到事件弹窗，随机选择选项（{len(popup)} 选 1）")
                actions.click(*choice)
                time.sleep(2.0)
                return

        # 开局识别一次当前房间
        if self._known_room is None:
            room, _ = vision.detect_current_room(img)
            if room:
                self._known_room = room
                self.log(f"📍 当前房间: {room}")

        # 1) 收金币
        if now - self._last_collect >= self.cfg.get("collect_interval", 6.0):
            self._collect_coins(img)
            self._last_collect = now

        # 2) 分配顾客（按住-拖出-扫描-入座）
        # 满员快切：当前房间数字显示没有空位，直接换房，不必拖拽扫描
        cur = self._known_room
        if cur and now - self._last_room_switch > 8:
            nm = vision.read_room_counts(img).get(cur)
            if nm and nm[0] >= nm[1]:
                self.log(f"🈵 {cur} 房间已满（{nm[0]}/{nm[1]}），提前切换")
                self._switch_room(img)
                self._last_room_switch = now
                return
        self._assign_customers(img)

        # 3) 切换房间（定时轮换）
        if self.cfg.get("room_cycle", True):
            if now - self._last_room_switch >= self.cfg.get("room_cycle_interval", 20.0):
                self._switch_room(img)
                self._last_room_switch = now

    def _restart_from_end(self, end_btn):
        """本局结束后的自动重启链：点击结算页「结束营业」→ 游戏首页「开始营业」
        → 开业准备页「开始营业」→ 重新进入游戏，继续挂机。
        页面加载需 1~3 秒，用状态轮询（点击后重截图确认下一步）而非盲等；
        总超时 45s，超时放弃并停止（避免盲目乱点）。"""
        actions.click(*end_btn)
        self.log("👆 已点击「结束营业」，返回游戏首页")
        time.sleep(1.5)

        deadline = time.time() + 45
        clicked = set()  # 需要点掉的两个页面：游戏首页 + 开业准备页（按模板名去重，
                         # 防止页面切换慢时同一页被重复点击、提前凑满次数而漏点下一页）
        while time.time() < deadline and not self.stop_event.is_set():
            img = vision.capture(self.cfg.get("region")) if self.cfg.get("region") \
                else vision.capture(mss_fullscreen())
            # 结算页可能因动画延迟仍在，优先复检
            end2 = vision.find_end_business(img)
            if end2:
                actions.click(*end2)
                time.sleep(1.5)
                continue
            hit = vision.find_start_business(img)
            if hit:
                bx, by, page = hit
                actions.click(bx, by)
                clicked.add(page)
                self.log(f"👆 已点击「开始营业」（{len(clicked)}/2：{page}）")
                if len(clicked) >= 2:
                    time.sleep(3.0)  # 等开店加载动画
                    break
                time.sleep(2.0)
                continue
            time.sleep(1.0)  # 页面跳转中，稍候重试

        if len(clicked) < 2:
            self.log(f"⚠ 重启营业流程超时（仅完成 {len(clicked)}/2 个页面），自动停止")
            self.stop_event.set()
            return

        # 新一局开始：清空本局状态（店员/桌台可能重新安排）
        self._known_room = None
        self._room_tags.clear()
        self._table_fails.clear()
        self._pending_seat = None
        self._last_room_switch = 0.0
        self._last_collect = 0.0
        self._last_end_check = time.time() + 10  # 刚进游戏，10s 内不再查结算页
        self.log("▶ 新的一局已开始，继续自动营业")

    def _collect_coins(self, img):
        # 先点一键收集按钮
        btn = vision.find_collect_button(img)
        if btn:
            actions.click(*btn)
            self.log("💰 已点击一键收集")
            time.sleep(0.4)
            img = vision.capture(self.cfg.get("region")) if self.cfg.get("region") else vision.capture(mss_fullscreen())
        # 再逐个点场内剩余金币堆
        coins = vision.find_coins(img)
        for cx, cy in coins[:8]:
            actions.click(cx, cy)
            time.sleep(0.1)
        if coins:
            self.log(f"💰 点收 {len(coins)} 处金币")

    def _learn_table_tags(self, img2, staffed):
        """学习本房间每张有店员桌子的标签（拖拽时可见，每局固定），首次学到时汇报"""
        room = self._known_room
        if not room or not staffed:
            return
        cache = self._room_tags.setdefault(room, {})
        new_tags = []
        for tx, ty, tags in staffed:
            key = (round(tx / 60), round(ty / 60))
            if key not in cache:
                cache[key] = frozenset(tags)
                new_tags.append("%s@(%d,%d)" % (sorted(tags), tx, ty))
        if new_tags:
            self.log(f"📋 {room} 房间桌台标签: " + "; ".join(new_tags))

    def _mark_table(self, tx, ty, ok):
        """记录某桌的入座结果：成功清零，失败累加（用于满桌临时拉黑）"""
        room = self._known_room
        if not room:
            return
        m = self._table_fails.setdefault(room, {})
        k = (round(tx / 60), round(ty / 60))
        if ok:
            m.pop(k, None)
        else:
            c, _ = m.get(k, [0, 0.0])
            m[k] = [c + 1, time.time()]

    def _skippable(self, tx, ty):
        """最近 30s 内连续 2 次入座失败的桌视为已满，临时跳过"""
        room = self._known_room
        if not room:
            return False
        info = self._table_fails.get(room, {}).get((round(tx / 60), round(ty / 60)))
        return bool(info) and info[0] >= 2 and time.time() - info[1] < 30

    def _resolve_tags(self, img2, tables):
        """对桌点列表读标签（拖拽时可见，每局固定），并学习缓存"""
        staffed = []
        for tx, ty in tables:
            tags = vision.read_table_tags(img2, tx, ty)
            if tags:
                staffed.append((tx, ty, tags))
        self._learn_table_tags(img2, staffed)
        return staffed

    def _scan_held(self, region, h, cx, cy, light=False):
        """按住顾客拖出后快速扫描：返回 (img2, held, pref, tables, staffed)。
        不扫绿光——标签匹配失败时才由 _glow_fallback 补扫（省一次全帧 HSV）。
        light=True（任意匹配模式）：跳过偏好与标签识别，只找空桌。"""
        img2 = vision.capture(region) if region else vision.capture(mss_fullscreen())
        held = (cx, cy - int(h * 0.28))
        if light:
            return img2, held, None, vision.find_empty_tables(img2, room=self._known_room), []
        pref = vision.read_customer_pref(img2, *held)
        tables = vision.find_empty_tables(img2, room=self._known_room)
        staffed = self._resolve_tags(img2, tables)
        return img2, held, pref, tables, staffed

    def _glow_fallback(self, img2, held, tables):
        """标签没匹配上时才扫绿光（游戏官方匹配信号）：返回 (glow, staffed2)。
        glow = 绿光 blob 质心列表（发光桌的『+』常被绿光干扰漏检，用质心补充）。"""
        glow = [g for g in vision.find_glow_tables(img2)
                # 排除拖拽点附近（被按住顾客卡片的青色边框会造成绿光 FP）
                if (g[0] - held[0]) ** 2 + (g[1] - held[1]) ** 2 > 160 ** 2]
        # 合并（绿光质心≈桌心，120px 去重：绿光环碎片贴着已识别桌时丢弃）
        tables2 = list(tables)
        for gx, gy in glow:
            if all((gx - tx) ** 2 + (gy - ty) ** 2 > 120 ** 2 for tx, ty in tables2):
                tables2.append((gx, gy))
        return glow, self._resolve_tags(img2, tables2)

    def _assign_customers(self, img):
        """一轮内连续安排多位顾客：按住拖出 -> 读偏好+桌台标签 -> 标签匹配 -> 拖入。
        匹配依据是桌下标签条（每局固定）与顾客偏好图标的交集，不依赖绿光；
        标签匹配失败时才补扫绿光（游戏官方匹配信号）。"""
        region = self.cfg.get("region")
        seated = 0
        total_ms = 0.0
        tried = set()  # 本轮已尝试过的桌（核验失败后不再立即重试同一张）
        for _ in range(6):  # 单轮最多安排 6 位，防止卡死
            if self._pending_seat:
                time.sleep(0.25)  # 等入座/弹回动画结束再核验
            img = vision.capture(region) if region else vision.capture(mss_fullscreen())
            customers = vision.find_customers(img)
            # 核验上一次拖放：原槽位仍有顾客且总数未减少 => 没坐进去（桌可能已满）
            if self._pending_seat:
                pcx, pcy, pcount, ptx, pty = self._pending_seat
                self._pending_seat = None
                ok = True
                if customers:
                    still = any((x - pcx) ** 2 + (y - pcy) ** 2 <= 40 ** 2 for x, y in customers)
                    ok = len(customers) < pcount or not still
                self._mark_table(ptx, pty, ok)
                if not ok:
                    tried.add((round(ptx / 60), round(pty / 60)))
                    self.log(f"⚠ ({ptx},{pty}) 桌入座失败（可能已满），暂时跳过")
            if not customers:
                break
            cx, cy = customers[0]  # 耐心最少者优先
            h = (region or mss_fullscreen())["height"]
            t0 = time.time()
            # 按住顾客拖出顾客区，触发『+』、桌下标签条与偏好图标显示
            actions.pickup(cx, cy, cx, cy - int(h * 0.28), duration=0.08)
            time.sleep(0.05)
            any_mode = self.cfg.get("any_match", False)
            img2, held, pref, tables, staffed = self._scan_held(region, h, cx, cy, light=any_mode)
            if any_mode:
                # 任意匹配：所有空桌都是候选（排除拉黑桌），绿光桌也并入
                matched = [(tx, ty) for tx, ty in tables
                           if (round(tx / 60), round(ty / 60)) not in tried
                           and not self._skippable(tx, ty)]
            else:
                # 标签匹配为主：顾客偏好 ∈ 桌台标签（排除拉黑桌）
                matched = [(tx, ty) for tx, ty, tags in staffed
                           if pref and pref in tags
                           and (round(tx / 60), round(ty / 60)) not in tried
                           and not self._skippable(tx, ty)]
                if not matched and not staffed and not tables:
                    # 标签条可能还没渲染完：快速重试一次
                    time.sleep(0.07)
                    img2, held, pref, tables, staffed = self._scan_held(region, h, cx, cy)
                    matched = [(tx, ty) for tx, ty, tags in staffed
                               if pref and pref in tags
                               and (round(tx / 60), round(ty / 60)) not in tried
                               and not self._skippable(tx, ty)]
            if not matched:
                # 标签没匹配上才扫绿光作补充（同样排除拉黑桌）
                glow, staffed = self._glow_fallback(img2, held, tables)
                for gx, gy in glow:
                    if (round(gx / 60), round(gy / 60)) in tried or self._skippable(gx, gy):
                        continue
                    if all((gx - mx) ** 2 + (gy - my) ** 2 > 60 ** 2 for mx, my in matched):
                        matched.append((gx, gy))
            if not matched:
                # 无匹配桌：原位松开，顾客自动回到顾客区
                actions.drop_cancel()
                if any_mode:
                    self.log("🈳 本房间没有空桌")
                elif staffed:
                    self.log(f"🈳 偏好{pref} 无匹配桌（有店员的桌 {len(staffed)} 张）")
                else:
                    self.log("🈳 本房间没有带店员的空桌")
                if self.cfg.get("room_cycle", True) and time.time() - self._last_room_switch > 8:
                    self._switch_room(img2)
                    self._last_room_switch = time.time()
                return seated
            tx, ty = min(matched, key=lambda t: (t[0] - cx) ** 2 + (t[1] - cy) ** 2)
            actions.drop_at(tx, ty)
            self._pending_seat = (cx, cy, len(customers), tx, ty)
            seated += 1
            total_ms += (time.time() - t0) * 1000
        if seated:
            self.log(f"🪑 本轮安排 {seated} 位入座（平均 {int(total_ms / seated)}ms/位）")
        return seated

    def _switch_room(self, img):
        order = self._room_order  # 银→金→铜
        # 通过右上角房间牌判断当前房间，切到下一个
        current = self._known_room
        if current is None or current not in order:
            current, _ = vision.detect_current_room(img)
        if current in order:
            seq = order[(order.index(current) + 1):] + order[:order.index(current) + 1]
        else:
            seq = order[:]
        # 优先切到有空位的房间（门下数字 "已入座/容量"）；读不到就按顺序下一个
        counts = vision.read_room_counts(img)
        free = {r for r, nm in counts.items() if nm and nm[0] < nm[1]}
        target = next((r for r in seq if r in free), None)
        if target is None:
            if current in free:
                nm = counts[current]
                self.log(f"⏭ 其他房间都满员，当前房间还有空位（{nm[0]}/{nm[1]}），暂不切换")
                return
            target = seq[0]
        pos = vision.find_room_doors(img).get(target)
        if pos:
            actions.click(*pos)
            self._known_room = target
            self.log(f"🚪 切换房间: {target}（空位 {counts.get(target)}）")
            time.sleep(1.0)
        else:
            self.log("⚠ 未找到房间门按钮")


def mss_fullscreen():
    import mss
    with mss.mss() as s:
        mon = s.monitors[1]  # 主屏
    return {"left": mon["left"], "top": mon["top"], "width": mon["width"], "height": mon["height"]}


class App:
    # 全局热键（游戏全屏时本窗口无焦点，普通按键绑定收不到，用 RegisterHotKey 系统级热键）
    HOTKEYS = {1: (0x0000, 0x77, "F8"),   # id: (修饰键, VK, 名称) F8=开始
               2: (0x0000, 0x78, "F9")}   # F9=结束
    WM_HOTKEY = 0x0312

    def __init__(self):
        self.cfg = load_config()
        vision.GAME_LANG = self.cfg.get("game_lang", "cn")  # 按钮识别模板语言（中文/日语）
        self.worker = None
        self.root = tk.Tk()
        self.root.title("店铺助理")
        self.root.attributes("-topmost", True)
        self.root.resizable(False, False)
        self._set_icon()
        self._build_ui()
        threading.Thread(target=self._hotkey_thread, daemon=True).start()

    def _set_icon(self):
        """窗口与任务栏图标（assets/logo.png，打包后随 _MEIPASS 资源目录）"""
        try:
            icon_path = os.path.join(vision.BASE_DIR, "assets", "logo.png")
            if os.path.exists(icon_path):
                self._icon = tk.PhotoImage(file=icon_path)
                self.root.iconphoto(True, self._icon)
        except Exception:
            pass  # 图标加载失败不影响功能

    def _build_ui(self):
        pad = {"padx": 8, "pady": 4}
        frame = tk.Frame(self.root, bd=0)
        frame.pack(fill="both", expand=True)

        self.start_btn = tk.Button(frame, text="▶ 开始 (F8)", width=14, bg="#4CAF50", fg="white",
                                   font=("Microsoft YaHei", 11, "bold"), command=self.start)
        self.start_btn.grid(row=0, column=0, **pad)
        self.stop_btn = tk.Button(frame, text="■ 结束 (F9)", width=14, bg="#9E9E9E", fg="white",
                                  font=("Microsoft YaHei", 11, "bold"), command=self.stop, state="disabled")
        self.stop_btn.grid(row=0, column=1, **pad)

        self.room_var = tk.BooleanVar(value=self.cfg.get("room_cycle", True))
        tk.Checkbutton(frame, text="自动切换房间（银→金→铜）", variable=self.room_var,
                       command=self._toggle_room, font=("Microsoft YaHei", 9)).grid(row=1, column=0, columnspan=2, sticky="w", **pad)

        self.any_var = tk.BooleanVar(value=self.cfg.get("any_match", False))
        tk.Checkbutton(frame, text="任意匹配模式（有空位就放，不做标签匹配）", variable=self.any_var,
                       command=self._toggle_any, font=("Microsoft YaHei", 9)).grid(row=2, column=0, columnspan=2, sticky="w", **pad)

        # 游戏语言：决定「结束营业 / 开始营业」按钮识别模板（中日两套，界面不同）
        self.lang_var = tk.StringVar(value=self.cfg.get("game_lang", "cn"))
        lang_frame = tk.Frame(frame)
        lang_frame.grid(row=3, column=0, columnspan=2, sticky="w", **pad)
        tk.Label(lang_frame, text="游戏语言：", font=("Microsoft YaHei", 9)).pack(side="left")
        for val, label in (("cn", "简体中文"), ("jp", "日本語")):
            tk.Radiobutton(lang_frame, text=label, value=val, variable=self.lang_var,
                           command=self._toggle_lang, font=("Microsoft YaHei", 9)).pack(side="left")

        self.calib_btn = tk.Button(frame, text="框选游戏区域", width=27,
                                   font=("Microsoft YaHei", 9), command=self.calibrate)
        self.calib_btn.grid(row=4, column=0, columnspan=2, **pad)

        # 实时日志框（只读，按行动态刷新，自动滚动到底部）
        log_frame = tk.Frame(frame)
        log_frame.grid(row=5, column=0, columnspan=2, padx=8, pady=(2, 8), sticky="we")
        self.log_box = tk.Text(log_frame, height=12, width=48, wrap="char",
                               font=("Microsoft YaHei", 9), state="disabled",
                               bg="#fafafa", fg="#333", relief="solid", bd=1)
        log_scroll = tk.Scrollbar(log_frame, command=self.log_box.yview)
        self.log_box.config(yscrollcommand=log_scroll.set)
        self.log_box.pack(side="left", fill="both", expand=True)
        log_scroll.pack(side="right", fill="y")

        r = self.cfg.get("region")
        self._log("⌨ 快捷键：F8 开始 / F9 结束（全局有效，游戏内可用）")
        if r:
            self._log(f"游戏区域 {r['width']}x{r['height']}（如需调整请重新框选）")

    def _hotkey_thread(self):
        """注册全局热键并循环分发（守护线程，进程退出自动注销）"""
        import ctypes.wintypes
        user32 = ctypes.windll.user32
        failed = []
        for hid, (mod, vk, name) in self.HOTKEYS.items():
            if not user32.RegisterHotKey(None, hid, mod, vk):
                failed.append(name)
        if failed:
            self.root.after(0, self._log, "⚠ 快捷键 %s 注册失败（可能被其他程序占用）" % "/".join(failed))
        msg = ctypes.wintypes.MSG()
        while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
            if msg.message == self.WM_HOTKEY:
                if msg.wParam == 1:
                    self.root.after(0, self.start)
                elif msg.wParam == 2:
                    self.root.after(0, self.stop)

    def _toggle_room(self):
        self.cfg["room_cycle"] = self.room_var.get()
        save_config(self.cfg)

    def _toggle_any(self):
        self.cfg["any_match"] = self.any_var.get()
        save_config(self.cfg)
        if self.cfg["any_match"]:
            self._log("⚡ 任意匹配模式：有空位就放，不做标签匹配")

    def _toggle_lang(self):
        self.cfg["game_lang"] = self.lang_var.get()
        save_config(self.cfg)
        vision.GAME_LANG = self.cfg["game_lang"]
        self._log("🌐 游戏语言：" + ("日本語（営業終了/営業開始 模板）" if self.cfg["game_lang"] == "jp"
                                else "简体中文（结束营业/开始营业 模板）"))

    def calibrate(self):
        self.root.withdraw()
        try:
            if getattr(sys, "frozen", False):
                # 打包后不能用 subprocess：sys.executable 是 exe 本身，把它当 python
                # 调用会再启动一个程序实例（抢注 F8/F9 热键）。
                # 也不能在进程内再建第二个 tk.Tk()（嵌套解释器会在框选结束后
                # 卡死主窗口事件循环）。改为在同一解释器里创建 Toplevel 遮罩，
                # 并显式传入 exe 目录的 config.json（calibrate.py 的 __file__
                # 在 _MEIPASS 临时目录里，默认路径会写错位置）。
                import importlib.util
                path = os.path.join(vision.BASE_DIR, "tools", "calibrate.py")
                spec = importlib.util.spec_from_file_location("shop_calibrate", path)
                mod = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(mod)
                mod.main(config_path=CONFIG_PATH, parent=self.root)
            else:
                import subprocess
                subprocess.call([sys.executable, os.path.join(APP_DIR, "tools", "calibrate.py")])
            self.cfg = load_config()
            r = self.cfg.get("region")
            if r:
                self._log(f"游戏区域已更新: {r['width']}x{r['height']}")
        finally:
            self.root.deiconify()

    def start(self):
        if self.worker and self.worker.is_alive():
            return
        if not self.cfg.get("region"):
            self._log("⚠ 请先点击『框选游戏区域』选定游戏画面")
            return
        self.worker = Worker(self.cfg, lambda m: self.root.after(0, self._log, m),
                             finish_fn=lambda: self.root.after(0, self._worker_finished))
        self.worker.start()
        self.start_btn.config(state="disabled")
        self.stop_btn.config(state="normal", bg="#f44336")

    def stop(self):
        if self.worker:
            self.worker.stop_event.set()
            self.worker = None
        self.start_btn.config(state="normal")
        self.stop_btn.config(state="disabled", bg="#9E9E9E")

    def _worker_finished(self):
        """Worker 线程结束（含检测到游戏结束自动停止）后复位按钮。
        手动停止时 stop() 已先复位，这里幂等处理 worker 已置 None 的情况。"""
        self.worker = None
        self.start_btn.config(state="normal")
        self.stop_btn.config(state="disabled", bg="#9E9E9E")

    def _log(self, msg):
        # 追加到日志框（带时间戳，最多保留 300 行，自动滚到底部）
        self.log_box.config(state="normal")
        self.log_box.insert("end", f"[{time.strftime('%H:%M:%S')}] {msg}\n")
        if int(self.log_box.index("end-1c").split(".")[0]) > 300:
            self.log_box.delete("1.0", "2.0")
        self.log_box.see("end")
        self.log_box.config(state="disabled")

    def run(self):
        self.root.mainloop()


if __name__ == "__main__":
    App().run()
