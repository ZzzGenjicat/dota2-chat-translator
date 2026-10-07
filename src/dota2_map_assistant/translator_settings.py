from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path

@dataclass(frozen=True)
class TranslatorSettings:
    network_profile: str = "Balanced"
    network_paused: bool = False
    provider_url: str = ""
    provider_api_key: str = ""
    provider_model: str = ""
    translation_backend: str = "offline"
    offline_model_path: str = "models/m2m100-418m-ct2-int8"
    offline_cpu_threads: int = 2
    dota_game_dir: str = ""
    chatgpt_model: str = ""
    chatgpt_instructions: str = ""
    always_on_top: bool = True
    opacity: float = 0.96
    font_size: int = 12
    max_rows: int = 100
    window_bounds: tuple[int, int, int, int] | None = None


def load_settings(path: Path, *, legacy_path: Path | None = None) -> TranslatorSettings:
    if not path.exists() and legacy_path is not None:
        path = legacy_path
    if not path.exists():
        return TranslatorSettings()
    try:
        with path.open("r", encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, UnicodeError, json.JSONDecodeError):
        return TranslatorSettings()
    if not isinstance(data, dict):
        return TranslatorSettings()
    return TranslatorSettings(
        network_profile="Balanced",
        network_paused=False,
        provider_url="",
        provider_api_key="",
        provider_model="",
        translation_backend="offline",
        offline_model_path=str(data.get("offline_model_path") or "models/m2m100-418m-ct2-int8"),
        offline_cpu_threads=_clamp_int(data.get("offline_cpu_threads"), 2, 1, 4),
        dota_game_dir=str(data.get("dota_game_dir") or ""),
        chatgpt_model=str(data.get("chatgpt_model") or ""),
        chatgpt_instructions=str(data.get("chatgpt_instructions") or ""),
        always_on_top=bool(data.get("always_on_top", True)),
        opacity=_clamp_float(data.get("opacity"), 0.96, 0.35, 1.0),
        font_size=_clamp_int(data.get("font_size"), 12, 9, 32),
        max_rows=_clamp_int(data.get("max_rows"), 100, 20, 500),
        window_bounds=_parse_window_bounds(data.get("window_bounds")),
    )


def save_settings(path: Path, settings: TranslatorSettings) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8") as fh:
        json.dump(asdict(settings), fh, ensure_ascii=False, indent=2)
    temporary.replace(path)


def _clamp_float(value: object, default: float, lower: float, upper: float) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        parsed = default
    return max(lower, min(upper, parsed))


def _clamp_int(value: object, default: int, lower: int, upper: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        parsed = default
    return max(lower, min(upper, parsed))


def _parse_window_bounds(value: object) -> tuple[int, int, int, int] | None:
    if not isinstance(value, (list, tuple)) or len(value) != 4:
        return None
    try:
        left, top, width, height = (int(item) for item in value)
    except (TypeError, ValueError):
        return None
    if width < 600 or height < 400:
        return None
    return left, top, width, height
