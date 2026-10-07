import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from dota2_map_assistant.app_paths import resource_root, user_data_dir, portable_resource_path, recorded_resource_root
from dota2_map_assistant.offline_translation import cache_path, resolve_model_path
from dota2_map_assistant.chat_lexicon import ChatLexicon


class AppPathsTests(unittest.TestCase):
    def test_resources_follow_source_or_packaged_application_not_working_directory(self):
        self.assertEqual(resource_root(), ROOT)
        with patch.object(sys, "frozen", True, create=True), patch.object(sys, "_MEIPASS", str(ROOT / "bundle"), create=True):
            self.assertEqual(resource_root(), ROOT / "bundle")

    def test_user_data_uses_current_user_and_platform(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            with patch.object(Path, "home", return_value=home), patch.dict(os.environ, {}, clear=True):
                for platform, expected in (
                    ("win32", home / "AppData" / "Local" / "Dota2ChatTranslator"),
                    ("darwin", home / "Library" / "Application Support" / "Dota2ChatTranslator"),
                    ("linux", home / ".local" / "share" / "Dota2ChatTranslator"),
                ):
                    with self.subTest(platform=platform), patch.object(sys, "platform", platform):
                        self.assertEqual(user_data_dir(), expected)
                        self.assertFalse(expected.exists())

    def test_explicit_data_directory_and_platform_environment_locations(self):
        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ, {}, clear=True):
            base = Path(tmp)
            with patch.object(sys, "platform", "win32"), patch.dict(os.environ, {"LOCALAPPDATA": str(base)}):
                self.assertEqual(user_data_dir(), base / "Dota2ChatTranslator")
            with patch.object(sys, "platform", "linux"), patch.dict(os.environ, {"XDG_DATA_HOME": str(base)}):
                self.assertEqual(user_data_dir(), base / "Dota2ChatTranslator")
            with patch.dict(os.environ, {"DOTA2_TRANSLATOR_DATA_DIR": str(base / "portable")}):
                self.assertEqual(user_data_dir(), (base / "portable").resolve())

    def test_model_inside_program_is_saved_relative_and_external_model_stays_explicit(self):
        model = ROOT / "models" / "custom"
        self.assertEqual(portable_resource_path(model), "models/custom")
        self.assertEqual(resolve_model_path("models/custom"), model)
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(portable_resource_path(Path(tmp)), str(Path(tmp).resolve()))

    def test_old_absolute_default_model_path_recovers_after_program_moves(self):
        with tempfile.TemporaryDirectory() as tmp:
            old = Path(tmp) / "old-location" / "models" / "m2m100-418m-ct2-int8"
            bundled = Path(tmp) / 'new-location' / 'models' / 'm2m100-418m-ct2-int8'
            bundled.mkdir(parents=True)
            with patch("dota2_map_assistant.offline_translation.recorded_resource_root", return_value=old.parents[1]), \
                 patch('dota2_map_assistant.offline_translation.DEFAULT_MODEL_PATH', bundled):
                self.assertEqual(resolve_model_path(str(old)), bundled)
            custom = Path(tmp) / "missing-custom-model"
            self.assertEqual(resolve_model_path(str(custom)), custom)

    def test_missing_external_model_with_default_name_is_not_silently_substituted(self):
        with tempfile.TemporaryDirectory() as tmp:
            for parent in ("external-models", "models"):
                custom = Path(tmp) / parent / "m2m100-418m-ct2-int8"
                self.assertEqual(resolve_model_path(str(custom)), custom)

    def test_legacy_runtime_record_identifies_bundled_root_but_not_external_python(self):
        with tempfile.TemporaryDirectory() as tmp:
            app = Path(tmp) / "new-program"
            (app / "config").mkdir(parents=True)
            record = app / "config" / "python_runtime.txt"
            old = Path(tmp) / "old-program"
            with patch("dota2_map_assistant.app_paths.resource_root", return_value=app):
                record.write_text(str(old / ".runtime" / "python" / "python.exe"), encoding="utf-16")
                self.assertEqual(recorded_resource_root(), old)
                record.write_text(str(old / "global-python" / "python.exe"), encoding="utf-16")
                self.assertIsNone(recorded_resource_root())
                record.write_text(".runtime/python/python.exe", encoding="utf-16")
                self.assertIsNone(recorded_resource_root())

    def test_foreign_windows_default_model_can_recover_using_its_bundled_record(self):
        with tempfile.TemporaryDirectory() as tmp:
            app = Path(tmp)
            (app / "config").mkdir()
            (app / "config" / "python_runtime.txt").write_text(
                r"R:\old-program\.runtime\python\python.exe", encoding="utf-16")
            bundled = app / 'models' / 'm2m100-418m-ct2-int8'
            bundled.mkdir(parents=True)
            with patch("dota2_map_assistant.app_paths.resource_root", return_value=app), \
                 patch('dota2_map_assistant.offline_translation.DEFAULT_MODEL_PATH', bundled):
                self.assertEqual(resolve_model_path(r"R:\old-program\models\m2m100-418m-ct2-int8"),
                                 bundled)

    def test_cache_is_writable_user_data_instead_of_program_resources(self):
        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ, {"DOTA2_TRANSLATOR_DATA_DIR": tmp}):
            cache = cache_path(ROOT / "models" / "m2m100-418m-ct2-int8", ChatLexicon())
            self.assertEqual(cache.parent, Path(tmp).resolve() / "cache")
            self.assertFalse(cache.exists())

    def test_bundled_model_cache_survives_moving_the_program(self):
        with tempfile.TemporaryDirectory() as tmp:
            roots = [Path(tmp) / "old-app", Path(tmp) / "new-app"]
            names = []
            for root in roots:
                model = root / "models" / "m2m100-418m-ct2-int8"
                model.mkdir(parents=True)
                for filename in ("model.bin", "config.json", "shared_vocabulary.txt", "spm.128k.model"):
                    path = model / filename
                    path.write_bytes(b"same model fixture")
                    os.utime(path, ns=(1000000000, 1000000000))
                with patch("dota2_map_assistant.offline_translation.APP_ROOT", root):
                    names.append(cache_path(model, ChatLexicon()).name)
            self.assertEqual(names[0], names[1])
