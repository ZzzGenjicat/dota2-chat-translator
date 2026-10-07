import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from dota2_map_assistant.steam_discovery import (
    find_dota_dir, normalize_dota_dir, steam_roots,
)


def make_game(library: Path, name="dota 2 beta") -> Path:
    dota = library / "steamapps" / "common" / name / "game" / "dota"
    (dota / "cfg").mkdir(parents=True)
    (dota / "gameinfo.gi").write_text('"GameInfo" {}', encoding="utf-8")
    return dota.resolve()


class SteamDiscoveryTests(unittest.TestCase):
    def test_reads_custom_steam_libraries_with_spaces_unicode_and_escaped_paths(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            steam = base / "Steam Client"
            custom = base / "其他硬盘" / "自定义游戏库"
            dota = make_game(custom)
            (steam / "steamapps").mkdir(parents=True)
            escaped = str(custom).replace("\\", "\\\\")
            (steam / "steamapps" / "libraryfolders.vdf").write_text(
                '\ufeff"libraryfolders" { "0" { "path" "' + escaped + '" "apps" { "570" "123" } } }', encoding="utf-8")
            self.assertEqual(find_dota_dir(roots=[steam]), dota)

    def test_legacy_vdf_and_config_location_are_supported(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            steam, other = base / "Steam", base / "Games"
            dota = make_game(other)
            (steam / "config").mkdir(parents=True)
            escaped = str(other).replace("\\", "\\\\")
            (steam / "config" / "libraryfolders.vdf").write_text(
                '"LibraryFolders" { "TimeNextStatsReport" "123" "1" "' + escaped + '" }', encoding="utf-8")
            self.assertEqual(find_dota_dir(roots=[steam]), dota)

    def test_game_manifest_can_name_nondefault_install_directory(self):
        with tempfile.TemporaryDirectory() as tmp:
            library = Path(tmp)
            dota = make_game(library, "Dota自定义名称")
            (library / "steamapps" / "appmanifest_570.acf").write_text(
                '"AppState" { "appid" "570" "installdir" "Dota自定义名称" }', encoding="utf-8")
            self.assertEqual(find_dota_dir(roots=[library]), dota)

    def test_stale_saved_game_path_falls_back_to_current_libraries(self):
        with tempfile.TemporaryDirectory() as tmp:
            library = Path(tmp)
            dota = make_game(library)
            self.assertEqual(find_dota_dir(library / "old-dota", roots=[library]), dota)
            self.assertEqual(find_dota_dir(dota, roots=[]), dota)

    def test_leftover_user_config_does_not_make_uninstalled_game_valid(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            old = base / "old-game" / "game" / "dota"
            (old / "cfg" / "gamestate_integration").mkdir(parents=True)
            (old / "cfg" / "gamestate_integration" / "translator.cfg").write_text("leftover", encoding="utf-8")
            current = make_game(base / "current-library")
            self.assertIsNone(normalize_dota_dir(old))
            self.assertEqual(find_dota_dir(old, roots=[base / "current-library"]), current)

    def test_manual_selection_accepts_install_root_game_folder_or_dota_folder(self):
        with tempfile.TemporaryDirectory() as tmp:
            dota = make_game(Path(tmp))
            for selected in (dota, dota.parent, dota.parents[1], dota / "cfg"):
                self.assertEqual(normalize_dota_dir(selected), dota)
            self.assertIsNone(normalize_dota_dir(Path(tmp) / "unknown"))
            unrelated = Path(tmp) / "another-game"
            (unrelated / "cfg").mkdir(parents=True)
            self.assertIsNone(normalize_dota_dir(unrelated))

    def test_bad_library_file_does_not_hide_a_valid_default_install(self):
        with tempfile.TemporaryDirectory() as tmp:
            library = Path(tmp)
            dota = make_game(library)
            vdf = library / "steamapps" / "libraryfolders.vdf"
            for invalid in (b"\xff\xfe", b'"broken" {', b" " * 1048577):
                vdf.write_bytes(invalid)
                self.assertEqual(find_dota_dir(roots=[library]), dota)

    def test_manifest_cannot_escape_the_steam_common_directory(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            library = base / "Steam"
            (library / "steamapps").mkdir(parents=True)
            outside = base / "escaped" / "game" / "dota"
            (outside / "cfg").mkdir(parents=True)
            (library / "steamapps" / "appmanifest_570.acf").write_text(
                '"AppState" { "appid" "570" "installdir" "../../escaped" }', encoding="utf-8")
            self.assertIsNone(find_dota_dir(roots=[library]))

    def test_platform_roots_use_home_environment_or_registry(self):
        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ, {}, clear=True):
            home = Path(tmp)
            with patch.object(Path, "home", return_value=home):
                mac = steam_roots(platform="darwin")
                self.assertIn(home / "Library" / "Application Support" / "Steam", mac)
                linux = steam_roots(platform="linux")
                self.assertIn(home / ".local" / "share" / "Steam", linux)
            with patch.dict(os.environ, {"ProgramFiles(x86)": str(home / "apps"), "LOCALAPPDATA": str(home / "user-local")}), patch("dota2_map_assistant.steam_discovery._windows_registry_roots", return_value=[home / "custom-client"]):
                windows = steam_roots(platform="win32")
                self.assertEqual(windows[0], home / "custom-client")
                self.assertIn(home / "apps" / "Steam", windows)
                self.assertIn(home / "user-local" / "Steam", windows)
