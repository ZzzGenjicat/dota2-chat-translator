import json
import sys
import tempfile
import unittest
import urllib.request
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from dota2_map_assistant.gsi_capture import (
    CONFIG_NAME, GsiChatReader, GsiListener, check_gsi_config,
    gsi_config_text, install_gsi_config,
)


class GsiCaptureTests(unittest.TestCase):
    def test_extracts_chat_in_game_time_order_and_ignores_repeat_posts(self):
        reader = GsiChatReader()
        payload = {"map": {"matchid": "123"}, "events": [
            {"game_time": 12, "event_type": "chat_message", "player_id": 2,
             "channel_type": 11, "message": "иди мид"},
            {"game_time": 10, "event_type": "chat_message", "player_id": 5,
             "channel_type": 12, "message": "слева мясо"},
            {"game_time": 11, "event_type": "kill", "message": "не чат"},
            {"game_time": 13, "event_type": "chat_message", "player_id": 3,
             "channel_type": 11, "message": "push mid"},
        ]}
        raw = json.dumps(payload, ensure_ascii=False)
        self.assertEqual([x.text for x in reader.read(raw)], ["слева мясо", "иди мид"])
        self.assertEqual(reader.read(raw), [])

    def test_new_match_resets_event_identity(self):
        reader = GsiChatReader()
        event = {"game_time": 1, "event_type": "chat_message", "player_id": 1,
                 "channel_type": 11, "message": "привет"}
        first = json.dumps({"map": {"matchid": "1"}, "events": [event]})
        second = json.dumps({"map": {"matchid": "2"}, "events": [event]})
        self.assertEqual(len(reader.read(first)), 1)
        self.assertEqual(len(reader.read(second)), 1)

    def test_equal_game_times_follow_oldest_first_with_newest_first_feed(self):
        reader = GsiChatReader()
        payload = {"events": [
            {"game_time": 10, "event_type": "chat_message", "player_id": 1,
             "channel_type": 11, "message": "вторая строка"},
            {"game_time": 10, "event_type": "chat_message", "player_id": 2,
             "channel_type": 11, "message": "первая строка"},
        ]}
        self.assertEqual([x.text for x in reader.read(json.dumps(payload))],
                         ["первая строка", "вторая строка"])

    def test_rejects_other_json_and_non_chat_events(self):
        reader = GsiChatReader()
        self.assertEqual(reader.read("not json"), [])
        self.assertEqual(reader.read(json.dumps({"events": [{"event_type": "chat_message", "message": "да"}]})), [])
        self.assertEqual(reader.read(json.dumps({"events": [{"event_type": "chat_message", "player_id": 1, "message": "\ue0b8"}]})), [])

    def test_chat_wheel_arrow_is_not_treated_as_typed_russian_chat(self):
        reader = GsiChatReader()
        payload = {"events": [
            {"game_time": 1, "event_type": "chat_message", "player_id": 1, "channel_type": 12,
             "message": "▶ я только за"},
            {"game_time": 2, "event_type": "chat_message", "player_id": 1, "channel_type": 12,
             "message": "я только за"},
            {"game_time": 3, "event_type": "chat_wheel", "player_id": 1, "message": "▶ я только за"},
        ]}
        self.assertEqual([item.text for item in reader.read(json.dumps(payload, ensure_ascii=False))],
                         ["я только за"])

    def test_config_is_local_and_can_install_under_selected_dota_directory(self):
        self.assertIn('"uri" "http://127.0.0.1:47854/"', gsi_config_text())
        with tempfile.TemporaryDirectory() as tmp:
            dota = Path(tmp) / "game" / "dota"
            (dota / "cfg").mkdir(parents=True)
            target = install_gsi_config(dota)
            self.assertEqual(target.parent.name, "gamestate_integration")
            self.assertEqual(target.read_text(encoding="utf-8"), gsi_config_text())

    def test_startup_check_is_read_only_for_missing_and_installed_config(self):
        with tempfile.TemporaryDirectory() as tmp:
            dota = Path(tmp)
            (dota / "cfg").mkdir()
            missing = check_gsi_config(dota)
            self.assertEqual(missing.state, "missing")
            self.assertEqual(missing.path.name, CONFIG_NAME)
            self.assertFalse(missing.path.parent.exists())
            target = install_gsi_config(dota)
            before = target.stat().st_mtime_ns
            with patch.object(Path, "write_text", side_effect=AssertionError("check must not write")):
                self.assertEqual(check_gsi_config(dota).state, "installed")
            self.assertEqual(target.stat().st_mtime_ns, before)

    def test_check_accepts_comments_bom_and_equivalent_keyvalues_formatting(self):
        with tempfile.TemporaryDirectory() as tmp:
            dota = Path(tmp)
            (dota / "cfg").mkdir()
            target = install_gsi_config(dota)
            target.write_text('\ufeff// local chat config\n"Chat" {\n'
                              'uri "http://127.0.0.1:47854/"\n'
                              'data { events "1" map "1" provider "1" }\n}', encoding="utf-8")
            self.assertEqual(check_gsi_config(dota).state, "installed")
            text = gsi_config_text()
            for key in ("uri", "data", "events", "map", "provider"):
                text = text.replace(f'"{key}"', f'"{key.upper()}"')
            target.write_text(text, encoding="utf-8")
            self.assertEqual(check_gsi_config(dota).state, "installed")

    def test_check_rejects_wrong_endpoint_disabled_data_and_malformed_config(self):
        invalid = [
            gsi_config_text(port=1),
            gsi_config_text().replace('"events" "1"', '"events" "0"'),
            gsi_config_text().replace('"provider" "1"', ''),
            gsi_config_text().replace('"map" "1"', ''),
            gsi_config_text().rstrip()[:-1],
            gsi_config_text() + '"extra"',
            gsi_config_text().replace('"data"', '"data'),
            gsi_config_text().replace('"events" "1"', '"events" "1" "events" "0"'),
            gsi_config_text().replace('"provider" "1"', '"" "garbage" "provider" "1"'),
            gsi_config_text().replace('"events" "1"', '"EVENTS" "0" "events" "1"'),
        ]
        with tempfile.TemporaryDirectory() as tmp:
            dota = Path(tmp)
            (dota / "cfg").mkdir()
            target = install_gsi_config(dota)
            for text in invalid:
                with self.subTest(text=text):
                    target.write_text(text, encoding="utf-8")
                    self.assertEqual(check_gsi_config(dota).state, "invalid")
                    self.assertEqual(target.read_text(encoding="utf-8"), text)

    def test_invalid_encoding_is_reported_and_can_be_repaired(self):
        with tempfile.TemporaryDirectory() as tmp:
            dota = Path(tmp)
            (dota / "cfg").mkdir()
            target = install_gsi_config(dota)
            target.write_bytes(b"\xff\xfe\x00broken")
            self.assertEqual(check_gsi_config(dota).state, "invalid")
            install_gsi_config(dota)
            self.assertEqual(check_gsi_config(dota).state, "installed")

    def test_check_distinguishes_unknown_game_and_unreadable_file(self):
        self.assertEqual(check_gsi_config(None).state, "game_not_found")
        with tempfile.TemporaryDirectory() as tmp:
            dota = Path(tmp)
            self.assertEqual(check_gsi_config(dota).state, "game_not_found")
            (dota / "cfg").mkdir()
            install_gsi_config(dota)
            with patch.object(Path, "read_text", side_effect=PermissionError("denied")):
                self.assertEqual(check_gsi_config(dota).state, "unreadable")

    def test_check_rejects_oversized_or_excessively_nested_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            dota = Path(tmp)
            (dota / "cfg").mkdir()
            target = install_gsi_config(dota)
            for text in (' ' * 65537, '"a" {' * 40 + '}' * 40):
                target.write_text(text, encoding="utf-8")
                self.assertEqual(check_gsi_config(dota).state, "invalid")

    def test_local_http_receiver_delivers_each_russian_event_once(self):
        received = []
        listener = GsiListener(lambda messages: received.extend(messages), port=0)
        listener.start()
        try:
            port = listener.server.server_address[1]
            payload = json.dumps({"map": {"matchid": "42"}, "events": [
                {"game_time": 1, "event_type": "chat_message", "player_id": 0,
                 "channel_type": 11, "message": "привет"},
            ]}, ensure_ascii=False).encode("utf-8")
            for _ in range(2):
                request = urllib.request.Request(f"http://127.0.0.1:{port}/", data=payload,
                                                 headers={"Content-Type": "application/json"})
                with urllib.request.urlopen(request, timeout=2) as response:
                    self.assertEqual(response.read(), b"ok")
            self.assertEqual([message.text for message in received], ["привет"])
        finally:
            listener.stop()


if __name__ == "__main__":
    unittest.main()
