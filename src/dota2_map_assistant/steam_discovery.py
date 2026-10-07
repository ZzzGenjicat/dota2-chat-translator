"""Discover Steam libraries on each platform, including custom library paths."""
from __future__ import annotations

import os
import sys
from pathlib import Path

from .valve_keyvalues import parse_keyvalues


def _windows_registry_roots() -> list[Path]:
    try:
        import winreg
    except ImportError:
        return []
    roots = []
    for hive, key in (
        (winreg.HKEY_CURRENT_USER, r"Software\Valve\Steam"),
        (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\Valve\Steam"),
        (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Valve\Steam"),
    ):
        try:
            with winreg.OpenKey(hive, key) as handle:
                for value in ("SteamPath", "InstallPath"):
                    try:
                        path = winreg.QueryValueEx(handle, value)[0]
                        if isinstance(path, str) and path.strip():
                            roots.append(Path(path))
                    except OSError:
                        pass
        except OSError:
            pass
    return roots


def steam_roots(*, platform: str | None = None) -> list[Path]:
    platform = platform or sys.platform
    roots = []
    override = os.environ.get("DOTA2_TRANSLATOR_STEAM_DIR")
    if override:
        roots.append(Path(override).expanduser())
    if platform == "win32":
        roots.extend(_windows_registry_roots())
        for name in ("ProgramFiles(x86)", "ProgramFiles", "LOCALAPPDATA"):
            if os.environ.get(name):
                roots.append(Path(os.environ[name]) / "Steam")
        # No drive sweep: Steam records external/custom libraries in its VDF.
    elif platform == "darwin":
        roots.append(Path.home() / "Library" / "Application Support" / "Steam")
    else:
        home = Path.home()
        roots.extend((home / ".steam" / "steam", home / ".steam" / "root",
                      home / ".local" / "share" / "Steam",
                      home / ".var" / "app" / "com.valvesoftware.Steam" / ".local" / "share" / "Steam"))
    return list(dict.fromkeys(roots))


def normalize_dota_dir(path: str | Path) -> Path | None:
    selected = Path(path).expanduser()
    candidates = [selected, selected / "game" / "dota", selected / "dota"]
    if selected.name.casefold() == "cfg":
        candidates.insert(0, selected.parent)
    for candidate in candidates:
        try:
            shipped = (candidate / "gameinfo.gi").is_file() or (candidate / "pak01_dir.vpk").is_file()
            if candidate.name.casefold() == "dota" and (candidate / "cfg").is_dir() and shipped:
                return candidate.resolve()
        except OSError:
            continue
    return None


def _read_config(path: Path) -> dict:
    try:
        if path.stat().st_size > 1048576:
            return {}
        return parse_keyvalues(path.read_text(encoding="utf-8-sig"))
    except (OSError, UnicodeError, ValueError):
        return {}


def _library_paths(steam: Path) -> list[Path]:
    paths = []
    for folder in ("steamapps", "config"):
        libraries = _read_config(steam / folder / "libraryfolders.vdf").get("libraryfolders", {})
        if not isinstance(libraries, dict):
            continue
        for key, value in libraries.items():
            if not key.isdigit():
                continue
            path = value.get("path") if isinstance(value, dict) else value
            if isinstance(path, str) and path.strip():
                candidate = Path(path).expanduser()
                if candidate.is_absolute():
                    paths.append(candidate)
    return paths


def _game_in_library(library: Path) -> Path | None:
    manifest = _read_config(library / "steamapps" / "appmanifest_570.acf").get("appstate", {})
    names = ["dota 2 beta"]
    if isinstance(manifest, dict) and manifest.get("appid") == "570":
        name = manifest.get("installdir")
        if isinstance(name, str) and name not in ("", ".", "..") and not any(ch in name for ch in '/\\:'):
            names.insert(0, name)
    for name in dict.fromkeys(names):
        dota = normalize_dota_dir(library / "steamapps" / "common" / name)
        if dota is not None:
            return dota
    return None


def find_dota_dir(preferred: str | Path | None = None, *, roots: list[Path] | None = None) -> Path | None:
    """Prefer a verified saved selection; otherwise follow Steam's libraries."""
    preferred = preferred or os.environ.get("DOTA2_TRANSLATOR_DOTA_DIR")
    if preferred:
        dota = normalize_dota_dir(preferred)
        if dota is not None:
            return dota
    steam = list(roots) if roots is not None else steam_roots()
    libraries = list(steam)
    for root in steam:
        libraries.extend(_library_paths(root))
    for library in dict.fromkeys(libraries):
        dota = _game_in_library(library)
        if dota is not None:
            return dota
    return None
