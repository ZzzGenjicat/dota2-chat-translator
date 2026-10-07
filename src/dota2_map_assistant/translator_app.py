from __future__ import annotations

import time
import sys
import tkinter as tk
import os
import ctypes
import queue
import threading
from dataclasses import replace
from pathlib import Path
from tkinter import ttk, filedialog
from tkinter import font as tkfont

from .translator_controller import TranslatorController, _row_from_entry
from .gsi_capture import GsiConfigCheck, check_gsi_config, find_dota_dir, install_gsi_config
from .steam_discovery import normalize_dota_dir
from .app_paths import resource_root, user_data_dir, portable_resource_path
from .translator_settings import TranslatorSettings, load_settings, save_settings
from .outgoing_translation import OutgoingTranslator


APP_ROOT = resource_root()
LEGACY_SETTINGS_PATH = APP_ROOT / "config" / "translator_settings.json"
COLORS = {
    "background": "#F3F6FB", "surface": "#FFFFFF", "feed": "#FFFFFF",
    "line": "#DCE4EF", "text": "#19243A", "muted": "#66758B",
    "accent": "#2563EB", "gold": "#A46712", "danger": "#B54747",
}


class TranslatorApp:
    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self._closing = False
        self._poll_after_id: str | None = None
        self._restore_after_id: str | None = None
        self._settings_after_id: str | None = None
        self._outgoing_after_id: str | None = None
        self._outgoing_generation = 0
        self._outgoing_inflight = False
        self.outgoing_result = ""
        self.settings_path = user_data_dir() / "config" / "translator_settings.json"
        self.settings = replace(load_settings(self.settings_path, legacy_path=LEGACY_SETTINGS_PATH), translation_backend='offline')
        self.controller = TranslatorController(self.settings)
        self.settings = self.controller.settings
        self.outgoing_translator = OutgoingTranslator(self.controller.outgoing_provider)
        self.outgoing_events: queue.Queue[tuple[int, str, str]] = queue.Queue()
        self.model_events: queue.Queue[tuple[object, str]] = queue.Queue()
        self.gsi_events: queue.Queue[GsiConfigCheck] = queue.Queue()
        self._gsi_check_pending = False
        self._gsi_check = GsiConfigCheck("game_not_found")
        self._warming_engine = None
        self.model_path_var = tk.StringVar(value=self.settings.offline_model_path)
        self.cpu_threads_var = tk.IntVar(value=self.settings.offline_cpu_threads)
        self.always_on_top_var = tk.BooleanVar(value=self.settings.always_on_top)
        self.opacity_var = tk.DoubleVar(value=self.settings.opacity)
        self.font_size_var = tk.IntVar(value=self.settings.font_size)
        self.status_var = tk.StringVar(value="已暂停；点击开始抓取游戏聊天")
        self.translation_status_var = tk.StringVar(value='本地离线 · M2M100 418M INT8')
        self.model_status_var = tk.StringVar(value=self.controller.engine.status)
        self.gsi_status_var = tk.StringVar(value="正在检查游戏直读配置…")
        self._rendered_rows: tuple = ()
        self._build_ui()
        self._restore_window_bounds()
        self._apply_window_settings()
        self._poll_after_id = self.root.after(250, self._poll)
        self.root.bind("<Destroy>", self._on_destroy, add="+")
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)
        self._begin_gsi_check()

    def _build_ui(self) -> None:
        self.root.title("Dota 2 聊天翻译")
        self.root.geometry("1100x740")
        self.root.minsize(860, 700)
        self.root.configure(background=COLORS["background"])
        families = set(tkfont.families(self.root))
        default_font = tkfont.nametofont('TkDefaultFont', root=self.root).actual('family')
        self.ui_font = _choose_ui_font(families, default_font)
        style = ttk.Style(self.root)
        style.theme_use("clam")
        style.configure("App.TFrame", background=COLORS["background"])
        style.configure("Surface.TFrame", background=COLORS["surface"])
        style.configure("App.TLabel", background=COLORS["background"], foreground=COLORS["text"], font=(self.ui_font, 10))
        style.configure("Muted.TLabel", background=COLORS["background"], foreground=COLORS["muted"], font=(self.ui_font, 10))
        style.configure("Surface.TLabel", background=COLORS["surface"], foreground=COLORS["text"], font=(self.ui_font, 10))
        style.configure("SurfaceMuted.TLabel", background=COLORS["surface"], foreground=COLORS["muted"], font=(self.ui_font, 9))
        style.configure("Section.TLabel", background=COLORS["surface"], foreground=COLORS["text"], font=(self.ui_font, 11, "bold"))
        style.configure("Title.TLabel", background=COLORS["background"], foreground=COLORS["text"], font=(self.ui_font, 20, "bold"))
        style.configure("App.TButton", background=COLORS["surface"], foreground=COLORS["text"], borderwidth=1,
                        relief="solid", font=(self.ui_font, 10), padding=(12, 9))
        style.map("App.TButton", background=[("active", "#EAF0FA")])
        style.configure("Link.TButton", background=COLORS["surface"], foreground=COLORS["accent"],
                        borderwidth=0, font=(self.ui_font, 9), padding=(3, 1))
        style.map("Link.TButton", background=[("active", "#EAF0FA")])
        style.configure("Accent.TButton", background=COLORS["accent"], foreground="#FFFFFF", borderwidth=0,
                        font=(self.ui_font, 11, "bold"), padding=(16, 12))
        style.map("Accent.TButton", background=[("active", "#1D4ED8")], foreground=[("active", "#FFFFFF")])
        style.configure("App.TCheckbutton", background=COLORS["surface"], foreground=COLORS["text"], font=(self.ui_font, 10))
        style.map("App.TCheckbutton", background=[("active", COLORS["surface"])])
        style.configure("App.TCombobox", fieldbackground="#FFFFFF", background="#FFFFFF", foreground=COLORS["text"], padding=5)
        style.configure("App.TEntry", fieldbackground="#FFFFFF", foreground=COLORS["text"], padding=5)
        style.configure("App.TSpinbox", fieldbackground="#FFFFFF", foreground=COLORS["text"], padding=5)

        outer = ttk.Frame(self.root, style="App.TFrame", padding=(24, 20))
        outer.pack(fill=tk.BOTH, expand=True)
        header = ttk.Frame(outer, style="App.TFrame")
        header.pack(fill=tk.X, pady=(0, 20))
        ttk.Label(header, text="Dota 2 聊天翻译", style="Title.TLabel").pack(side=tk.LEFT)
        ttk.Label(header, text="俄语聊天实时译成中文", style="Muted.TLabel").pack(side=tk.LEFT, padx=(18, 0), pady=(8, 0))
        ttk.Label(header, text="游戏直读 · 俄语聊天", style="Muted.TLabel").pack(side=tk.RIGHT, pady=(8, 0))

        body = ttk.Frame(outer, style="App.TFrame")
        body.pack(fill=tk.BOTH, expand=True)
        self.sidebar = tk.Frame(body, background=COLORS["surface"], highlightbackground=COLORS["line"],
                                highlightthickness=1, width=298)
        self.sidebar.pack(side=tk.LEFT, fill=tk.Y, padx=(0, 18))
        self.sidebar.pack_propagate(False)
        side = ttk.Frame(self.sidebar, style="Surface.TFrame", padding=18)
        side.pack(fill=tk.BOTH, expand=True)

        gsi_heading = ttk.Frame(side, style="Surface.TFrame")
        gsi_heading.pack(fill=tk.X)
        ttk.Label(gsi_heading, text="游戏聊天直读", style="Section.TLabel").pack(side=tk.LEFT)
        self.gsi_folder_button = ttk.Button(gsi_heading, text="选择目录", command=self._choose_dota_dir, style="Link.TButton")
        self.gsi_folder_button.pack(side=tk.RIGHT)
        gsi_setup = ttk.Frame(side, style="Surface.TFrame")
        gsi_setup.pack(fill=tk.X, pady=(8, 12))
        ttk.Label(gsi_setup, textvariable=self.gsi_status_var,
                  style="SurfaceMuted.TLabel", wraplength=250).pack(anchor=tk.W)
        self.gsi_install_button = ttk.Button(gsi_setup, text="安装游戏直读配置",
                                            command=self._install_gsi, style="App.TButton")
        ttk.Separator(side).pack(fill=tk.X, pady=(0, 16))
        ttk.Label(side, text="翻译方式", style="Section.TLabel").pack(anchor=tk.W)
        ttk.Label(side, textvariable=self.translation_status_var, style="SurfaceMuted.TLabel",
                  wraplength=250).pack(anchor=tk.W, pady=(8, 6))
        ttk.Label(side, textvariable=self.model_status_var, style='SurfaceMuted.TLabel',
                  wraplength=250).pack(anchor=tk.W, pady=(0, 8))
        ttk.Label(side, text='俄语 → 中文 / 中文 → 俄语\n保留粗话，优先使用游戏口语。',
                  style='SurfaceMuted.TLabel', wraplength=250).pack(anchor=tk.W, pady=(0, 12))
        self.offline_translation_button = ttk.Button(side, text='预热本地模型', command=self._warmup_model,
                                                    style='App.TButton')
        self.offline_translation_button.pack(fill=tk.X, pady=(0, 12))
        self.settings_button = ttk.Button(side, text="更多设置", command=self._toggle_settings, style="App.TButton")
        self.settings_button.pack(fill=tk.X, side=tk.BOTTOM, pady=(9, 0))
        self.start_button = ttk.Button(side, text="开始抓取", command=self._toggle_capture, style="Accent.TButton")
        self.start_button.pack(fill=tk.X, side=tk.BOTTOM, pady=(18, 0))

        self.settings_window = tk.Toplevel(self.root)
        self.settings_window.title("翻译设置")
        self.settings_window.geometry("480x660")
        self.settings_window.minsize(440, 640)
        self.settings_window.configure(background=COLORS["surface"])
        self.settings_window.transient(self.root)
        self.settings_window.withdraw()
        self.settings_window.protocol("WM_DELETE_WINDOW", self._toggle_settings)
        self.settings_panel = ttk.Frame(self.settings_window, style="Surface.TFrame", padding=22)
        self.settings_panel.pack(fill=tk.BOTH, expand=True)
        advanced = self.settings_panel
        ttk.Label(advanced, text='本地翻译', style='Section.TLabel').pack(anchor=tk.W, pady=(0, 8))
        ttk.Label(advanced, text='模型文件夹', style='SurfaceMuted.TLabel').pack(anchor=tk.W)
        ttk.Entry(advanced, textvariable=self.model_path_var, style='App.TEntry').pack(fill=tk.X, pady=(4, 5))
        ttk.Button(advanced, text='选择模型文件夹', command=self._choose_model_path, style='App.TButton').pack(fill=tk.X)
        thread_row = ttk.Frame(advanced, style='Surface.TFrame')
        thread_row.pack(fill=tk.X, pady=(10, 6))
        ttk.Label(thread_row, text='CPU 线程数（默认 2）', style='SurfaceMuted.TLabel').pack(side=tk.LEFT)
        ttk.Combobox(thread_row, values=(1, 2, 3, 4), textvariable=self.cpu_threads_var,
                     state='readonly', width=5, style='App.TCombobox').pack(side=tk.RIGHT)
        ttk.Label(advanced, text='1 线程更省 CPU；2 线程兼顾速度。空闲 3 分钟自动释放模型。',
                  style='SurfaceMuted.TLabel', wraplength=410).pack(anchor=tk.W, pady=(0, 6))
        ttk.Button(advanced, text='应用本地设置 / 重载词库', command=self._apply_offline_settings,
                   style='App.TButton').pack(fill=tk.X, pady=(0, 12))
        ttk.Label(advanced, text="显示设置", style="Section.TLabel").pack(anchor=tk.W, pady=(0, 16))
        ttk.Checkbutton(advanced, text="窗口置顶", variable=self.always_on_top_var,
                        command=self._apply_window_settings, style="App.TCheckbutton").pack(anchor=tk.W)
        font_row = ttk.Frame(advanced, style="Surface.TFrame")
        font_row.pack(fill=tk.X, pady=(8, 7))
        ttk.Label(font_row, text="译文字号", style="SurfaceMuted.TLabel").pack(side=tk.LEFT)
        font_spin = ttk.Spinbox(font_row, from_=9, to=32, width=5, textvariable=self.font_size_var,
                               command=self._apply_font_size, style="App.TSpinbox")
        font_spin.pack(side=tk.RIGHT)
        font_spin.bind("<Return>", lambda _event: self._apply_font_size())
        font_spin.bind("<FocusOut>", lambda _event: self._apply_font_size())
        ttk.Label(advanced, text="窗口透明度", style="SurfaceMuted.TLabel").pack(anchor=tk.W)
        ttk.Scale(advanced, from_=0.35, to=1.0, variable=self.opacity_var,
                  command=lambda _value: self._apply_window_settings()).pack(fill=tk.X, pady=(3, 8))
        ttk.Button(advanced, text="保存设置", command=self._save_settings, style="App.TButton").pack(fill=tk.X, pady=(0, 6))
        ttk.Button(advanced, text="清空文字", command=self._clear_output, style="App.TButton").pack(fill=tk.X, pady=(0, 6))
        ttk.Button(advanced, text="清空翻译缓存", command=self._clear_cache, style="App.TButton").pack(fill=tk.X)

        main = ttk.Frame(body, style="App.TFrame")
        main.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        self.feed_header = ttk.Frame(main, style="App.TFrame")
        self.feed_header.pack(fill=tk.X, pady=(0, 11))
        ttk.Label(self.feed_header, text="实时译文", style="Title.TLabel").pack(side=tk.LEFT)
        ttk.Label(self.feed_header, text="最新消息在下方", style="Muted.TLabel").pack(side=tk.RIGHT, pady=(10, 0))
        feed_frame = tk.Frame(main, background=COLORS["surface"], highlightbackground=COLORS["line"],
                              highlightthickness=1)
        feed_frame.pack(fill=tk.BOTH, expand=True)
        self.feed = tk.Text(feed_frame, wrap=tk.WORD, background=COLORS["feed"], foreground=COLORS["text"],
                            insertbackground=COLORS["text"], relief=tk.FLAT, borderwidth=0, padx=14, pady=10,
                            highlightthickness=0, spacing1=0, spacing3=1, cursor="arrow")
        scrollbar = ttk.Scrollbar(feed_frame, orient=tk.VERTICAL, command=self.feed.yview)
        self.feed.configure(yscrollcommand=scrollbar.set)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        self.feed.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        self.feed.insert(tk.END, "点击开始抓取，等待 Dota 2 聊天。", "original")
        self.feed.configure(state=tk.DISABLED)

        composer = tk.Frame(main, background=COLORS["surface"], highlightbackground=COLORS["line"],
                            highlightthickness=1, padx=14, pady=10)
        composer.pack(fill=tk.X, pady=(12, 0))
        heading = ttk.Frame(composer, style="Surface.TFrame")
        heading.pack(fill=tk.X, pady=(0, 6))
        ttk.Label(heading, text="发言翻译  ·  中文 → 俄语", style="Section.TLabel").pack(side=tk.LEFT)
        ttk.Label(heading, text="输入后自动翻译，保留原话语气", style="SurfaceMuted.TLabel").pack(side=tk.RIGHT)
        self.outgoing_input = tk.Text(composer, height=2, wrap=tk.WORD, font=(self.ui_font, 10),
                                      background=COLORS["background"], foreground=COLORS["text"],
                                      insertbackground=COLORS["text"], relief=tk.FLAT, padx=9, pady=5)
        self.outgoing_input.pack(fill=tk.X)
        self.outgoing_input.bind("<<Modified>>", self._on_outgoing_modified)
        self.outgoing_input.edit_modified(False)
        result_row = ttk.Frame(composer, style="Surface.TFrame")
        result_row.pack(fill=tk.X, pady=(6, 0))
        self.outgoing_output = tk.Text(result_row, height=2, wrap=tk.WORD, font=(self.ui_font, 10),
                                       background=COLORS["surface"], foreground=COLORS["text"],
                                       relief=tk.FLAT, padx=2, pady=3, state=tk.DISABLED)
        self.outgoing_output.pack(side=tk.LEFT, fill=tk.X, expand=True)
        self.copy_outgoing_button = ttk.Button(result_row, text="复制俄语", command=self._copy_outgoing,
                                                style="App.TButton", state=tk.DISABLED)
        self.copy_outgoing_button.pack(side=tk.RIGHT, padx=(8, 0))
        self.outgoing_status_var = tk.StringVar(value="输入中文后自动翻译")
        ttk.Label(composer, textvariable=self.outgoing_status_var, style="SurfaceMuted.TLabel").pack(anchor=tk.W)

        status = ttk.Frame(outer, style="App.TFrame")
        status.pack(fill=tk.X, pady=(14, 0))
        ttk.Label(status, textvariable=self.status_var, style="Muted.TLabel").pack(side=tk.LEFT, fill=tk.X, expand=True)

    def _begin_gsi_check(self) -> None:
        if self._closing or self._gsi_check_pending:
            return
        self._gsi_check_pending = True
        self.gsi_status_var.set("正在检查游戏直读配置…")
        self.gsi_install_button.pack_forget()
        events = self.gsi_events
        preferred = self.settings.dota_game_dir

        def check() -> None:
            # Disk discovery runs once per startup/retry, never on the Tk thread.
            try:
                dota = find_dota_dir(preferred) if preferred else find_dota_dir()
                result = check_gsi_config(dota)
            except (OSError, UnicodeError):
                result = GsiConfigCheck("unreadable")
            events.put(result)

        threading.Thread(target=check, name="gsi-config-check", daemon=True).start()

    def _show_gsi_check(self, result: GsiConfigCheck, *, just_installed: bool = False) -> None:
        self._gsi_check = result
        self._gsi_check_pending = False
        if result.dota_dir is not None:
            selected = str(result.dota_dir.resolve())
            if selected != self.settings.dota_game_dir:
                self.settings = replace(self.settings, dota_game_dir=selected)
                self.controller.settings = self.settings
                try:
                    save_settings(self.settings_path, self.settings)
                except OSError:
                    self.status_var.set("无法记住游戏目录；本次仍可使用")
        messages = {
            "installed": "配置已安装，无需重复安装。每次打开自动检查；首次安装后需重启 Dota 2。",
            "missing": "配置未安装。只需安装一次，安装后重启 Dota 2。",
            "invalid": "配置异常，请修复。通常只需安装一次，修复后重启 Dota 2。",
            "game_not_found": "未找到 Dota 2。可选择游戏目录，或确认安装后重新检查。",
            "unreadable": "无法读取配置，暂不能确认安装状态。请检查访问权限后重新检查。",
        }
        self.gsi_status_var.set("配置已安装，无需重复安装。请重启 Dota 2 后点击“开始抓取”。"
                                if just_installed and result.state == "installed" else messages[result.state])
        if result.state == "installed":
            self.gsi_install_button.pack_forget()
            return
        installable = result.state in ("missing", "invalid")
        text = ("修复游戏直读配置" if result.state == "invalid" else "安装游戏直读配置") if installable else "重新检查"
        self.gsi_install_button.configure(text=text, command=self._install_gsi if installable else self._begin_gsi_check)
        self.gsi_install_button.pack(fill=tk.X, pady=(8, 0))

    def _choose_dota_dir(self) -> None:
        if self._closing or self._gsi_check_pending:
            return
        selected = filedialog.askdirectory(parent=self.root, title="选择 Dota 2 安装目录（dota 2 beta 或 game/dota）")
        if not selected:
            return
        dota = normalize_dota_dir(selected)
        if dota is None:
            self.gsi_status_var.set("所选目录不是 Dota 2。请选择“dota 2 beta”或“game/dota”文件夹。")
            return
        self._show_gsi_check(check_gsi_config(dota))

    def _install_gsi(self) -> None:
        if self._closing or self._gsi_check_pending:
            return
        dota_dir = self._gsi_check.dota_dir
        if dota_dir is None or self._gsi_check.state not in ("missing", "invalid"):
            self._begin_gsi_check()
            return
        try:
            target = install_gsi_config(dota_dir)
        except OSError as exc:
            self.status_var.set(f"安装游戏直读配置失败：{exc}")
            self.gsi_status_var.set("安装失败，请检查写入权限后重试。只需安装一次，安装后重启 Dota 2。")
            return
        result = check_gsi_config(dota_dir)
        self._show_gsi_check(result, just_installed=True)
        if result.state != "installed":
            self.status_var.set("配置写入后检查未通过，请按左侧说明重试")
            return
        self.status_var.set(f"配置已安装到 {target}；请重启 Dota 2 后点击开始抓取")

    def _toggle_capture(self) -> None:
        if self.controller.status == "运行中":
            self.controller.stop()
            self.start_button.configure(text="开始抓取")
        else:
            self._save_settings()
            self.controller.start()
            self.start_button.configure(text="暂停抓取" if self.controller.status == "运行中" else "开始抓取")
        self.status_var.set(self.controller.capture_status)

    def _choose_model_path(self) -> None:
        selected = filedialog.askdirectory(parent=self.settings_window, title='选择 M2M100 INT8 模型文件夹')
        if selected:
            self.model_path_var.set(portable_resource_path(selected))

    def _apply_offline_settings(self) -> None:
        if self._outgoing_inflight or self._warming_engine is not None:
            self.model_status_var.set('当前翻译或预热完成后可应用设置')
            return
        try:
            threads = max(1, min(4, int(self.cpu_threads_var.get())))
            self.controller.set_offline_provider(self.model_path_var.get(), threads)
        except (ValueError, RuntimeError, tk.TclError) as exc:
            self.model_status_var.set(str(exc))
            return
        self.settings = self.controller.settings
        self.model_path_var.set(self.settings.offline_model_path)
        self.outgoing_translator.provider = self.controller.outgoing_provider
        self._save_settings()
        self._schedule_outgoing_translation()
        self._warmup_model()

    def _warmup_model(self) -> None:
        if self._warming_engine is not None:
            return
        engine = self.controller.engine
        self._warming_engine = engine
        self.model_status_var.set('正在预热本地模型…')
        self.offline_translation_button.configure(state=tk.DISABLED)
        def work() -> None:
            try:
                engine.warmup()
                message = engine.status
            except Exception as exc:
                message = str(exc)
            self.model_events.put((engine, message))
        threading.Thread(target=work, daemon=True, name='offline-warmup').start()

    def _on_outgoing_modified(self, _event: tk.Event) -> None:
        if not self.outgoing_input.edit_modified():
            return
        self.outgoing_input.edit_modified(False)
        self._schedule_outgoing_translation()

    def _schedule_outgoing_translation(self) -> None:
        self._outgoing_generation += 1
        if self._outgoing_after_id is not None:
            self.root.after_cancel(self._outgoing_after_id)
            self._outgoing_after_id = None
        self._show_outgoing_result("")
        if not self.outgoing_input.get("1.0", tk.END).strip():
            self.outgoing_status_var.set("输入中文后自动翻译")
            return
        self.outgoing_status_var.set("准备翻译…")
        self._outgoing_after_id = self.root.after(450, self._start_outgoing_translation)

    def _start_outgoing_translation(self) -> None:
        self._outgoing_after_id = None
        if self._closing or self._outgoing_inflight:
            return
        source = self.outgoing_input.get("1.0", tk.END).strip()
        if not source:
            return
        generation = self._outgoing_generation
        backend = "offline"
        model = ""
        self._outgoing_inflight = True
        self.outgoing_status_var.set("正在翻译…")

        def work() -> None:
            try:
                result = self.outgoing_translator.translate(source, backend, model)
                self.outgoing_events.put((generation, result, ""))
            except Exception as exc:
                self.outgoing_events.put((generation, "", str(exc)))

        threading.Thread(target=work, daemon=True, name="outgoing-translation").start()

    def _show_outgoing_result(self, result: str) -> None:
        self.outgoing_result = result
        self.outgoing_output.configure(state=tk.NORMAL)
        self.outgoing_output.delete("1.0", tk.END)
        if result:
            self.outgoing_output.insert("1.0", result)
        self.outgoing_output.configure(state=tk.DISABLED)
        self.copy_outgoing_button.configure(state=tk.NORMAL if result else tk.DISABLED)

    def _copy_outgoing(self) -> None:
        if not self.outgoing_result:
            return
        self.root.clipboard_clear()
        self.root.clipboard_append(self.outgoing_result)
        self.outgoing_status_var.set("俄语译文已复制，可粘贴到 Dota 2 聊天")

    def _toggle_settings(self) -> None:
        if self.settings_window.state() == "normal":
            self.settings_window.withdraw()
            self.settings_button.configure(text="更多设置")
        else:
            self.settings_window.deiconify()
            left = self.root.winfo_rootx() + max(20, (self.root.winfo_width() - 480) // 2)
            top = self.root.winfo_rooty() + max(20, (self.root.winfo_height() - 660) // 2)
            self._settings_after_id = self.root.after_idle(lambda: _place_window(self.settings_window, left, top))
            self.settings_window.lift()
            self.settings_button.configure(text="收起设置")

    def _save_settings(self) -> None:
        try:
            font_size = max(9, min(32, int(self.font_size_var.get())))
        except (ValueError, tk.TclError):
            font_size = 14
            self.font_size_var.set(font_size)
        self.settings = replace(
            self.settings,
            network_profile="Balanced",
            network_paused=False,
            provider_url="",
            provider_api_key="",
            provider_model="",
            always_on_top=self.always_on_top_var.get(),
            opacity=float(self.opacity_var.get()),
            font_size=font_size,
            window_bounds=(self.root.winfo_x(), self.root.winfo_y(), self.root.winfo_width(), self.root.winfo_height()),
        )
        self.controller.settings = self.settings
        self.controller.set_profile("Balanced")
        self.controller.set_network_paused(False)
        try:
            save_settings(self.settings_path, self.settings)
        except OSError:
            self.status_var.set("无法保存设置；本次仍可使用，请检查文件夹写入权限")
        else:
            self.status_var.set("设置已保存")
        self._apply_font_size()

    def _clear_output(self) -> None:
        self.controller.clear_output()
        self._render_rows()

    def _clear_cache(self) -> None:
        try:
            self.controller.clear_cache()
        except OSError:
            self.status_var.set("无法清空缓存，请检查文件夹写入权限")
        else:
            self.status_var.set("翻译缓存已清空")

    def _apply_font_size(self) -> None:
        try:
            size = max(9, min(32, int(self.font_size_var.get())))
        except (ValueError, tk.TclError):
            size = 14
        self.feed.tag_configure("translation", font=(self.ui_font, size, "bold"), foreground=COLORS["text"], spacing3=1)
        self.feed.tag_configure("original", font=(self.ui_font, max(10, size - 2)), foreground=COLORS["muted"], spacing3=3)
        self.feed.tag_configure("meta", font=(self.ui_font, 8), foreground=COLORS["accent"], spacing3=3)
        self.feed.tag_configure("pending", font=(self.ui_font, max(10, size - 2)), foreground=COLORS["gold"], spacing3=1)
        self.feed.tag_configure("separator", foreground=COLORS["line"])

    def _apply_window_settings(self) -> None:
        topmost = bool(self.always_on_top_var.get())
        self.root.attributes("-topmost", topmost)
        self.settings_window.attributes("-topmost", topmost)
        self.root.attributes("-alpha", float(self.opacity_var.get()))
        self._apply_font_size()

    def _poll(self) -> None:
        self._poll_after_id = None
        if self._closing:
            return
        while True:
            try:
                gsi_check = self.gsi_events.get_nowait()
            except queue.Empty:
                break
            self._show_gsi_check(gsi_check)
        self.controller.poll()
        while True:
            try:
                generation, result, error = self.outgoing_events.get_nowait()
            except queue.Empty:
                break
            self._outgoing_inflight = False
            if generation == self._outgoing_generation:
                if error:
                    self.outgoing_status_var.set(f"翻译失败：{error}")
                else:
                    self._show_outgoing_result(result)
                    self.outgoing_status_var.set("译文已就绪，点击复制俄语")
            elif self._outgoing_after_id is None and self.outgoing_input.get("1.0", tk.END).strip():
                self._start_outgoing_translation()
        while True:
            try:
                engine, message = self.model_events.get_nowait()
            except queue.Empty:
                break
            self._warming_engine = None
            if engine is self.controller.engine:
                self.model_status_var.set(message)
                self.offline_translation_button.configure(state=tk.NORMAL)
        if self._warming_engine is None:
            self.model_status_var.set(self.controller.engine.status)
        self._render_rows()
        self.status_var.set(self.controller.capture_status if self.controller.status == "运行中" else self.status_var.get())
        self._poll_after_id = self.root.after(250, self._poll)

    def _render_rows(self) -> None:
        signature = tuple(
            (row.key, row.status, row.translated_text, row.observed_at) for row in self.controller.rows
        )
        if signature == self._rendered_rows:
            return
        self._rendered_rows = signature
        at_bottom = self.feed.yview()[1] > 0.98
        scroll_position = self.feed.yview()[0]
        self.feed.configure(state=tk.NORMAL)
        self.feed.delete("1.0", tk.END)
        if not self.controller.rows:
            self.feed.insert(tk.END, "点击开始抓取，等待 Dota 2 推送聊天。", "original")
        for row in self.controller.rows:
            timestamp = time.strftime("%H:%M:%S", time.localtime(row.observed_at))
            if row.translated_text:
                self.feed.insert(tk.END, row.translated_text + "\n", "translation")
            else:
                self.feed.insert(tk.END, _display_status(row.status) + "\n", "pending")
            self.feed.insert(tk.END, f"{timestamp} · {row.language.upper()}  ", "meta")
            self.feed.insert(tk.END, row.source_text + "\n", "original")
        self.feed.configure(state=tk.DISABLED)
        if at_bottom:
            self.feed.see(tk.END)
        else:
            self.feed.yview_moveto(scroll_position)

    def _restore_window_bounds(self) -> None:
        bounds = self.settings.window_bounds
        if bounds is None:
            return
        left, top, width, height = bounds
        screen = _virtual_screen_bbox(self.root)
        if left + width < screen[0] + 100 or left > screen[2] - 100 or top + height < screen[1] + 100 or top > screen[3] - 100:
            return
        self.root.geometry(f"{width}x{height}+0+0")
        self._restore_after_id = self.root.after(80, lambda: _place_window(self.root, left, top))

    def _on_close(self) -> None:
        self._cancel_callbacks()
        self._save_settings()
        self.controller.close()
        self.root.destroy()

    def _on_destroy(self, event: tk.Event) -> None:
        if event.widget is self.root:
            self._cancel_callbacks()

    def _cancel_callbacks(self) -> None:
        self._closing = True
        for name in ("_poll_after_id", "_restore_after_id", "_settings_after_id", "_outgoing_after_id"):
            after_id = getattr(self, name)
            if after_id is not None:
                try:
                    self.root.after_cancel(after_id)
                except tk.TclError:
                    pass
                setattr(self, name, None)


def _display_status(status: str) -> str:
    if status == "queued":
        return "等待翻译…"
    if status == "paused":
        return "本地翻译已暂停"
    if status == "untranslated":
        return "等待本地翻译…"
    if status == "unchanged":
        return "翻译服务未返回中文译文"
    if status.startswith("failed"):
        reason = status.partition(":")[2].strip()
        if "429" in reason or "限额" in reason or "暂不可用" in reason:
            return "翻译服务限流，请稍后重试"
        return "翻译暂不可用 · " + reason
    return "识别完成"


def _choose_ui_font(families: set[str], fallback: str, platform: str | None = None) -> str:
    preferred = ('PingFang SC', 'Heiti SC', '.AppleSystemUIFont') if (platform or sys.platform) == 'darwin' else ('Microsoft YaHei UI', 'Segoe UI')
    return next((name for name in preferred if name in families), fallback)


def _place_window(window: tk.Tk, left: int, top: int) -> None:
    if os.name != "nt":
        window.geometry(f"+{left}+{top}")
        return
    window.update_idletasks()
    user32 = ctypes.windll.user32
    user32.GetParent.restype = ctypes.c_void_p
    user32.SetWindowPos.argtypes = (
        ctypes.c_void_p, ctypes.c_void_p, ctypes.c_int, ctypes.c_int,
        ctypes.c_int, ctypes.c_int, ctypes.c_uint,
    )
    user32.SetWindowPos.restype = ctypes.c_bool
    wrapper = user32.GetParent(window.winfo_id())
    if wrapper:
        user32.SetWindowPos(wrapper, None, left, top, 0, 0, 0x15)


def _virtual_screen_bbox(root: tk.Tk) -> tuple[int, int, int, int]:
    if os.name == "nt":
        user32 = ctypes.windll.user32
        left = user32.GetSystemMetrics(76)
        top = user32.GetSystemMetrics(77)
        return left, top, left + user32.GetSystemMetrics(78), top + user32.GetSystemMetrics(79)
    return 0, 0, root.winfo_screenwidth(), root.winfo_screenheight()


def main() -> None:
    _enable_dpi_awareness()
    root = tk.Tk()
    app = TranslatorApp(root)
    root.after(100, app._warmup_model)
    root.mainloop()


def _enable_dpi_awareness() -> None:
    if os.name == "nt":
        try:
            import ctypes

            ctypes.windll.shcore.SetProcessDpiAwareness(2)
        except (AttributeError, OSError):
            pass
