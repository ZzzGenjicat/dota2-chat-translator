"""Receive Dota 2 chat from Game State Integration on this PC."""

from __future__ import annotations

import json
import re
import threading
import time
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Callable, Literal

from .translator_models import TextObservation
from .text_recognition import is_chat_wheel_message
from .steam_discovery import find_dota_dir
from .valve_keyvalues import parse_keyvalues as _parse_keyvalues

GSI_PORT = 47854
CONFIG_NAME = "gamestate_integration_dota2_chat_translator.cfg"
_CYRILLIC = re.compile(r"[\u0400-\u04ff]")
_EMOTICONS = re.compile(r"[\ue000-\uf8ff]")


@dataclass(frozen=True)
class GsiConfigCheck:
    state: Literal["installed", "missing", "invalid", "unreadable", "game_not_found"]
    dota_dir: Path | None = None
    path: Path | None = None


def check_gsi_config(dota_dir: Path | None, port: int = GSI_PORT) -> GsiConfigCheck:
    """Inspect the chat config without creating or rewriting any game files."""
    if dota_dir is None:
        return GsiConfigCheck("game_not_found")
    dota_dir = Path(dota_dir)
    target = dota_dir / "cfg" / "gamestate_integration" / CONFIG_NAME
    try:
        if not (dota_dir / "cfg").is_dir():
            return GsiConfigCheck("game_not_found")
        if target.stat().st_size > 65536:
            return GsiConfigCheck("invalid", dota_dir, target)
        text = target.read_text(encoding="utf-8-sig")
    except FileNotFoundError:
        return GsiConfigCheck("missing", dota_dir, target)
    except UnicodeError:
        return GsiConfigCheck("invalid", dota_dir, target)
    except OSError:
        return GsiConfigCheck("unreadable", dota_dir, target)
    try:
        parsed = _parse_keyvalues(text)
        config = next(iter(parsed.values())) if len(parsed) == 1 else None
        data = config.get("data") if isinstance(config, dict) else None
        valid = (isinstance(data, dict)
                 and config.get("uri") == f"http://127.0.0.1:{port}/"
                 and all(data.get(key) == "1" for key in ("provider", "map", "events")))
    except ValueError:
        valid = False
    return GsiConfigCheck("installed" if valid else "invalid", dota_dir, target)


def gsi_config_text(port: int = GSI_PORT) -> str:
    """Valve KeyValues config: send only the fields needed for chat."""
    return "\n".join((
        '"Dota 2 Chat Translator"', "{",
        f'    "uri" "http://127.0.0.1:{port}/"',
        '    "timeout" "5.0"', '    "buffer" "0.1"',
        '    "throttle" "0.1"', '    "heartbeat" "10.0"',
        '    "data"', '    {',
        '        "provider" "1"', '        "map" "1"',
        '        "events" "1"', '    }', '}', '',
    ))


def install_gsi_config(dota_dir: Path, port: int = GSI_PORT) -> Path:
    dota_dir = Path(dota_dir)
    if not (dota_dir / "cfg").is_dir():
        raise FileNotFoundError(f"不是 Dota 2 游戏目录：{dota_dir}")
    target = dota_dir / "cfg" / "gamestate_integration" / CONFIG_NAME
    target.parent.mkdir(parents=True, exist_ok=True)
    wanted = gsi_config_text(port)
    try:
        existing = target.read_text(encoding="utf-8-sig")
    except (FileNotFoundError, UnicodeError):
        existing = None
    if existing != wanted:
        target.write_text(wanted, encoding="utf-8")
    return target


class GsiChatReader:
    """Read repeated GSI snapshots into unique chronological Russian messages."""

    def __init__(self) -> None:
        self._matchid: str | None = None
        self._seen: set[tuple[object, ...]] = set()

    def read(self, body: bytes | str) -> list[TextObservation]:
        try:
            data = json.loads(body)
        except (UnicodeError, ValueError, TypeError):
            return []
        if not isinstance(data, dict):
            return []
        match = data.get("map")
        matchid = str(match.get("matchid", "")) if isinstance(match, dict) else ""
        if self._matchid is None:
            self._matchid = matchid
        elif matchid and matchid != self._matchid:
            self._seen.clear()
            self._matchid = matchid
        events = data.get("events")
        if not isinstance(events, list):
            return []
        fresh: list[tuple[float, int, str]] = []
        for event in events:
            if not isinstance(event, dict) or event.get("event_type") != "chat_message":
                continue
            text = event.get("message")
            slot = event.get("player_id")
            if not isinstance(text, str) or not isinstance(slot, int) or isinstance(slot, bool):
                continue
            text = _EMOTICONS.sub("", text).strip()
            if is_chat_wheel_message(text):
                continue
            game_time = event.get("game_time")
            try:
                game_time = float(game_time)
            except (TypeError, ValueError):
                game_time = 0.0
            key = (game_time, slot, event.get("channel_type"), text)
            if key in self._seen:
                continue
            self._seen.add(key)
            if text and _CYRILLIC.search(text):
                # GSI lists newest first; reverse ties within the same game second.
                fresh.append((game_time, -len(fresh), text))
        if len(self._seen) > 5000:
            self._seen = set(list(self._seen)[-2000:])
        fresh.sort(key=lambda item: (item[0], item[1]))
        now = time.time()
        return [TextObservation(text, "rus", 1.0, now + index * 0.000001)
                for index, (_, _, text) in enumerate(fresh)]


class GsiListener:
    """Small loopback HTTP receiver with a bounded request body."""

    def __init__(self, on_messages: Callable[[list[TextObservation]], None], port: int = GSI_PORT) -> None:
        self._on_messages = on_messages
        self.port = port
        self.reader = GsiChatReader()
        self._reader_lock = threading.Lock()
        self.server: ThreadingHTTPServer | None = None
        self.thread: threading.Thread | None = None

    def start(self) -> None:
        if self.server is not None:
            return
        owner = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self) -> None:
                try:
                    length = int(self.headers.get("Content-Length", "0"))
                except ValueError:
                    length = 0
                if length <= 0 or length > 4 * 1024 * 1024:
                    self.send_error(413)
                    return
                body = self.rfile.read(length)
                with owner._reader_lock:
                    messages = owner.reader.read(body)
                if messages:
                    owner._on_messages(messages)
                self.send_response(200)
                self.end_headers()
                self.wfile.write(b"ok")

            def log_message(self, _format: str, *_args: object) -> None:
                pass

        self.server = ThreadingHTTPServer(("127.0.0.1", self.port), Handler)
        self.server.daemon_threads = True
        self.thread = threading.Thread(target=self.server.serve_forever, name="dota-gsi-listener", daemon=True)
        self.thread.start()

    def stop(self) -> None:
        if self.server is None:
            return
        self.server.shutdown()
        self.server.server_close()
        self.server = None
        if self.thread is not None:
            self.thread.join(timeout=1)
            self.thread = None
