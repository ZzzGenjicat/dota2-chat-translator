import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from dota2_map_assistant.translator_models import PROFILE_LIMITS
from dota2_map_assistant.translator_settings import (
    TranslatorSettings,
    load_settings,
    save_settings,
)


class TranslatorSettingsTests(unittest.TestCase):
    def test_new_install_defaults_to_offline_translation(self) -> None:
        self.assertEqual(TranslatorSettings().translation_backend, "offline")

    def test_malformed_settings_file_uses_defaults(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "settings.json"
            path.write_text("{incomplete", encoding="utf-8")

            self.assertEqual(load_settings(path), TranslatorSettings())

    def test_old_screen_mode_is_migrated_to_game_direct_read(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "settings.json"
            path.write_text('{"source_mode":"ocr","capture_region":[1,2,30,40]}', encoding="utf-8")

            settings = load_settings(path)

            self.assertFalse(hasattr(settings, "source_mode"))
            self.assertFalse(hasattr(settings, "capture_region"))

    def test_old_custom_service_settings_are_not_used_by_free_backup(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "settings.json"
            path.write_text('{"translation_backend":"service","provider_url":"https://old.test",'
                            '"provider_api_key":"old-secret","provider_model":"old-model"}', encoding="utf-8")

            settings = load_settings(path)

            self.assertEqual(settings.translation_backend, "offline")
            self.assertEqual((settings.provider_url, settings.provider_api_key, settings.provider_model),
                             ("", "", ""))

    def test_settings_round_trip_preserves_translation_controls(self) -> None:
        settings = TranslatorSettings(
            translation_backend="offline", offline_cpu_threads=1, offline_model_path="models/custom",
            chatgpt_model="model-from-account",
            chatgpt_instructions="只译俄语游戏聊天",
            opacity=0.82,
            font_size=18,
            max_rows=80,
            dota_game_dir="games/dota 2 beta/game/dota",
            window_bounds=(-1800, 150, 1100, 760),
        )
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "settings.json"
            save_settings(path, settings)
            self.assertEqual(load_settings(path), settings)

    def test_legacy_settings_load_only_when_new_user_settings_are_absent(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            current, legacy = Path(tmp) / "user.json", Path(tmp) / "old.json"
            save_settings(legacy, TranslatorSettings(font_size=20))
            self.assertEqual(load_settings(current, legacy_path=legacy).font_size, 20)
            self.assertFalse(current.exists())
            save_settings(current, TranslatorSettings(font_size=14))
            self.assertEqual(load_settings(current, legacy_path=legacy).font_size, 14)

    def test_profiles_have_monotonic_request_limits(self) -> None:
        self.assertLess(
            PROFILE_LIMITS["Full"].min_interval_seconds,
            PROFILE_LIMITS["Balanced"].min_interval_seconds,
        )
        self.assertGreater(
            PROFILE_LIMITS["Balanced"].max_chars_per_minute,
            PROFILE_LIMITS["Low"].max_chars_per_minute,
        )


if __name__ == "__main__":
    unittest.main()
