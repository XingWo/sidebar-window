#!/usr/bin/env python3

import json
import os
import random
import subprocess
import sys
import threading
import time
import tkinter as tk
from tkinter import messagebox, filedialog
from pathlib import Path

# --- 实例锁 ---
_LOCK_FILE = Path(__file__).with_name(".sidebar.lock")
_LOCK_FD = None

def _acquire_single_instance():
    """通过文件锁保证只运行一个实例"""
    global _LOCK_FD
    try:
        _LOCK_FD = open(_LOCK_FILE, "w")
        if sys.platform == "win32":
            import msvcrt
            msvcrt.locking(_LOCK_FD.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(_LOCK_FD.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        _LOCK_FD.write(str(os.getpid()))
        _LOCK_FD.flush()
        return True
    except (OSError, IOError):
        if _LOCK_FD:
            _LOCK_FD.close()
            _LOCK_FD = None
        return False

# --- 隐藏控制面板 ---
def _hide_console():
    """Windows 下隐藏命令行窗口"""
    if sys.platform == "win32":
        try:
            import ctypes
            hwnd = ctypes.windll.kernel32.GetConsoleWindow()
            if hwnd:
                ctypes.windll.user32.ShowWindow(hwnd, 0)  # SW_HIDE
        except Exception:
            pass


# --- 自动配置依赖 ---
def _ensure_deps():
    deps = [
        ("pyautogui", "pyautogui"),
        ("pyperclip", "pyperclip"),
        ("openai", "openai"),
        ("pystray", "pystray"),
        ("win32clipboard", "pywin32"),
    ]
    missing = []
    pip_names = []
    for mod, pkg in deps:
        try:
            __import__(mod)
        except ImportError:
            missing.append(mod)
            pip_names.append(pkg)
    if missing:
        print(f"⚠ 缺少 {missing} 模块，正在自动安装...")
        subprocess.check_call([sys.executable, "-m", "pip", "install", *pip_names])

def _ensure_extra_deps():
    missing = []
    for mod in ("windnd",):
        try:
            __import__(mod)
        except ImportError:
            missing.append(mod)
    if missing:
        subprocess.check_call([sys.executable, "-m", "pip", "install", *missing])

_ensure_deps()
_ensure_extra_deps()

_hide_console()
import pyautogui
import pyperclip
import windnd
from openai import OpenAI
from pystray import Icon, Menu, MenuItem
from PIL import Image, ImageDraw

# pyautogui 安全设置
pyautogui.FAILSAFE = True
pyautogui.PAUSE = 0.05

# --- 保持运行 ---
DATA_FILE = Path(__file__).with_name("sidebar_buttons.json")

# 翻译方向列表
TRANSLATE_OPTIONS = [
    "中英", "中日", "中韩",
    "中法", "中德", "中俄",
    "中西", "中阿", "中泰",
    "中意", "中葡", "中葡(巴西语)",
    "中越", "中印", "中土", "中荷",
    "中波", "中瑞", "中丹",
    "中芬", "中挪", "中捷",
    "中罗", "中匈", "中希",
    "中希伯来", "中马来",
    "中印尼", "中菲律宾",
    "自动检测",
]



def load_data():
    if DATA_FILE.exists():
        raw = json.loads(DATA_FILE.read_text("utf-8"))
        return raw
    return {
        "buttons": [],
        "side": "right",
        "settings": {
            "api_key": "",
            "base_url": "https://api.xiaomimimo.com/v1",
            "model": "mimo-v2-flash",
            "translate": "中英",
        },
    }

def save_data(data):
    DATA_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2), "utf-8")


