"""Locate bundled resources and writable state without machine-specific paths."""
from __future__ import annotations

import os
import sys
from pathlib import Path, PurePath, PureWindowsPath


def resource_root() -> Path:
    bundled = getattr(sys, "_MEIPASS", None)
    if getattr(sys, "frozen", False) and bundled:
        return Path(bundled)
    return Path(__file__).resolve().parents[2]


def recorded_resource_root() -> PurePath | None:
    """Recognize an old bundled runtime record, without guessing custom paths."""
    record = resource_root() / "config" / "python_runtime.txt"
    try:
        if record.stat().st_size > 131072:
            return None
        text = record.read_text(encoding="utf-16").strip()
    except (OSError, UnicodeError):
        return None
    runtime = PureWindowsPath(text) if PureWindowsPath(text).is_absolute() else Path(text)
    if not runtime.is_absolute() or len(runtime.parts) < 4:
        return None
    folders = tuple(part.casefold() for part in runtime.parts[-3:-1])
    if folders in ((".runtime", "python"), (".venv", "scripts"), (".venv", "bin")):
        if runtime.name.casefold() in ("python.exe", "pythonw.exe", "python", "python3"):
            return runtime.parents[2]
    return None


def user_data_dir() -> Path:
    override = os.environ.get("DOTA2_TRANSLATOR_DATA_DIR")
    if override:
        return Path(os.path.expandvars(override)).expanduser().resolve()
    if sys.platform == "win32":
        base = Path(os.environ["LOCALAPPDATA"]) if os.environ.get("LOCALAPPDATA") else Path.home() / "AppData" / "Local"
    elif sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support"
    else:
        base = Path(os.environ["XDG_DATA_HOME"]) if os.environ.get("XDG_DATA_HOME") else Path.home() / ".local" / "share"
    return base / "Dota2ChatTranslator"


def portable_resource_path(path: str | Path) -> str:
    """Store resources inside the program relative to it; keep external choices."""
    value = Path(path).expanduser()
    value = value if value.is_absolute() else resource_root() / value
    value = value.resolve()
    try:
        return value.relative_to(resource_root().resolve()).as_posix()
    except ValueError:
        return str(value)
