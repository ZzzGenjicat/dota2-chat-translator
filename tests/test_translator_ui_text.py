import sys
import tkinter as tk
import time
import threading
import tempfile
import gc
import os
import json
from dataclasses import replace
from unittest.mock import patch
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from dota2_map_assistant.translator_app import TranslatorApp, _display_status
from dota2_map_assistant.translator_controller import DisplayRow
from dota2_map_assistant.translator_settings import TranslatorSettings
from dota2_map_assistant.gsi_capture import GsiConfigCheck, install_gsi_config
from dota2_map_assistant.steam_discovery import find_dota_dir


class TranslatorUiTextTests(unittest.TestCase):
    def setUp(self) -> None:
        data = tempfile.TemporaryDirectory()
        self.addCleanup(data.cleanup)
        data_env = patch.dict(os.environ, {"DOTA2_TRANSLATOR_DATA_DIR": data.name})
        data_env.start()
        self.addCleanup(data_env.stop)
        finder = patch("dota2_map_assistant.translator_app.find_dota_dir", return_value=None)
        finder.start()
        self.addCleanup(finder.stop)

    def tearDown(self) -> None:
        # Dispose destroyed Tk objects on the UI thread before the next worker
        # can trigger Python's cyclic garbage collector in a later test.
        gc.collect()

    def _wait_for_gsi_check(self, root, app) -> None:
        deadline = time.monotonic() + 2
        while app._gsi_check_pending and time.monotonic() < deadline:
            root.update()
            time.sleep(0.01)
        self.assertFalse(app._gsi_check_pending, "startup check did not finish")

    def test_installed_config_hides_install_button_without_rewriting(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            dota = Path(tmp)
            (dota / "cfg").mkdir()
            target = install_gsi_config(dota)
            before = target.stat().st_mtime_ns
            root = tk.Tk()
            root.withdraw()
            with patch("dota2_map_assistant.translator_app.find_dota_dir", return_value=dota):
                app = TranslatorApp(root)
                try:
                    self._wait_for_gsi_check(root, app)
                    self.assertEqual(app.gsi_install_button.winfo_manager(), "")
                    self.assertIn("已安装", app.gsi_status_var.get())
                    self.assertIn("无需重复", app.gsi_status_var.get())
                    self.assertEqual(target.stat().st_mtime_ns, before)
                finally:
                    app.controller.close()
                    root.destroy()

    def test_missing_and_invalid_config_show_action_and_hide_after_install(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            dota = Path(tmp)
            (dota / "cfg").mkdir()
            for initial_state in ("missing", "invalid"):
                with self.subTest(initial_state=initial_state):
                    if initial_state == "invalid":
                        target = install_gsi_config(dota)
                        target.write_text('"broken" {', encoding="utf-8")
                    root = tk.Tk()
                    root.withdraw()
                    with patch("dota2_map_assistant.translator_app.find_dota_dir", return_value=dota):
                        app = TranslatorApp(root)
                        try:
                            self._wait_for_gsi_check(root, app)
                            self.assertEqual(app.gsi_install_button.winfo_manager(), "pack")
                            self.assertIn("一次", app.gsi_status_var.get())
                            self.assertIn("重启 Dota 2", app.gsi_status_var.get())
                            self.assertEqual(app.gsi_install_button.cget("text"),
                                             "安装游戏直读配置" if initial_state == "missing" else "修复游戏直读配置")
                            app.gsi_install_button.invoke()
                            self.assertEqual(app.gsi_install_button.winfo_manager(), "")
                            self.assertIn("重启 Dota 2", app.gsi_status_var.get())
                            target = app._gsi_check.path
                            self.assertTrue(target.is_file())
                        finally:
                            app.controller.close()
                            root.destroy()

    def test_next_startup_detects_config_removed_since_last_run(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            dota = Path(tmp)
            (dota / "cfg").mkdir()
            target = install_gsi_config(dota)
            for expected_state in ("installed", "missing"):
                root = tk.Tk()
                root.withdraw()
                with patch("dota2_map_assistant.translator_app.find_dota_dir", return_value=dota):
                    app = TranslatorApp(root)
                    try:
                        self._wait_for_gsi_check(root, app)
                        self.assertEqual(app._gsi_check.state, expected_state)
                        self.assertEqual(bool(app.gsi_install_button.winfo_manager()), expected_state == "missing")
                    finally:
                        app.controller.close()
                        root.destroy()
                if target.exists():
                    target.unlink()

    def test_unfound_game_offers_retry_without_attempting_install(self) -> None:
        root = tk.Tk()
        root.withdraw()
        app = TranslatorApp(root)
        try:
            self._wait_for_gsi_check(root, app)
            self.assertIn("未找到 Dota 2", app.gsi_status_var.get())
            self.assertEqual(app.gsi_install_button.cget("text"), "重新检查")
            with patch("dota2_map_assistant.translator_app.install_gsi_config") as installer:
                app.gsi_install_button.invoke()
                self._wait_for_gsi_check(root, app)
                installer.assert_not_called()
        finally:
            app.controller.close()
            root.destroy()

    def test_failed_install_keeps_button_and_explanation_visible(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            dota = Path(tmp)
            (dota / "cfg").mkdir()
            root = tk.Tk()
            root.withdraw()
            with patch("dota2_map_assistant.translator_app.find_dota_dir", return_value=dota):
                app = TranslatorApp(root)
                try:
                    self._wait_for_gsi_check(root, app)
                    with patch("dota2_map_assistant.translator_app.install_gsi_config", side_effect=PermissionError("denied")):
                        app.gsi_install_button.invoke()
                    self.assertEqual(app.gsi_install_button.winfo_manager(), "pack")
                    self.assertIn("失败", app.gsi_status_var.get())
                    self.assertIn("一次", app.gsi_status_var.get())
                finally:
                    app.controller.close()
                    root.destroy()

    def test_slow_startup_check_does_not_block_window_and_can_finish_after_close(self) -> None:
        entered = threading.Event()
        release = threading.Event()

        def slow_find():
            entered.set()
            release.wait(2)
            return None

        root = tk.Tk()
        root.withdraw()
        with patch("dota2_map_assistant.translator_app.find_dota_dir", side_effect=slow_find):
            app = TranslatorApp(root)
            try:
                self.assertTrue(entered.wait(1))
                root.update()
                self.assertTrue(app._gsi_check_pending)
                self.assertEqual(app.gsi_install_button.winfo_manager(), "")
                self.assertTrue(app.start_button.winfo_exists())
                root.destroy()
            finally:
                release.set()
                app.controller.close()
                if not app._closing:
                    root.destroy()

    def test_setup_instructions_keep_controls_visible_at_minimum_size(self) -> None:
        root = tk.Tk()
        scaling = root.tk.call("tk", "scaling")
        root.tk.call("tk", "scaling", 1.666)  # Windows 125% display scaling.
        with patch("dota2_map_assistant.translator_app.load_settings", return_value=TranslatorSettings()):
            app = TranslatorApp(root)
        try:
            root.geometry("860x700+100+100")
            root.update()
            for state in ("installed", "missing", "invalid", "unreadable", "game_not_found"):
                with self.subTest(state=state):
                    app._show_gsi_check(GsiConfigCheck(state))
                    root.update_idletasks()
                    controls = [app.start_button, app.settings_button, app.offline_translation_button]
                    if state != "installed":
                        controls.append(app.gsi_install_button)
                    for widget in controls:
                        self.assertTrue(widget.winfo_ismapped(), widget.cget("text"))
                        self.assertEqual(widget.winfo_height(), widget.winfo_reqheight(), widget.cget("text"))
        finally:
            app.controller.close()
            root.tk.call("tk", "scaling", scaling)
            root.destroy()

    def test_manual_game_selection_is_validated_saved_and_rechecked_on_next_launch(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            install = Path(tmp) / "我的 Dota 2"
            dota = install / "game" / "dota"
            (dota / "cfg").mkdir(parents=True)
            (dota / "gameinfo.gi").write_text('"GameInfo" {}', encoding="utf-8")
            dota = dota.resolve()
            root = tk.Tk()
            root.withdraw()
            app = TranslatorApp(root)
            try:
                self._wait_for_gsi_check(root, app)
                with patch("dota2_map_assistant.translator_app.filedialog.askdirectory", return_value=str(install)), patch("dota2_map_assistant.translator_app.install_gsi_config") as installer:
                    app.gsi_folder_button.invoke()
                    installer.assert_not_called()
                self.assertEqual(app._gsi_check.dota_dir, dota)
                self.assertEqual(app._gsi_check.state, "missing")
                self.assertEqual(app.settings.dota_game_dir, str(dota))
                self.assertEqual(app.settings_path.parent.parent, Path(os.environ["DOTA2_TRANSLATOR_DATA_DIR"]).resolve())
                saved = json.loads(app.settings_path.read_text(encoding="utf-8"))
                self.assertEqual(saved["dota_game_dir"], str(dota))
            finally:
                app.controller.close()
                root.destroy()
            root = tk.Tk()
            root.withdraw()
            with patch("dota2_map_assistant.translator_app.find_dota_dir", wraps=find_dota_dir) as finder:
                app = TranslatorApp(root)
                try:
                    self._wait_for_gsi_check(root, app)
                    finder.assert_called_once_with(str(dota))
                    self.assertEqual(app._gsi_check.dota_dir, dota)
                finally:
                    app.controller.close()
                    root.destroy()

    def test_unrelated_manual_directory_is_rejected_without_remembering_it(self) -> None:
        root = tk.Tk()
        root.withdraw()
        app = TranslatorApp(root)
        try:
            self._wait_for_gsi_check(root, app)
            with patch("dota2_map_assistant.translator_app.filedialog.askdirectory", return_value=str(ROOT)):
                app.gsi_folder_button.invoke()
            self.assertEqual(app.settings.dota_game_dir, "")
            self.assertIn("不是 Dota 2", app.gsi_status_var.get())
        finally:
            app.controller.close()
            root.destroy()

    def test_saved_window_position_is_restored_after_window_maps(self) -> None:
        root = tk.Tk()
        with patch("dota2_map_assistant.translator_app.load_settings", return_value=TranslatorSettings(window_bounds=(320, 140, 900, 700))):
            app = TranslatorApp(root)
        try:
            root.update()
            time.sleep(0.12)
            root.update()
            self.assertEqual((root.winfo_x(), root.winfo_y()), (320, 140))
        finally:
            app.controller.close()
            root.destroy()

    def test_destroy_cancels_scheduled_ui_poll(self) -> None:
        root = tk.Tk()
        app = TranslatorApp(root)
        poll_id = app._poll_after_id

        root.destroy()

        self.assertNotIn(poll_id, root.tk.call("after", "info"))
        app.controller.close()

    def test_settings_window_stays_above_always_on_top_main_window(self) -> None:
        root = tk.Tk()
        root.withdraw()
        app = TranslatorApp(root)
        try:
            app.always_on_top_var.set(True)
            app._apply_window_settings()
            app._toggle_settings()
            self.assertTrue(bool(app.settings_window.attributes("-topmost")))
        finally:
            app.controller.close()
            root.destroy()

    def test_settings_window_opens_near_main_window(self) -> None:
        root = tk.Tk()
        app = TranslatorApp(root)
        try:
            root.geometry("900x700+600+100")
            root.update()
            app._toggle_settings()
            root.update()
            self.assertGreaterEqual(app.settings_window.winfo_rootx(), root.winfo_rootx())
            self.assertLess(app.settings_window.winfo_rootx(), root.winfo_rootx() + root.winfo_width())
        finally:
            app.controller.close()
            root.destroy()

    def test_cache_clear_failure_is_shown_in_window(self) -> None:
        root = tk.Tk()
        root.withdraw()
        app = TranslatorApp(root)
        try:
            with patch.object(app.controller, "clear_cache", side_effect=OSError("read only")):
                app._clear_cache()
            self.assertIn("无法清空", app.status_var.get())
        finally:
            app.controller.close()
            root.destroy()

    def test_settings_write_failure_does_not_break_window(self) -> None:
        root = tk.Tk()
        root.withdraw()
        app = TranslatorApp(root)
        try:
            with patch("dota2_map_assistant.translator_app.save_settings", side_effect=OSError("read only")):
                app._save_settings()
            self.assertIn("无法保存设置", app.status_var.get())
            self.assertTrue(app.feed.winfo_exists())
        finally:
            app.controller.close()
            root.destroy()

    def test_feed_shows_screen_order_with_newest_message_at_bottom(self) -> None:
        root = tk.Tk()
        root.withdraw()
        app = TranslatorApp(root)
        try:
            app.controller.rows = [
                DisplayRow("eng:old", "old", "eng", "旧消息", "translated", 1.0),
                DisplayRow("eng:new", "new", "eng", "新消息", "translated", 2.0),
            ]
            app._render_rows()
            shown = app.feed.get("1.0", tk.END)
            self.assertLess(shown.index("旧消息"), shown.index("新消息"))
            self.assertIn("new", shown)
            self.assertNotIn("────────────────", shown)
        finally:
            app.controller.close()
            root.destroy()

    def test_light_layout_has_capture_controls_and_readable_feed(self) -> None:
        root = tk.Tk()
        root.withdraw()
        try:
            app = TranslatorApp(root)
            self.assertTrue(app.sidebar.winfo_exists())
            self.assertTrue(app.feed.winfo_exists())
            self.assertEqual(app.start_button.cget("text"), "开始抓取")
            background = root.winfo_rgb(root.cget("background"))
            self.assertGreater(min(background), 40000)
        finally:
            app.controller.close()
            root.destroy()





    def test_chinese_composer_automatically_translates_and_copies_russian(self) -> None:
        root = tk.Tk()
        app = TranslatorApp(root)
        try:
            calls = []

            def translate(text, backend, model):
                calls.append((text, backend, model))
                return "Иди на мид, идиот"

            app.outgoing_translator.translate = translate
            app.outgoing_input.insert("1.0", "去中路，白痴")
            deadline = time.monotonic() + 3
            while time.monotonic() < deadline and not app.outgoing_result:
                root.update()
                time.sleep(0.03)
            self.assertEqual(app.outgoing_result, "Иди на мид, идиот")
            self.assertEqual(calls[0][0], "去中路，白痴")
            self.assertEqual(str(app.copy_outgoing_button.cget("state")), "normal")
            app.copy_outgoing_button.invoke()
            self.assertEqual(root.clipboard_get(), "Иди на мид, идиот")
        finally:
            app.controller.close()
            root.destroy()

    def test_editing_chinese_clears_old_copyable_translation(self) -> None:
        root = tk.Tk()
        app = TranslatorApp(root)
        try:
            app._show_outgoing_result("Иди на мид")
            app.outgoing_input.insert("1.0", "新消息")
            root.update()
            self.assertEqual(app.outgoing_result, "")
            self.assertEqual(str(app.copy_outgoing_button.cget("state")), "disabled")
        finally:
            app.controller.close()
            root.destroy()

    def test_old_translation_cannot_replace_newer_chinese_input(self) -> None:
        root = tk.Tk()
        app = TranslatorApp(root)
        release_first = threading.Event()
        try:
            calls = []

            def translate(text, backend, model):
                calls.append(text)
                if text == "第一句":
                    release_first.wait(2)
                    return "Первое"
                return "Второе"

            app.outgoing_translator.translate = translate
            app.outgoing_input.insert("1.0", "第一句")
            root.update()
            app._start_outgoing_translation()
            app.outgoing_input.delete("1.0", tk.END)
            app.outgoing_input.insert("1.0", "第二句")
            root.update()
            release_first.set()
            deadline = time.monotonic() + 3
            while time.monotonic() < deadline and app.outgoing_result != "Второе":
                root.update()
                time.sleep(0.03)
            self.assertEqual(app.outgoing_result, "Второе")
            self.assertEqual(calls, ["第一句", "第二句"])
        finally:
            release_first.set()
            app.controller.close()
            root.destroy()







    def test_startup_is_offline_even_with_old_online_settings(self):
        root = tk.Tk()
        with patch('dota2_map_assistant.translator_app.load_settings',
                   return_value=TranslatorSettings(translation_backend='chatgpt', chatgpt_model='old')):
            app = TranslatorApp(root)
        try:
            self.assertEqual(app.settings.translation_backend, 'offline')
            self.assertIn('本地离线', app.translation_status_var.get())
            self.assertFalse(hasattr(app, 'chatgpt_window'))
            self.assertTrue(app.offline_translation_button.winfo_exists())
            self.assertIs(app.outgoing_translator.provider.engine, app.controller.engine)
        finally:
            app.controller.close()
            root.destroy()

    def test_offline_settings_apply_and_save(self):
        root = tk.Tk()
        app = TranslatorApp(root)
        try:
            app.cpu_threads_var.set(1)
            with patch('dota2_map_assistant.translator_app.save_settings') as save, patch.object(app, '_warmup_model'):
                app._apply_offline_settings()
            self.assertEqual(app.settings.offline_cpu_threads, 1)
            self.assertIs(app.outgoing_translator.provider.engine, app.controller.engine)
            save.assert_called()
        finally:
            app.controller.close()
            root.destroy()

    def test_cache_clear_includes_outgoing_cache(self):
        root = tk.Tk()
        app = TranslatorApp(root)
        try:
            self.assertIs(app.controller.translation_cache, app.outgoing_translator.provider.cache)
        finally:
            app.controller.close()
            root.destroy()

    def test_all_offline_settings_buttons_are_visible(self):
        root = tk.Tk()
        app = TranslatorApp(root)
        try:
            app._toggle_settings()
            root.update()
            bottom = app.settings_window.winfo_rooty() + app.settings_window.winfo_height()
            for child in app.settings_panel.winfo_children():
                if isinstance(child, tk.ttk.Button):
                    self.assertGreater(child.winfo_height(), 1, child.cget('text'))
                    self.assertLessEqual(child.winfo_rooty() + child.winfo_height(), bottom, child.cget('text'))
        finally:
            app.controller.close()
            root.destroy()


if __name__ == '__main__':
    unittest.main()