# --- 。。。 ---
class Sidebar:
    WIDTH_COLLAPSED = 17
    WIDTH_EXPANDED = 300
    LONG_PRESS_MS = 600

    def __init__(self, side="right"):
        self.data = load_data()
        self.buttons_data = self.data["buttons"]
        self.side = self.data.get("side", side)
        if "settings" not in self.data:
            self.data["settings"] = {
                "api_key": "", "base_url": "https://api.xiaomimimo.com/v1",
                "model": "mimo-v2-flash", "translate": "中英",
            }

        self.root = tk.Tk()
        self.root.overrideredirect(True)
        self.root.attributes("-topmost", True)
        self.root.configure(bg="#2b2b2b")

        sw = self.root.winfo_screenwidth()
        sh = self.root.winfo_screenheight()
        self.screen_h = sh
        x = sw - self.WIDTH_COLLAPSED if self.side == "right" else 0
        self.root.geometry(f"{self.WIDTH_COLLAPSED}x{sh}+{x}+0")

        self.expanded = False
        self._dropdown_open = False  # 标记下拉列表是否打开
        self._drag_active = False    # 标记按钮拖动进行中

        # ── 内容区 ──
        self.content = tk.Frame(self.root, bg="#2b2b2b")
        self.content.place(x=0, y=0, width=self.WIDTH_EXPANDED, height=sh)

        # 输入框
        self.entry_var = tk.StringVar()
        self.entry = tk.Entry(
            self.content, textvariable=self.entry_var,
            font=("Microsoft YaHei", 11), bg="#3c3c3c", fg="#e0e0e0",
            insertbackground="#e0e0e0", relief="flat", bd=0,
        )
        self.entry.pack(fill="x", padx=12, pady=(14, 6), ipady=6)
        self.entry.bind("<Return>", lambda e: self._do_ai_chat())

        # 可滚动按钮区
        self.canvas = tk.Canvas(self.content, bg="#2b2b2b", highlightthickness=0)
        self.scrollbar = tk.Scrollbar(self.content, orient="vertical", command=self.canvas.yview)
        self.btn_frame = tk.Frame(self.canvas, bg="#2b2b2b")
        self.btn_frame.bind("<Configure>",
                            lambda e: self.canvas.configure(scrollregion=self.canvas.bbox("all")))
        self.canvas.create_window((0, 0), window=self.btn_frame, anchor="nw",
                                  tags="btn_window")
        self.canvas.configure(yscrollcommand=self.scrollbar.set)
        self.canvas.bind("<Configure>", self._on_canvas_resize)

        self.canvas.pack(side="left", fill="both", expand=True, padx=(12, 0), pady=6)
        self.scrollbar.pack(side="right", fill="y", pady=6)

        self.canvas.bind_all("<MouseWheel>", self._on_mousewheel)
        self.canvas.bind_all("<Button-4>", self._on_mousewheel)
        self.canvas.bind_all("<Button-5>", self._on_mousewheel)

        # 折叠窄条
        self.hint = tk.Frame(self.root, bg="#444", width=self.WIDTH_COLLAPSED)
        self.hint.place(x=0, y=0, width=self.WIDTH_COLLAPSED, height=sh)
        tk.Label(self.hint, text="◀" if self.side == "right" else "▶",
                 bg="#444", fg="#aaa", font=("Arial", 10)).place(
            relx=0.5, rely=0.5, anchor="center")

        self.root.bind("<Enter>", self._on_enter)
        self.root.bind("<Leave>", self._on_leave)

        # 关闭窗口时最小化到托盘
        self.root.protocol("WM_DELETE_WINDOW", self._minimize_to_tray)

        self._tray_icon = None
        self._tray_running = False

        self._rebuild_buttons()
        self._set_collapsed()

    def _on_canvas_resize(self, event):
        self.canvas.itemconfigure("btn_window", width=event.width)

    # --- 展开缩回 ---
    def _set_collapsed(self):
        sw = self.root.winfo_screenwidth()
        x = sw - self.WIDTH_COLLAPSED if self.side == "right" else 0
        self.root.geometry(f"{self.WIDTH_COLLAPSED}x{self.screen_h}+{x}+0")
        self.content.place_forget()
        self.hint.place(x=0, y=0, width=self.WIDTH_COLLAPSED, height=self.screen_h)
        self.expanded = False

    def _set_expanded(self):
        sw = self.root.winfo_screenwidth()
        x = sw - self.WIDTH_EXPANDED if self.side == "right" else 0
        self.root.geometry(f"{self.WIDTH_EXPANDED}x{self.screen_h}+{x}+0")
        self.hint.place_forget()
        self.content.place(x=0, y=0, width=self.WIDTH_EXPANDED, height=self.screen_h)
        self.expanded = True

    def _on_enter(self, _=None):
        self._set_expanded()

    def _on_leave(self, event):
        if self._drag_active:
            return  # 拖动按钮期间不收起侧边栏，避免按钮位置失效导致误判
        try:
            x, y = event.x_root, event.y_root
            gx = self.root.winfo_rootx()
            gy = self.root.winfo_rooty()
            gw = self.root.winfo_width()
            gh = self.root.winfo_height()
            if not (gx <= x <= gx + gw and gy <= y <= gy + gh):
                self._set_collapsed()
        except Exception:
            pass

    def _on_mousewheel(self, event):
        # 下拉列表打开时，不处理主画布的滚动
        if self._dropdown_open:
            return
        if event.num == 4:
            self.canvas.yview_scroll(-3, "units")
        elif event.num == 5:
            self.canvas.yview_scroll(3, "units")
        else:
            self.canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")

    # --- 翻译 ---
    def _do_ai_chat(self):
        user_input = self.entry_var.get().strip()
        if not user_input:
            return

        settings = self.data.get("settings", {})
        api_key = settings.get("api_key", "")
        base_url = settings.get("base_url", "https://api.xiaomimimo.com/v1")
        model = settings.get("model", "mimo-v2-flash")
        translate = settings.get("translate", "中英")

        if not api_key:
            messagebox.showwarning("提示", "请先在设置中填写 API Key")
            return

        # 构建翻译 prompt
        if translate == "自动检测":
            system_prompt = "🔴 你是中英互译员，无论收到的内容是什么，都会准确并且口语化的翻译出结果，并且只会回答翻译结果，回答只输出翻译结果，注意是互译"
        else:
            system_prompt = (
                f"🔴 你是{translate}互译员，无论收到的内容是什么，都会准确并且口语化的翻译出结果，并且只会回答翻译结果，回答只输出翻译结果，注意是互译"
            )

        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_input},
        ]

        try:
            client = OpenAI(api_key=api_key, base_url=base_url)
            completion = client.chat.completions.create(
                model=model,
                messages=messages,
                max_completion_tokens=1024,
                temperature=0.3,
                top_p=0.95,
                stream=False,
                stop=None,
                frequency_penalty=0,
                presence_penalty=0,
                extra_body={"thinking": {"type": "disabled"}},
            )
            ai_reply = completion.choices[0].message.content
            pyperclip.copy(ai_reply)
            # 清空输入框并显示短暂提示
            self.entry_var.set("")
            self._show_toast(f"✅ 已复制到剪贴板：{ai_reply}")
        except Exception as e:
            messagebox.showerror("AI 错误", f"调用AI接口出错:\n{e}")

    def _show_toast(self, msg, duration=1500):
        toast = tk.Toplevel(self.root)
        toast.overrideredirect(True)
        toast.attributes("-topmost", True)
        toast.configure(bg="#4a90d9")
        lbl = tk.Label(toast, text=msg, font=("Microsoft YaHei", 11),
                       bg="#4a90d9", fg="white", padx=16, pady=8)
        lbl.pack()
        # 居中显示在屏幕顶部
        toast.update_idletasks()
        tw = toast.winfo_width()
        sw = self.root.winfo_screenwidth()
        toast.geometry(f"+{(sw - tw) // 2}+80")
        toast.after(duration, toast.destroy)

    # --- 列表 ---
    def _rebuild_buttons(self):
        for w in self.btn_frame.winfo_children():
            w.destroy()

        # 第一个按钮：AI对话
        tk.Button(
            self.btn_frame, text="AI 翻译", font=("Microsoft YaHei", 11),
            bg="#4a90d9", fg="white", activebackground="#357abd",
            relief="flat", bd=0, cursor="hand2",
            command=self._do_ai_chat,
        ).pack(fill="x", padx=4, pady=4, ipady=8)

        # 用户自定义按钮
        for idx, item in enumerate(self.buttons_data):
            self._make_user_button(idx, item)

        # 最后一个按钮：添加
        tk.Button(
            self.btn_frame, text="＋ 添加按钮", font=("Microsoft YaHei", 11),
            bg="#3c3c3c", fg="#aaa", activebackground="#505050",
            relief="flat", bd=0, cursor="hand2",
            command=self._add_button_dialog,
        ).pack(fill="x", padx=4, pady=(8, 4), ipady=8)

        # 设置按钮
        tk.Button(
            self.btn_frame, text="⚙ 设置", font=("Microsoft YaHei", 11),
            bg="#333", fg="#999", activebackground="#505050",
            relief="flat", bd=0, cursor="hand2",
            command=self._settings_dialog,
        ).pack(fill="x", padx=4, pady=(4, 4), ipady=8)

    def _make_user_button(self, idx, item):
        texts = item.get("texts", [])
        files = item.get("files", [])
        text_count = len(texts)
        file_count = len(files)
        parts = []
        if text_count:
            parts.append(f"{text_count}条文本")
        if file_count:
            parts.append(f"{file_count}个文件")
        count_label = f"  ({', '.join(parts)})" if (text_count + file_count) > 1 else ""
        display_text = item["label"] + count_label

        btn = tk.Button(
            self.btn_frame, text=display_text, font=("Microsoft YaHei", 11),
            bg="#3c3c3c", fg="#e0e0e0", activebackground="#505050",
            relief="flat", bd=0, cursor="hand2", anchor="w",
        )
        btn.pack(fill="x", padx=4, pady=3, ipady=8)

        state = {
            "press_id": None,
            "dragging": False,
            "start_x": 0,
            "start_y": 0,
        }

        def press(event):
            state["dragging"] = False
            state["start_x"] = event.x_root
            state["start_y"] = event.y_root
            state["press_id"] = btn.after(
                self.LONG_PRESS_MS,
                lambda: self._on_long_press(idx, state, btn),
            )

        def motion(event):
            if state["press_id"] is None and not state["dragging"]:
                return
            dx = abs(event.x_root - state["start_x"])
            dy = abs(event.y_root - state["start_y"])
            if not state["dragging"] and (dx > 5 or dy > 5):
                state["dragging"] = True
                self._drag_active = True
                if state["press_id"]:
                    btn.after_cancel(state["press_id"])
                    state["press_id"] = None
                btn.configure(bg="#5a5a5a")

        def release(event):
            if state["press_id"]:
                btn.after_cancel(state["press_id"])
                state["press_id"] = None

            if state["dragging"]:
                self._drag_active = False
                btn.configure(bg="#3c3c3c")
                state["dragging"] = False

                # 判断鼠标是否仍在侧边栏范围内
                x, y = event.x_root, event.y_root
                gx = self.root.winfo_rootx()
                gy = self.root.winfo_rooty()
                gw = self.root.winfo_width()
                gh = self.root.winfo_height()
                inside = gx <= x <= gx + gw and gy <= y <= gy + gh

                if inside:
                    target = self._hit_test_user_button(event.y_root, idx)
                    if target is not None:
                        self._reorder_button(idx, target)
                        return

                # 鼠标在侧边栏外（或栏内无命中目标）→ 粘贴
                chosen = self._pick_random_text(texts)
                self._click_and_paste(event.x_root, event.y_root, chosen, files)

                # 拖放结束后如果鼠标在栏外，收起侧边栏
                if not inside:
                    self._set_collapsed()
            else:
                chosen = self._pick_random_text(texts)
                self._do_user_print(chosen, files)

        btn.bind("<ButtonPress-1>", press)
        btn.bind("<B1-Motion>", motion)
        btn.bind("<ButtonRelease-1>", release)

    def _pick_random_text(self, texts):
        """从多条内容中随机选一条"""
        if not texts:
            return ""
        if len(texts) == 1:
            return texts[0]
        chosen = random.choice(texts)
        print(f"[随机] 共{len(texts)}条，选中: {chosen[:50]}...")
        return chosen

    def _hit_test_user_button(self, screen_y, exclude_idx):
        user_btns = []
        for child in self.btn_frame.winfo_children():
            try:
                if child.cget("bg") == "#3c3c3c" and child.cget("anchor") == "w":
                    user_btns.append(child)
            except tk.TclError:
                continue

        for i, w in enumerate(user_btns):
            wy = w.winfo_rooty()
            wh = w.winfo_height()
            if wy <= screen_y <= wy + wh:
                mid = wy + wh / 2
                insert_idx = i if screen_y < mid else i + 1
                if insert_idx == exclude_idx or insert_idx == exclude_idx + 1:
                    return None
                return insert_idx
        return None

    def _reorder_button(self, from_idx, to_idx):
        item = self.buttons_data.pop(from_idx)
        if to_idx > from_idx:
            to_idx -= 1
        self.buttons_data.insert(to_idx, item)
        self.data["buttons"] = self.buttons_data
        save_data(self.data)
        self._rebuild_buttons()
        print(f"[排序] 「{item['label']}」: {from_idx} → {to_idx}")

    def _on_long_press(self, idx, state, btn):
        state["press_id"] = None
        self._confirm_delete(idx)

    def _do_user_print(self, text, files=None):
        """粘贴文字（可选）+ 文件（可选）。files 为文件路径列表（无前缀）。"""
        if text:
            pyperclip.copy(text)
            pyautogui.hotkey("ctrl", "v")
            print(f"[粘贴文字] {text}")
        if files:
            if text:
                time.sleep(0.1)
            self._paste_files(files)
            print(f"[粘贴文件] {len(files)}个文件")

    def _paste_files(self, paths):
        """
        将文件路径以 CF_HDROP 格式放入剪贴板，
        然后发送 Ctrl+V，效果等同于从文件管理器拖拽文件到目标窗口。
        paths: 纯文件路径列表（无前缀）
        """
        try:
            import win32clipboard
            import ctypes
            from ctypes import wintypes

            # CF_HDROP = 15
            # DROPFILES 结构: DWORD pFiles, POINT pt, BOOL fWide, BYTE[...] szFileList
            files_str = "\0".join(paths) + "\0\0"

            class DROPFILES(ctypes.Structure):
                _fields_ = [
                    ("pFiles", wintypes.DWORD),
                    ("pt", wintypes.POINT),
                    ("fNC", wintypes.BOOL),
                    ("fWide", wintypes.BOOL),
                ]

            drop = DROPFILES()
            drop.pFiles = ctypes.sizeof(DROPFILES)
            drop.pt.x = 0
            drop.pt.y = 0
            drop.fNC = False
            drop.fWide = True

            data_bytes = bytes(drop) + files_str.encode("utf-16-le")

            win32clipboard.OpenClipboard()
            try:
                win32clipboard.EmptyClipboard()
                win32clipboard.SetClipboardData(15, data_bytes)  # 15 = CF_HDROP
            finally:
                win32clipboard.CloseClipboard()

            time.sleep(0.05)
            pyautogui.hotkey("ctrl", "v")
        except ImportError:
            pyperclip.copy("\n".join(paths))
            pyautogui.hotkey("ctrl", "v")
            self._show_toast("⚠ 已复制文件路径，请手动粘贴文件，Windows可能需要自行安装 pywin32 库")
        except Exception as e:
            print(f"[文件粘贴失败] {e}")
            self._show_toast(f"⚠ 文件粘贴失败: {e}")

    def _click_and_paste(self, x, y, text, files=None):
        try:
            pyautogui.click(x, y)
            time.sleep(0.05)
            self._do_user_print(text, files)
            label = text if text else f"{len(files)}个文件"
            print(f"[拖动粘贴] 「{label}」→ ({x}, {y})")
        except Exception as e:
            print(f"[拖动粘贴失败] {e}")

    # --- 同名索引 ---
    def _find_existing_button(self, label):
        """返回第一个 label 匹配的按钮索引，没有返回 -1"""
        for i, item in enumerate(self.buttons_data):
            if item["label"] == label:
                return i
        return -1

    # --- 添加自定义按钮 ---
    def _add_button_dialog(self):
        dlg = tk.Toplevel(self.root)
        dlg.title("添加按钮")
        dlg.geometry("420x560")
        dlg.configure(bg="#2b2b2b")
        dlg.attributes("-topmost", True)
        dlg.resizable(False, False)
        dlg.grab_set()

        # 拖入的文件路径（纯路径，无前缀）
        dropped_files = []

        # ── 顶部确认/取消按钮 ──
        bf = tk.Frame(dlg, bg="#2b2b2b")
        bf.pack(pady=(10, 4), anchor="n")

        tk.Label(dlg, text="按钮显示文字：", bg="#2b2b2b", fg="#ccc",
                 font=("Microsoft YaHei", 10)).pack(anchor="w", padx=20, pady=(8, 2))
        var_label = tk.StringVar()
        e1 = tk.Entry(dlg, textvariable=var_label, font=("Microsoft YaHei", 11),
                      bg="#3c3c3c", fg="#e0e0e0", insertbackground="#e0e0e0",
                      relief="flat", bd=0)
        e1.pack(fill="x", padx=20, ipady=4, pady=(0, 8))

        tk.Label(dlg, text="添加文本内容（每条可包含换行，随机选一条粘贴）：",
                 bg="#2b2b2b", fg="#ccc",
                 font=("Microsoft YaHei", 10)).pack(anchor="w", padx=20, pady=(4, 2))

        # ── 已添加的条目列表 ──
        entries_frame = tk.Frame(dlg, bg="#2b2b2b")
        entries_frame.pack(fill="both", padx=20, expand=True, pady=(0, 4))

        entries_canvas = tk.Canvas(entries_frame, bg="#2b2b2b", highlightthickness=0)
        entries_scrollbar = tk.Scrollbar(entries_frame, orient="vertical",
                                         command=entries_canvas.yview)
        entries_inner = tk.Frame(entries_canvas, bg="#2b2b2b")
        entries_inner.bind("<Configure>",
                           lambda e: entries_canvas.configure(
                               scrollregion=entries_canvas.bbox("all")))
        entries_canvas.create_window((0, 0), window=entries_inner, anchor="nw",
                                     tags="entries_win")
        entries_canvas.configure(yscrollcommand=entries_scrollbar.set)
        entries_canvas.bind("<Configure>",
                            lambda e: entries_canvas.itemconfigure(
                                "entries_win", width=e.width))
        entries_canvas.pack(side="left", fill="both", expand=True)
        entries_scrollbar.pack(side="right", fill="y")

        # 条目数据
        text_entries = []

        def _refresh_entries():
            for w in entries_inner.winfo_children():
                w.destroy()
            for i, txt in enumerate(text_entries):
                preview = txt.replace("\n", " ↵ ") if "\n" in txt else txt
                if len(preview) > 50:
                    preview = preview[:50] + "…"
                row = tk.Frame(entries_inner, bg="#3c3c3c")
                row.pack(fill="x", pady=1)
                tk.Label(row, text=f"{i + 1}. {preview}",
                         font=("Microsoft YaHei", 9), bg="#3c3c3c",
                         fg="#e0e0e0", anchor="w", padx=4).pack(
                    side="left", fill="x", expand=True)
                tk.Label(row, text="✕", font=("Arial", 9), bg="#3c3c3c",
                         fg="#f66", cursor="hand2", padx=6).pack(side="right")
                row.winfo_children()[-1].bind(
                    "<Button-1>", lambda e, idx=i: (text_entries.pop(idx),
                                                    _refresh_entries()))
                for child in row.winfo_children():
                    child.bind("<Enter>", lambda e, r=row: r.configure(bg="#4a4a4a"))
                    child.bind("<Leave>", lambda e, r=row: r.configure(bg="#3c3c3c"))

        # ── 输入区 + 添加按钮 ──
        input_area = tk.Frame(dlg, bg="#2b2b2b")
        input_area.pack(fill="x", padx=20, pady=(0, 4))

        txt_widget = tk.Text(input_area, font=("Microsoft YaHei", 11),
                             bg="#3c3c3c", fg="#e0e0e0", insertbackground="#e0e0e0",
                             relief="flat", bd=0, wrap="word", height=3)
        txt_widget.pack(fill="x", padx=0, pady=(0, 4))

        def _add_entry():
            raw = txt_widget.get("1.0", "end").rstrip("\n")
            if not raw.strip():
                return
            text_entries.append(raw)
            txt_widget.delete("1.0", "end")
            _refresh_entries()

        tk.Button(input_area, text="＋ 添加此条(只有添加了的文本才会触发)", font=("Microsoft YaHei", 10),
                  bg="#3c3c3c", fg="#aaa", activebackground="#505050",
                  relief="flat", bd=0, cursor="hand2",
                  command=_add_entry).pack(fill="x", ipady=4)

        # 文件拖入区域
        file_drop_frame = tk.Frame(dlg, bg="#333", bd=2, relief="groove",
                                   cursor="hand2")
        file_drop_frame.pack(fill="x", padx=20, pady=(0, 4))

        file_drop_label = tk.Label(
            file_drop_frame,
            text="📎 拖入文件到此处（多个文件 = 一条记录，触发时全部文件一起随其一文字粘贴）",
            font=("Microsoft YaHei", 9), bg="#333", fg="#888",
            pady=8,
        )
        file_drop_label.pack(fill="x")

        # 文件列表显示区
        file_list_frame = tk.Frame(dlg, bg="#2b2b2b")
        file_list_frame.pack(fill="x", padx=20, pady=(0, 4))

        def _refresh_file_list():
            """刷新文件列表显示"""
            for w in file_list_frame.winfo_children():
                w.destroy()
            for i, fp in enumerate(dropped_files):
                name = fp.split("\\")[-1].split("/")[-1]
                row = tk.Frame(file_list_frame, bg="#3c3c3c")
                row.pack(fill="x", pady=1)
                tk.Label(row, text=f"📄 {name}", font=("Microsoft YaHei", 9),
                         bg="#3c3c3c", fg="#e0e0e0", anchor="w", padx=4).pack(
                    side="left", fill="x", expand=True)
                tk.Label(row, text="✕", font=("Arial", 9), bg="#3c3c3c",
                         fg="#f66", cursor="hand2", padx=6).pack(side="right")
                row.winfo_children()[-1].bind(
                    "<Button-1>", lambda e, idx=i: (_drop_files_remove(idx),))
                for child in row.winfo_children():
                    child.bind("<Enter>", lambda e, r=row: r.configure(bg="#4a4a4a"))
                    child.bind("<Leave>", lambda e, r=row: r.configure(bg="#3c3c3c"))

        def _drop_files_remove(idx):
            dropped_files.pop(idx)
            _refresh_file_list()

        def _on_files_dropped(files):
            """windnd 回调：文件拖入"""
            for f in files:
                if isinstance(f, bytes):
                    f = f.decode("gbk", errors="replace")
                dropped_files.append(f)
            _refresh_file_list()
            count = len(dropped_files)
            file_drop_label.configure(
                text=f"📎 已拖入 {count} 个文件（继续拖入可追加）")

        # 绑定 windnd 拖拽
        windnd.hook_dropfiles(dlg, func=_on_files_dropped)

        # 点击文件区域也可以选择文件
        def _click_add_files(event=None):
            paths = filedialog.askopenfilenames(title="选择文件", parent=dlg)
            if paths:
                dropped_files.extend(paths)
                _refresh_file_list()
                count = len(dropped_files)
                file_drop_label.configure(
                    text=f"📎 已选择 {count} 个文件（可继续拖入）")

        file_drop_frame.bind("<Button-1>", _click_add_files)
        file_drop_label.bind("<Button-1>", _click_add_files)

        # 提示标签
        hint_lbl = tk.Label(dlg, text="", bg="#2b2b2b", fg="#f0a030",
                            font=("Microsoft YaHei", 9), wraplength=380)
        hint_lbl.pack(anchor="w", padx=20, pady=(0, 4))

        def _check_duplicate(*_):
            lbl = var_label.get().strip()
            idx = self._find_existing_button(lbl)
            if idx >= 0:
                existing = self.buttons_data[idx]
                tc = len(existing.get("texts", []))
                fc = len(existing.get("files", []))
                hint_lbl.configure(
                    text=f"⚠ 已存在「{lbl}」（{tc}条文本, {fc}个文件），"
                         f"确认时可选择合并或单独创建")
            else:
                hint_lbl.configure(text="")

        var_label.trace_add("write", _check_duplicate)

        def confirm():
            lbl = var_label.get().strip()
            texts = list(text_entries)
            files = list(dropped_files)
            if not lbl:
                messagebox.showwarning("提示", "按钮显示文字不能为空", parent=dlg)
                return
            if not texts and not files:
                messagebox.showwarning("提示", "至少输入一条文本或拖入一个文件", parent=dlg)
                return

            existing_idx = self._find_existing_button(lbl)
            if existing_idx >= 0:
                total = len(texts) + len(files)
                choice = self._ask_merge_or_create(lbl, total, dlg)
                if choice == "merge":
                    self.buttons_data[existing_idx]["texts"].extend(texts)
                    self.buttons_data[existing_idx]["files"].extend(files)
                    self.data["buttons"] = self.buttons_data
                    save_data(self.data)
                    self._rebuild_buttons()
                    tc = len(self.buttons_data[existing_idx]["texts"])
                    fc = len(self.buttons_data[existing_idx]["files"])
                    self._show_toast(f"✅ 已合并到「{lbl}」（{tc}条文本, {fc}个文件）")
                    dlg.destroy()
                elif choice == "create":
                    self.buttons_data.append({"label": lbl, "texts": texts, "files": files})
                    self.data["buttons"] = self.buttons_data
                    save_data(self.data)
                    self._rebuild_buttons()
                    dlg.destroy()
            else:
                self.buttons_data.append({"label": lbl, "texts": texts, "files": files})
                self.data["buttons"] = self.buttons_data
                save_data(self.data)
                self._rebuild_buttons()
                dlg.destroy()

        tk.Button(bf, text="确认", font=("Microsoft YaHei", 10),
                  bg="#4a90d9", fg="white", relief="flat", width=8,
                  command=confirm).pack(side="left", padx=8)
        tk.Button(bf, text="取消", font=("Microsoft YaHei", 10),
                  bg="#555", fg="#ccc", relief="flat", width=8,
                  command=dlg.destroy).pack(side="left", padx=8)

        e1.focus_set()

    def _ask_merge_or_create(self, label, new_count, parent):
        
        existing_idx = self._find_existing_button(label)
        existing_texts = self.buttons_data[existing_idx].get("texts", [])
        existing_files = self.buttons_data[existing_idx].get("files", [])
        existing_count = len(existing_texts) + len(existing_files)

        result = {"choice": None}

        dlg = tk.Toplevel(parent or self.root)
        dlg.title("按钮名称冲突")
        dlg.geometry("400x260")
        dlg.configure(bg="#2b2b2b")
        dlg.attributes("-topmost", True)
        dlg.resizable(False, False)
        dlg.grab_set()

        tk.Label(dlg, text=f"⚠ 已存在名为「{label}」的按钮",
                 font=("Microsoft YaHei", 12, "bold"),
                 bg="#2b2b2b", fg="#f0a030").pack(pady=(20, 6))

        tk.Label(dlg, text=f"已有 {existing_count} 条内容，新增 {new_count} 条",
                 font=("Microsoft YaHei", 10),
                 bg="#2b2b2b", fg="#ccc").pack(pady=(0, 14))

        tk.Label(dlg, text="请选择操作：",
                 font=("Microsoft YaHei", 10),
                 bg="#2b2b2b", fg="#aaa").pack(pady=(0, 10))

        btn_frame = tk.Frame(dlg, bg="#2b2b2b")
        btn_frame.pack(pady=4)

        def do_merge():
            result["choice"] = "merge"
            dlg.destroy()

        def do_create():
            result["choice"] = "create"
            dlg.destroy()

        def do_cancel():
            result["choice"] = None
            dlg.destroy()

        tk.Button(btn_frame, text="🔗 合并到已有按钮",
                  font=("Microsoft YaHei", 10),
                  bg="#2e7d32", fg="white", relief="flat", width=18,
                  command=do_merge).pack(side="left", padx=6, ipady=4)
        tk.Button(btn_frame, text="📌 单独创建新按钮",
                  font=("Microsoft YaHei", 10),
                  bg="#4a90d9", fg="white", relief="flat", width=18,
                  command=do_create).pack(side="left", padx=6, ipady=4)

        tk.Button(dlg, text="取消", font=("Microsoft YaHei", 10),
                  bg="#555", fg="#ccc", relief="flat", width=12,
                  command=do_cancel).pack(pady=(14, 10))

        dlg.wait_window()
        return result["choice"]

    # --- 删除 ---
    def _confirm_delete(self, idx):
        item = self.buttons_data[idx]
        texts = item.get("texts", [])
        files = item.get("files", [])
        parts = []
        if texts:
            parts.append(f"{len(texts)}条文本")
        if files:
            parts.append(f"{len(files)}个文件")
        count_info = f"（{', '.join(parts)}）" if (len(texts) + len(files)) > 1 else ""
        if messagebox.askyesno("删除按钮",
                               f"确定删除「{item['label']}」{count_info}？",
                               parent=self.root):
            self.buttons_data.pop(idx)
            self.data["buttons"] = self.buttons_data
            save_data(self.data)
            self._rebuild_buttons()

    # --- 设置 ---
    def _settings_dialog(self):
        dlg = tk.Toplevel(self.root)
        dlg.title("设置")
        dlg.geometry("380x420")
        dlg.configure(bg="#2b2b2b")
        dlg.attributes("-topmost", True)
        dlg.resizable(False, False)
        dlg.grab_set()

        settings = self.data.get("settings", {})

        row_y = 12

        # --- API Key ---
        tk.Label(dlg, text="API Key：", bg="#2b2b2b", fg="#ccc",
                 font=("Microsoft YaHei", 10)).place(x=20, y=row_y)
        row_y += 22
        var_api_key = tk.StringVar(value=settings.get("api_key", ""))
        ent_api_key = tk.Entry(dlg, textvariable=var_api_key,
                               font=("Microsoft YaHei", 11), show="*",
                               bg="#3c3c3c", fg="#e0e0e0", insertbackground="#e0e0e0",
                               relief="flat", bd=0)
        ent_api_key.place(x=20, y=row_y, width=340, height=28)
        row_y += 36

        # --- Base URL ---
        tk.Label(dlg, text="Base URL：", bg="#2b2b2b", fg="#ccc",
                 font=("Microsoft YaHei", 10)).place(x=20, y=row_y)
        row_y += 22
        var_base_url = tk.StringVar(value=settings.get("base_url", "https://api.xiaomimimo.com/v1"))
        ent_base_url = tk.Entry(dlg, textvariable=var_base_url,
                                font=("Microsoft YaHei", 11),
                                bg="#3c3c3c", fg="#e0e0e0", insertbackground="#e0e0e0",
                                relief="flat", bd=0)
        ent_base_url.place(x=20, y=row_y, width=340, height=28)
        row_y += 36

        # --- Model ---
        tk.Label(dlg, text="Model：", bg="#2b2b2b", fg="#ccc",
                 font=("Microsoft YaHei", 10)).place(x=20, y=row_y)
        row_y += 22
        var_model = tk.StringVar(value=settings.get("model", "mimo-v2-flash"))
        ent_model = tk.Entry(dlg, textvariable=var_model,
                             font=("Microsoft YaHei", 11),
                             bg="#3c3c3c", fg="#e0e0e0", insertbackground="#e0e0e0",
                             relief="flat", bd=0)
        ent_model.place(x=20, y=row_y, width=340, height=28)
        row_y += 36

        # --- Translate (可展开选项列表) ---
        tk.Label(dlg, text="翻译方向：", bg="#2b2b2b", fg="#ccc",
                 font=("Microsoft YaHei", 10)).place(x=20, y=row_y)
        row_y += 22

        var_translate = tk.StringVar(value=settings.get("translate", "中英"))

        translate_btn = tk.Label(dlg, text="", font=("Microsoft YaHei", 11),
                                 bg="#3c3c3c", fg="#e0e0e0", anchor="w", padx=8,
                                 cursor="hand2", relief="flat")
        translate_btn.place(x=20, y=row_y, width=310, height=28)
        arrow_lbl = tk.Label(dlg, text="▼", font=("Microsoft YaHei", 9),
                             bg="#3c3c3c", fg="#aaa", cursor="hand2")
        arrow_lbl.place(x=330, y=row_y, width=30, height=28)

        def _update_translate_label():
            translate_btn.configure(text=var_translate.get())

        _update_translate_label()

        def _open_translate_dropdown(_=None):
            self._dropdown_open = True
            dd = tk.Toplevel(dlg)
            dd.overrideredirect(True)
            dd.attributes("-topmost", True)
            dd.configure(bg="#1e1e1e")

            bx = dlg.winfo_rootx() + 20
            by = dlg.winfo_rooty() + row_y + 28
            dd.geometry(f"340x200+{bx}+{by}")

            dd_canvas = tk.Canvas(dd, bg="#1e1e1e", highlightthickness=0)
            dd_scroll = tk.Scrollbar(dd, orient="vertical", command=dd_canvas.yview)
            dd_inner = tk.Frame(dd_canvas, bg="#1e1e1e")
            dd_inner.bind("<Configure>",
                          lambda e: dd_canvas.configure(scrollregion=dd_canvas.bbox("all")))
            dd_canvas.create_window((0, 0), window=dd_inner, anchor="nw", tags="inner")
            dd_canvas.configure(yscrollcommand=dd_scroll.set)
            dd_canvas.pack(side="left", fill="both", expand=True)
            dd_scroll.pack(side="right", fill="y")

            def _dd_wheel(event):
                if event.num == 4:
                    dd_canvas.yview_scroll(-3, "units")
                elif event.num == 5:
                    dd_canvas.yview_scroll(3, "units")
                else:
                    dd_canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")
                return "break"

            dd.bind("<MouseWheel>", _dd_wheel)
            dd.bind("<Button-4>", _dd_wheel)
            dd.bind("<Button-5>", _dd_wheel)
            dd_canvas.bind("<MouseWheel>", _dd_wheel)
            dd_canvas.bind("<Button-4>", _dd_wheel)
            dd_canvas.bind("<Button-5>", _dd_wheel)
            dd_inner.bind("<MouseWheel>", _dd_wheel)
            dd_inner.bind("<Button-4>", _dd_wheel)
            dd_inner.bind("<Button-5>", _dd_wheel)

            for opt in TRANSLATE_OPTIONS:
                lbl = tk.Label(dd_inner, text=opt, font=("Microsoft YaHei", 10),
                               bg="#1e1e1e", fg="#ccc", anchor="w", padx=12, pady=4,
                               cursor="hand2")
                lbl.pack(fill="x")
                lbl.bind("<Button-1>", lambda e, o=opt: (_update_translate_label_var(o), setattr(self, '_dropdown_open', False), dd.destroy()))
                lbl.bind("<Enter>", lambda e, l=lbl: l.configure(bg="#3a3a3a"))
                lbl.bind("<Leave>", lambda e, l=lbl: l.configure(bg="#1e1e1e"))

            def _close_dd(event=None):
                if event and dd.winfo_exists():
                    ex, ey = event.x_root, event.y_root
                    dx, dy = dd.winfo_rootx(), dd.winfo_rooty()
                    dw, dh = dd.winfo_width(), dd.winfo_height()
                    if not (dx <= ex <= dx + dw and dy <= ey <= dy + dh):
                        self._dropdown_open = False
                        dd.destroy()
                else:
                    self._dropdown_open = False
                    dd.destroy()

            dd.bind("<FocusOut>", _close_dd)
            dd.focus_set()

        def _update_translate_label_var(val):
            var_translate.set(val)
            _update_translate_label()

        translate_btn.bind("<Button-1>", _open_translate_dropdown)
        arrow_lbl.bind("<Button-1>", _open_translate_dropdown)

        row_y += 36

        # --- 保存按钮 ---
        def save_settings():
            self.data["settings"] = {
                "api_key": var_api_key.get().strip(),
                "base_url": var_base_url.get().strip(),
                "model": var_model.get().strip(),
                "translate": var_translate.get(),
            }
            save_data(self.data)
            dlg.destroy()

        btn_y = row_y + 10
        tk.Button(dlg, text="💾 保存", font=("Microsoft YaHei", 10),
                  bg="#4a90d9", fg="white", relief="flat", width=12,
                  command=save_settings).place(x=20, y=btn_y, width=160, height=32)

        # --- 置左/置右 ---
        def move_left():
            self.data["side"] = "left"
            save_data(self.data)
            self._reposition("left")
            dlg.destroy()

        def move_right():
            self.data["side"] = "right"
            save_data(self.data)
            self._reposition("right")
            dlg.destroy()

        tk.Button(dlg, text="◀ 置左", font=("Microsoft YaHei", 10),
                  bg="#555", fg="#ccc", relief="flat", width=8,
                  command=move_left).place(x=200, y=btn_y, width=76, height=32)
        tk.Button(dlg, text="置右 ▶", font=("Microsoft YaHei", 10),
                  bg="#555", fg="#ccc", relief="flat", width=8,
                  command=move_right).place(x=284, y=btn_y, width=76, height=32)

        # --- 导出/导入配置 ---
        io_y = btn_y + 46
        tk.Button(dlg, text="📤 导出配置", font=("Microsoft YaHei", 10),
                  bg="#2e7d32", fg="white", relief="flat", width=14,
                  command=self._export_config).place(x=20, y=io_y, width=160, height=32)
        tk.Button(dlg, text="📥 导入配置", font=("Microsoft YaHei", 10),
                  bg="#6a1b9a", fg="white", relief="flat", width=14,
                  command=lambda: self._import_config(dlg)).place(x=200, y=io_y, width=160, height=32)

        ent_api_key.focus_set()
        ent_api_key.icursor("end")

    # --- 导出配置 ---
    def _export_config(self):
        path = filedialog.asksaveasfilename(
            title="导出配置",
            defaultextension=".json",
            filetypes=[("JSON 文件", "*.json"), ("所有文件", "*.*")],
            initialfile="sidebar_config.json",
            parent=self.root,
        )
        if not path:
            return
        try:
            export_data = {
                "buttons": self.data.get("buttons", []),
                "settings": self.data.get("settings", {}),
                "side": self.data.get("side", "right"),
            }
            Path(path).write_text(json.dumps(export_data, ensure_ascii=False, indent=2), "utf-8")
            messagebox.showinfo("导出成功", f"配置已保存到:\n{path}", parent=self.root)
        except Exception as e:
            messagebox.showerror("导出失败", str(e), parent=self.root)

    # --- 导入配置 ---
    def _import_config(self, parent_dlg=None):
        path = filedialog.askopenfilename(
            title="导入配置",
            filetypes=[("JSON 文件", "*.json"), ("所有文件", "*.*")],
            parent=parent_dlg or self.root,
        )
        if not path:
            return
        try:
            imported = json.loads(Path(path).read_text("utf-8"))
            if not isinstance(imported, dict):
                raise ValueError("配置文件格式错误")

            # 验证必要字段
            if "buttons" not in imported and "settings" not in imported:
                raise ValueError("缺少 buttons 或 settings 字段")

            # 合并配置
            if "buttons" in imported:
                self.data["buttons"] = list(imported["buttons"])
                self.buttons_data = self.data["buttons"]
            if "settings" in imported:
                for key in ("api_key", "base_url", "model", "translate"):
                    if key in imported["settings"]:
                        self.data["settings"][key] = imported["settings"][key]
            if "side" in imported:
                self.data["side"] = imported["side"]
                self.side = imported["side"]

            save_data(self.data)
            self._rebuild_buttons()
            self._reposition(self.side)
            messagebox.showinfo("导入成功", "配置已加载！", parent=parent_dlg or self.root)
        except Exception as e:
            messagebox.showerror("导入失败", str(e), parent=parent_dlg or self.root)

    def _reposition(self, side):
        self.side = side
        for child in self.hint.winfo_children():
            child.configure(text="◀" if side == "right" else "▶")
        if self.expanded:
            self._set_expanded()
        else:
            self._set_collapsed()

    # --- 系统托盘 ---
    def _create_tray_image(self):
        """生成一个简单的侧边栏托盘图标"""
        img = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
        draw = ImageDraw.Draw(img)
        # 侧边栏图标
        draw.rounded_rectangle([8, 8, 56, 56], radius=8, fill="#4a90d9")
        draw.rectangle([16, 16, 28, 48], fill="white")
        draw.rectangle([32, 16, 48, 28], fill="white")
        draw.rectangle([32, 36, 48, 48], fill="white")
        return img

    def _setup_tray(self):
        if self._tray_running:
            return
        self._tray_running = True

        image = self._create_tray_image()
        menu = Menu(
            MenuItem("显示面板", self._tray_show, default=True),
            MenuItem("隐藏面板", self._tray_hide),
            Menu.SEPARATOR,
            MenuItem("退出", self._tray_quit),
        )
        self._tray_icon = Icon("Sidebar", image, "侧边抽屉", menu)

        def _run_tray():
            self._tray_icon.run()

        self._tray_thread = threading.Thread(target=_run_tray, daemon=True)
        self._tray_thread.start()

    def _tray_show(self, icon=None, item=None):
        """从托盘恢复窗口"""
        self.root.after(0, self._restore_from_tray)

    def _restore_from_tray(self):
        self.root.deiconify()   # 恢复窗口
        self.root.lift()
        self.root.attributes("-topmost", True)
        self._set_collapsed()

    def _tray_hide(self, icon=None, item=None):
        """隐藏窗口到托盘"""
        self.root.after(0, self._minimize_to_tray)

    def _minimize_to_tray(self):
        """最小化到系统托盘"""
        self.root.withdraw()  # 隐藏窗口

    def _tray_quit(self, icon=None, item=None):
        """从托盘退出程序"""
        self._tray_running = False
        if self._tray_icon:
            self._tray_icon.stop()
        self.root.after(0, self._force_quit)

    def _force_quit(self):
        if self._tray_icon:
            try:
                self._tray_icon.stop()
            except Exception:
                pass
        self.root.destroy()

    def run(self):
        self._setup_tray()
        self.root.mainloop()


if __name__ == "__main__":
    if not _acquire_single_instance():
        sys.exit(0)
    Sidebar(side="right").run()
