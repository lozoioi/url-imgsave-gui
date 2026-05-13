#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
图片下载器 GUI — 基于 customtkinter
从网页批量抓取并下载图片，支持多线程、预览、筛选、历史记录。
"""

import os
import json
import subprocess
import threading
import time
from pathlib import Path

import customtkinter as ctk
from PIL import Image, UnidentifiedImageError
from tkinter import filedialog, messagebox

from core import get_image_urls, get_page_title, DownloadManager, filter_by_type
from core.utils import ALLOWED_EXTENSIONS, scan_directory_hashes, safe_filename

# --- 常量 ---
BASE_DIR = Path(__file__).parent
SETTINGS_FILE = BASE_DIR / "settings.json"
HISTORY_FILE = BASE_DIR / "history.json"
DEFAULT_SAVE_DIR = str(BASE_DIR / "downloaded_images")

ctk.set_appearance_mode("system")  # system / light / dark
ctk.set_default_color_theme("blue")


def load_json(path, default):
    if path.exists():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            pass
    return default


def save_json(path, data):
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


# ============================================================
#  主应用
# ============================================================

class App(ctk.CTk):
    def __init__(self):
        super().__init__()
        self.title("图片下载器")
        self.geometry("1100x750")
        self.minsize(900, 600)

        # 加载设置 & 历史
        self.settings = load_json(SETTINGS_FILE, {})
        self.history = load_json(HISTORY_FILE, [])

        # 下载管理器（单例，全局复用）
        self.manager = DownloadManager(max_workers=self.settings.get("max_workers", 4))

        # 标签页容器
        self.tabview = ctk.CTkTabview(self)
        self.tabview.pack(fill="both", expand=True, padx=10, pady=10)

        self.tab_download = self.tabview.add("下载")
        self.tab_history = self.tabview.add("历史记录")
        self.tab_settings = self.tabview.add("设置")

        self.download_tab = DownloadTab(self.tab_download, self)
        self.history_tab = HistoryTab(self.tab_history, self)
        self.settings_tab = SettingsTab(self.tab_settings, self)

        self.protocol("WM_DELETE_WINDOW", self._on_close)

    def _on_close(self):
        self.manager.cancel()
        self.destroy()

    def log(self, msg):
        """线程安全地写日志到下载标签页"""
        self.after(0, lambda: self.download_tab._append_log(msg))

    def add_history(self, entry):
        self.history.append(entry)
        save_json(HISTORY_FILE, self.history)
        self.after(0, self.history_tab.refresh)


# ============================================================
#  下载标签页
# ============================================================

class DownloadTab:
    def __init__(self, parent, app: App):
        self.app = app
        self.preview_images = []  # 保持 CTkImage 引用防止 GC

        # --- 左栏：控制区 ---
        left = ctk.CTkFrame(parent)
        left.pack(side="left", fill="both", expand=True, padx=(0, 5))

        # URL 输入
        ctk.CTkLabel(left, text="网页 URL（每行一个，支持批量）:", anchor="w").pack(fill="x", pady=(5, 0))
        self.url_box = ctk.CTkTextbox(left, height=100, wrap="word")
        self.url_box.pack(fill="x", padx=5, pady=5)

        # 保存目录
        dir_frame = ctk.CTkFrame(left, fg_color="transparent")
        dir_frame.pack(fill="x", padx=5, pady=5)
        ctk.CTkLabel(dir_frame, text="保存目录:").pack(side="left")
        default_dir = self.app.settings.get("save_dir", DEFAULT_SAVE_DIR)
        self.dir_var = ctk.StringVar(value=default_dir)
        self.dir_entry = ctk.CTkEntry(dir_frame, textvariable=self.dir_var)
        self.dir_entry.pack(side="left", fill="x", expand=True, padx=5)
        ctk.CTkButton(dir_frame, text="浏览", width=60, command=self._browse_dir).pack(side="right")

        # 线程数 + 去重
        opt_frame = ctk.CTkFrame(left, fg_color="transparent")
        opt_frame.pack(fill="x", padx=5, pady=5)

        ctk.CTkLabel(opt_frame, text="线程数:").pack(side="left")
        self.thread_var = ctk.IntVar(value=self.app.settings.get("max_workers", 4))
        ctk.CTkSlider(opt_frame, from_=1, to=16, number_of_steps=15,
                      variable=self.thread_var, width=100).pack(side="left", padx=5)
        self.thread_label = ctk.CTkLabel(opt_frame, text=str(self.thread_var.get()), width=25)
        self.thread_label.pack(side="left")
        self.thread_var.trace_add("write", lambda *_: self.thread_label.configure(text=str(self.thread_var.get())))

        self.dedup_var = ctk.BooleanVar(value=self.app.settings.get("dedup", True))
        ctk.CTkCheckBox(opt_frame, text="MD5去重", variable=self.dedup_var).pack(side="left", padx=10)

        # 图片源属性
        src_frame = ctk.CTkFrame(left, fg_color="transparent")
        src_frame.pack(fill="x", padx=5, pady=5)
        ctk.CTkLabel(src_frame, text="图片源属性:").pack(side="left")
        self.src_attrs_default = {"data-src", "src", "data-original"}
        self.src_checks = {}
        for attr in self.src_attrs_default:
            v = ctk.BooleanVar(value=True)
            ctk.CTkCheckBox(src_frame, text=attr, variable=v, width=20).pack(side="left", padx=3)
            self.src_checks[attr] = v
        self.custom_attr_var = ctk.StringVar()
        ctk.CTkEntry(src_frame, textvariable=self.custom_attr_var, width=80,
                     placeholder_text="自定义").pack(side="left", padx=5)

        # 进度条
        self.progress = ctk.CTkProgressBar(left)
        self.progress.pack(fill="x", padx=5, pady=10)
        self.progress.set(0)
        self.progress_label = ctk.CTkLabel(left, text="就绪")
        self.progress_label.pack()

        # 按钮区
        btn_frame = ctk.CTkFrame(left, fg_color="transparent")
        btn_frame.pack(fill="x", padx=5, pady=5)
        self.btn_start = ctk.CTkButton(btn_frame, text="开始下载", command=self._start_download)
        self.btn_start.pack(side="left", padx=3)
        self.btn_pause = ctk.CTkButton(btn_frame, text="暂停", command=self._pause, state="disabled")
        self.btn_pause.pack(side="left", padx=3)
        self.btn_stop = ctk.CTkButton(btn_frame, text="停止", command=self._stop, fg_color="#c0392b",
                                       hover_color="#e74c3c", state="disabled")
        self.btn_stop.pack(side="left", padx=3)

        # 日志
        ctk.CTkLabel(left, text="下载日志:", anchor="w").pack(fill="x", padx=5, pady=(10, 0))
        self.log_box = ctk.CTkTextbox(left, height=120, wrap="word", state="disabled")
        self.log_box.pack(fill="both", expand=True, padx=5, pady=5)

        # --- 右栏：预览区 ---
        right = ctk.CTkFrame(parent)
        right.pack(side="right", fill="both", expand=True, padx=(5, 0))
        ctk.CTkLabel(right, text="图片预览:", anchor="w").pack(fill="x", padx=5, pady=5)
        self.preview_frame = ctk.CTkScrollableFrame(right)
        self.preview_frame.pack(fill="both", expand=True, padx=5, pady=5)

        self.preview_inner = ctk.CTkFrame(self.preview_frame, fg_color="transparent")
        self.preview_inner.pack(fill="both", expand=True)
        self.preview_cols = 3
        self.preview_row = 0
        self.preview_col = 0

    # ---- 操作方法 ----

    def _browse_dir(self):
        d = filedialog.askdirectory(title="选择保存目录")
        if d:
            self.dir_var.set(d)

    def _get_source_attrs(self):
        attrs = [a for a, v in self.src_checks.items() if v.get()]
        custom = self.custom_attr_var.get().strip()
        if custom:
            attrs.append(custom)
        return attrs if attrs else list(self.src_attrs_default)

    def _get_urls(self):
        raw = self.url_box.get("1.0", "end-1c").strip()
        return [u.strip() for u in raw.splitlines() if u.strip() and u.strip().startswith("http")]

    def _get_allowed_types(self):
        return self.app.settings.get("allowed_types", "all")

    def _start_download(self):
        urls = self._get_urls()
        if not urls:
            messagebox.showwarning("提示", "请输入至少一个有效的 HTTP URL")
            return

        save_dir = self.dir_var.get().strip()
        if not save_dir:
            messagebox.showwarning("提示", "请选择保存目录")
            return

        os.makedirs(save_dir, exist_ok=True)

        # 配置 manager
        workers = self.thread_var.get()
        self.app.manager._max_workers = workers
        self.app.manager.on_progress = self._on_progress
        self.app.manager.on_file_done = self._on_file_done
        self.app.manager.on_error = self._on_error
        self.app.manager.on_all_done = self._on_all_done
        self.app.manager.on_status = self._on_status

        headers = self.app.settings_tab.get_headers()
        source_attrs = self._get_source_attrs()
        dedup = self.dedup_var.get()

        self._clear_preview()
        self._clear_log()

        # 在后台线程中爬取 + 下载
        def run():
            try:
                total_pages = len(urls)
                for idx, page_url in enumerate(urls):
                    if self.app.manager._cancel_event.is_set():
                        return

                    self.app.log(f"[{idx+1}/{total_pages}] 正在解析: {page_url}")

                    # 每个页面下载到独立子文件夹
                    try:
                        title = get_page_title(page_url, headers)
                    except Exception:
                        title = f"page_{idx+1}"
                    page_save_dir = os.path.join(save_dir, title)

                    try:
                        img_urls = get_image_urls(page_url, headers, source_attrs)
                    except Exception as e:
                        self.app.log(f"解析失败: {page_url} — {e}")
                        continue

                    if not img_urls:
                        self.app.log(f"未找到图片: {page_url}")
                        continue

                    # 文件类型筛选
                    allowed = self._get_allowed_types()
                    if allowed and allowed != "all":
                        img_urls = filter_by_type(img_urls, allowed)

                    self.app.log(f"找到 {len(img_urls)} 张图片（筛选后）")

                    # 记录历史
                    self.app.add_history({
                        "url": page_url,
                        "image_count": len(img_urls),
                        "save_dir": page_save_dir,
                        "time": time.strftime("%Y-%m-%d %H:%M:%S")
                    })

                    # 加载已有文件哈希（去重用）
                    known_hashes = set()
                    if dedup:
                        known_hashes = scan_directory_hashes(page_save_dir)

                    # 重置 manager 并下载当前页
                    done_event = threading.Event()

                    def on_all():
                        done_event.set()

                    old_all_done = self.app.manager.on_all_done
                    self.app.manager.on_all_done = on_all

                    self.app.log(f"开始下载到: {page_save_dir}")
                    ok = self.app.manager.start(img_urls, page_save_dir, headers, dedup, known_hashes)

                    if ok:
                        # 等待当前页下载完成或取消
                        while not done_event.is_set():
                            if self.app.manager._cancel_event.is_set():
                                done_event.set()
                            done_event.wait(timeout=0.5)

                    self.app.manager.on_all_done = old_all_done

                # 全部完成
                self.app.log("✓ 全部页面处理完毕！")
                self.app.after(0, self._reset_buttons)

            except Exception as e:
                self.app.log(f"错误: {e}")
                self.app.after(0, self._reset_buttons)

        self.btn_start.configure(state="disabled")
        self.btn_pause.configure(state="normal")
        self.btn_stop.configure(state="normal")
        self.progress.set(0)
        self.progress_label.configure(text="正在解析页面...")

        threading.Thread(target=run, daemon=True).start()

    def _pause(self):
        if self.app.manager.is_paused:
            self.app.manager.resume()
            self.btn_pause.configure(text="暂停")
        else:
            self.app.manager.pause()
            self.btn_pause.configure(text="继续")

    def _stop(self):
        self.app.manager.cancel()
        self._reset_buttons()
        self._append_log("⚠ 已停止下载")

    # ---- 回调（在 worker 线程调用，用 after 转发到主线程）----

    def _on_progress(self, completed, total):
        self.app.after(0, lambda: self._update_progress(completed, total))

    def _on_file_done(self, url, filepath):
        self.app.after(0, lambda: self._add_preview(filepath))

    def _on_error(self, url, msg):
        self.app.after(0, lambda: self._append_log(f"✗ 失败: {url} — {msg}"))

    def _on_all_done(self):
        self.app.after(0, self._all_done)

    def _on_status(self, msg):
        self.app.after(0, lambda: self._append_log(msg))

    # ---- UI 更新 ----

    def _update_progress(self, completed, total):
        if total > 0:
            self.progress.set(completed / total)
        self.progress_label.configure(text=f"{completed} / {total}")

    def _add_preview(self, filepath):
        try:
            img = Image.open(filepath)
            img.thumbnail((120, 120), Image.LANCZOS)
            ctk_img = ctk.CTkImage(light_image=img, size=(min(img.width, 120), min(img.height, 120)))

            cell = ctk.CTkFrame(self.preview_inner)
            cell.grid(row=self.preview_row, column=self.preview_col, padx=3, pady=3, sticky="nsew")

            lbl = ctk.CTkLabel(cell, image=ctk_img, text="", cursor="hand2")
            lbl.pack(padx=2, pady=2)

            # 右键菜单
            menu = ctk.CTkFrame(lbl)  # dummy, we'll use tkinter Menu
            lbl.bind("<Button-1>", lambda e, p=filepath: self._open_file(p))
            # Use tkinter PopupMenu for right-click
            from tkinter import Menu
            tk_menu = Menu(lbl, tearoff=0)
            tk_menu.add_command(label="打开文件", command=lambda p=filepath: self._open_file(p))
            tk_menu.add_command(label="打开文件夹", command=lambda p=filepath: self._open_folder(p))
            tk_menu.add_command(label="复制路径", command=lambda p=filepath: self._copy_path(p))
            lbl.bind("<Button-3>", lambda e, m=tk_menu: m.tk_popup(e.x_root, e.y_root))

            self.preview_images.append(ctk_img)  # 保活
            self.preview_col += 1
            if self.preview_col >= self.preview_cols:
                self.preview_col = 0
                self.preview_row += 1
        except Exception:
            pass

    def _clear_preview(self):
        for w in self.preview_inner.winfo_children():
            w.destroy()
        self.preview_images.clear()
        self.preview_row = 0
        self.preview_col = 0

    def _append_log(self, msg):
        self.log_box.configure(state="normal")
        self.log_box.insert("end", msg + "\n")
        self.log_box.see("end")
        self.log_box.configure(state="disabled")

    def _clear_log(self):
        self.log_box.configure(state="normal")
        self.log_box.delete("1.0", "end")
        self.log_box.configure(state="disabled")

    def _all_done(self):
        self._append_log("✓ 全部完成！")
        self._reset_buttons()

    def _reset_buttons(self):
        self.btn_start.configure(state="normal")
        self.btn_pause.configure(state="disabled", text="暂停")
        self.btn_stop.configure(state="disabled")

    @staticmethod
    def _open_file(filepath):
        try:
            os.startfile(filepath)
        except Exception:
            pass

    @staticmethod
    def _open_folder(filepath):
        try:
            subprocess.Popen(['explorer', '/select,', filepath])
        except Exception:
            pass

    @staticmethod
    def _copy_path(filepath):
        import tkinter as tk
        root = tk.Tk()
        root.withdraw()
        try:
            root.clipboard_clear()
            root.clipboard_append(filepath)
            root.update()
        except Exception:
            pass
        root.destroy()


# ============================================================
#  历史记录标签页
# ============================================================

class HistoryTab:
    def __init__(self, parent, app: App):
        self.app = app
        self.parent = parent

        # 工具栏
        toolbar = ctk.CTkFrame(parent, fg_color="transparent")
        toolbar.pack(fill="x", padx=5, pady=5)
        ctk.CTkButton(toolbar, text="清空历史", command=self._clear_history,
                      fg_color="#e74c3c", hover_color="#c0392b", width=80).pack(side="right", padx=5)
        ctk.CTkLabel(toolbar, text="下载历史记录", font=ctk.CTkFont(size=14, weight="bold")).pack(side="left")

        # 列表
        self.list_frame = ctk.CTkScrollableFrame(parent)
        self.list_frame.pack(fill="both", expand=True, padx=5, pady=5)

        self.refresh()

    def refresh(self):
        for w in self.list_frame.winfo_children():
            w.destroy()

        if not self.app.history:
            ctk.CTkLabel(self.list_frame, text="暂无记录").pack(pady=20)
            return

        for i, entry in enumerate(reversed(self.app.history)):
            row = ctk.CTkFrame(self.list_frame)
            row.pack(fill="x", padx=5, pady=3)

            info = f"[{entry.get('time', '?')}]  {entry.get('url', '?')}\n图片: {entry.get('image_count', 0)} 张  |  目录: {entry.get('save_dir', '?')}"
            ctk.CTkLabel(row, text=info, anchor="w", justify="left").pack(side="left", fill="x", expand=True, padx=5)

            actual_idx = len(self.app.history) - 1 - i

            btn_frame = ctk.CTkFrame(row, fg_color="transparent")
            btn_frame.pack(side="right", padx=5)

            ctk.CTkButton(btn_frame, text="重新下载", width=70,
                          command=lambda e=entry: self._redownload(e)).pack(side="left", padx=2)
            ctk.CTkButton(btn_frame, text="打开目录", width=70,
                          command=lambda e=entry: self._open_dir(e.get("save_dir", ""))).pack(side="left", padx=2)
            ctk.CTkButton(btn_frame, text="删除", width=50, fg_color="#e74c3c", hover_color="#c0392b",
                          command=lambda idx=actual_idx: self._delete_entry(idx)).pack(side="left", padx=2)

    def _redownload(self, entry):
        self.app.tabview.set("下载")
        self.app.download_tab.url_box.delete("1.0", "end")
        self.app.download_tab.url_box.insert("1.0", entry.get("url", ""))
        self.app.download_tab.dir_var.set(entry.get("save_dir", DEFAULT_SAVE_DIR))

    def _open_dir(self, path):
        if path and os.path.isdir(path):
            try:
                os.startfile(path)
            except Exception:
                pass

    def _delete_entry(self, idx):
        if 0 <= idx < len(self.app.history):
            del self.app.history[idx]
            save_json(HISTORY_FILE, self.app.history)
            self.refresh()

    def _clear_history(self):
        from tkinter import messagebox
        if messagebox.askyesno("确认", "确定要清空所有历史记录吗？"):
            self.app.history.clear()
            save_json(HISTORY_FILE, [])
            self.refresh()


# ============================================================
#  设置标签页
# ============================================================

class SettingsTab:
    def __init__(self, parent, app: App):
        self.app = app
        self.settings = app.settings

        frame = ctk.CTkScrollableFrame(parent)
        frame.pack(fill="both", expand=True, padx=10, pady=10)

        # --- Headers ---
        ctk.CTkLabel(frame, text="自定义请求头", font=ctk.CTkFont(size=14, weight="bold")).pack(anchor="w", pady=(10, 5))

        default_headers = (
            "User-Agent: Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36\n"
            "Referer: \n"
            "Cookie: "
        )
        headers_val = self.settings.get("headers_text", default_headers)
        self.headers_box = ctk.CTkTextbox(frame, height=100, wrap="none")
        self.headers_box.pack(fill="x", padx=5, pady=5)
        self.headers_box.insert("1.0", headers_val)

        # --- 文件类型筛选 ---
        ctk.CTkLabel(frame, text="文件类型筛选", font=ctk.CTkFont(size=14, weight="bold")).pack(anchor="w", pady=(15, 5))

        type_frame = ctk.CTkFrame(frame, fg_color="transparent")
        type_frame.pack(fill="x", padx=5, pady=5)

        allowed = self.settings.get("allowed_types", "all")
        self.type_vars = {}
        for ext in ['.gif', '.png', '.jpg', '.jpeg', '.webp', '.bmp']:
            v = ctk.BooleanVar(value=(allowed == "all" or ext in allowed))
            ctk.CTkCheckBox(type_frame, text=ext, variable=v).pack(side="left", padx=5)
            self.type_vars[ext] = v

        self.type_all_var = ctk.BooleanVar(value=(allowed == "all"))
        ctk.CTkCheckBox(type_frame, text="全部", variable=self.type_all_var).pack(side="left", padx=10)

        # --- 最小文件尺寸 ---
        size_frame = ctk.CTkFrame(frame, fg_color="transparent")
        size_frame.pack(fill="x", padx=5, pady=10)
        ctk.CTkLabel(size_frame, text="最小文件尺寸 (KB):").pack(side="left")
        self.min_size_var = ctk.StringVar(value=str(self.settings.get("min_size_kb", "0")))
        ctk.CTkEntry(size_frame, textvariable=self.min_size_var, width=60).pack(side="left", padx=5)
        ctk.CTkLabel(size_frame, text="0 = 不限").pack(side="left")

        # --- 默认保存目录 ---
        ctk.CTkLabel(frame, text="默认保存目录", font=ctk.CTkFont(size=14, weight="bold")).pack(anchor="w", pady=(15, 5))
        dir_frame = ctk.CTkFrame(frame, fg_color="transparent")
        dir_frame.pack(fill="x", padx=5, pady=5)
        self.default_dir_var = ctk.StringVar(value=self.settings.get("save_dir", DEFAULT_SAVE_DIR))
        ctk.CTkEntry(dir_frame, textvariable=self.default_dir_var).pack(side="left", fill="x", expand=True, padx=5)
        ctk.CTkButton(dir_frame, text="浏览", width=60, command=self._browse_default_dir).pack(side="right")

        # --- 主题 ---
        ctk.CTkLabel(frame, text="外观主题", font=ctk.CTkFont(size=14, weight="bold")).pack(anchor="w", pady=(15, 5))
        theme_frame = ctk.CTkFrame(frame, fg_color="transparent")
        theme_frame.pack(fill="x", padx=5, pady=5)

        self.theme_var = ctk.StringVar(value=self.settings.get("theme", "system"))
        for val, label in [("system", "跟随系统"), ("light", "浅色"), ("dark", "深色")]:
            ctk.CTkRadioButton(theme_frame, text=label, variable=self.theme_var, value=val,
                               command=self._on_theme_change).pack(side="left", padx=10)

        # --- 保存按钮 ---
        ctk.CTkButton(frame, text="保存设置", command=self._save_settings, width=120).pack(pady=20)

    def get_headers(self):
        """解析 headers 文本框为 dict"""
        headers = {}
        text = self.headers_box.get("1.0", "end-1c")
        for line in text.strip().splitlines():
            if ':' in line:
                key, _, val = line.partition(':')
                key = key.strip()
                val = val.strip()
                if key and val:
                    headers[key] = val
        return headers

    def _browse_default_dir(self):
        d = filedialog.askdirectory(title="选择默认保存目录")
        if d:
            self.default_dir_var.set(d)

    def _on_theme_change(self):
        ctk.set_appearance_mode(self.theme_var.get())

    def _save_settings(self):
        allowed = "all"
        if not self.type_all_var.get():
            selected = [ext for ext, v in self.type_vars.items() if v.get()]
            allowed = selected if selected else "all"

        self.settings["allowed_types"] = allowed
        self.settings["min_size_kb"] = self.min_size_var.get()
        self.settings["save_dir"] = self.default_dir_var.get()
        self.settings["theme"] = self.theme_var.get()
        self.settings["headers_text"] = self.headers_box.get("1.0", "end-1c")
        self.settings["max_workers"] = self.app.download_tab.thread_var.get()

        save_json(SETTINGS_FILE, self.settings)

        ctk.set_appearance_mode(self.theme_var.get())
        self.app.download_tab.dir_var.set(self.default_dir_var.get())

        messagebox.showinfo("设置", "设置已保存")


# ============================================================
#  入口
# ============================================================

if __name__ == "__main__":
    app = App()
    app.mainloop()
