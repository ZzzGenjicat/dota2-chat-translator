import sys
import tempfile
import unittest
import threading
from unittest.mock import patch
from pathlib import Path



ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from dota2_map_assistant.translation import ChatCompletionTranslationProvider, TranslationCache, TranslationQueue
from dota2_map_assistant.chatgpt_plan import ChatGPTPlanProvider
from dota2_map_assistant.translator_app import TranslatorController, _row_from_entry
from dota2_map_assistant.translator_controller import TRANSLATION_CACHE_PATH, _cache_path_for_settings
from dota2_map_assistant.translator_models import TextObservation, TranslationEntry
from dota2_map_assistant.translator_settings import TranslatorSettings
from dota2_map_assistant.sqlite_cache import SQLiteTranslationCache


class RecordingProvider:
    def translate(self, text: str, source_language: str) -> str:
        return f"中文:{text}"


class TranslatorControllerTests(unittest.TestCase):
    def test_distinct_gsi_commands_are_not_merged_as_ocr_variants(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            cache = TranslationCache(Path(tmp) / "cache.json")
            queue = TranslationQueue(RecordingProvider(), cache, start_worker=False)
            controller = TranslatorController(TranslatorSettings(), translation_queue=queue,
                                              translation_cache=cache)
            try:
                controller._receive_gsi_messages([
                    TextObservation("идем все вместе на рошана", "rus", 1.0, 1.0),
                    TextObservation("идем все вместе на базу", "rus", 1.0, 2.0),
                    TextObservation("купите пожалуйста варды", "rus", 1.0, 3.0),
                    TextObservation("купите пожалуйста смоки", "rus", 1.0, 4.0),
                ])
                controller.poll()
                self.assertEqual([row.source_text for row in controller.rows], [
                    "идем все вместе на рошана", "идем все вместе на базу",
                    "купите пожалуйста варды", "купите пожалуйста смоки",
                ])
            finally:
                controller.close()

    def test_offline_feed_uses_only_worker_cache_and_directional_entries(self) -> None:
        main_thread = threading.get_ident()
        reads = []
        writes = []

        class ObservedCache(SQLiteTranslationCache):
            def get(self, key):
                reads.append((key, threading.get_ident()))
                return super().get(key)

            def put(self, entry, expected_generation=None):
                writes.append((entry.key, threading.get_ident()))
                return super().put(entry, expected_generation=expected_generation)

        with tempfile.TemporaryDirectory() as tmp:
            cache = ObservedCache(Path(tmp) / "cache.sqlite3")
            controller = TranslatorController(TranslatorSettings(), translation_cache=cache)
            worker = controller.translation_queue._worker
            try:
                controller.process_observation(TextObservation("ты долбоёб", "rus", 1.0, 1.0))
                entry = controller.translation_queue._results.get(timeout=5)
                self.assertEqual(entry.translated_text, "你是傻逼")
                self.assertTrue(reads)
                self.assertTrue(all(thread != main_thread for _key, thread in reads), reads)
                self.assertEqual([key for key, _thread in writes], ["ru>zh:rus:ты долбоёб"])
            finally:
                controller.close()
                worker.join(timeout=5)

    def test_direct_observation_with_chat_wheel_arrow_never_reaches_translation(self):
        with tempfile.TemporaryDirectory() as tmp:
            cache = TranslationCache(Path(tmp) / "cache.json")
            provider = RecordingProvider()
            queue = TranslationQueue(provider, cache, start_worker=False)
            controller = TranslatorController(TranslatorSettings(), translation_queue=queue,
                                              translation_cache=cache)
            controller.process_observation(TextObservation("▶ я только за", "rus", 0.9, 1.0))
            self.assertEqual(controller.rows, [])
            self.assertEqual(queue._pending, set())
            queue.stop()





    def test_gsi_mode_starts_without_a_screen_region(self) -> None:
        class FakeListener:
            started = False
            stopped = False

            def __init__(self, on_messages):
                self.on_messages = on_messages

            def start(self):
                self.started = True

            def stop(self):
                self.stopped = True

        with tempfile.TemporaryDirectory() as tmp:
            cache = TranslationCache(Path(tmp) / "cache.json")
            queue = TranslationQueue(RecordingProvider(), cache, "Balanced", start_worker=False)
            controller = TranslatorController(TranslatorSettings(),
                                              translation_queue=queue, translation_cache=cache)
            with patch("dota2_map_assistant.translator_controller.GsiListener", FakeListener):
                controller.start()
                listener = controller._gsi_listener
                listener.on_messages([TextObservation("иди мид", "rus", 1.0, 1.0)])
                controller.poll()
                controller.stop()
            self.assertTrue(listener.started)
            self.assertTrue(listener.stopped)
            self.assertEqual([row.source_text for row in controller.rows], ["иди мид"])
            queue.stop()

    def test_changing_provider_requeues_pending_messages(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            cache = TranslationCache(Path(tmp) / "cache.json")
            queue = TranslationQueue(RecordingProvider(), cache, "Balanced", start_worker=False)
            controller = TranslatorController(
                TranslatorSettings(), translation_queue=queue, translation_cache=cache,
            )
            controller.process_observation(TextObservation("слева мясо", "rus", 0.9, 1.0))
            controller.set_offline_provider("models/m2m100-418m-ct2-int8", 1)
            self.assertIn("rus:слева мясо", queue._pending)
            self.assertEqual(controller.rows[0].status, "queued")
            queue.stop()





    def test_live_feed_only_accepts_russian_observations(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            cache = TranslationCache(Path(tmp) / "cache.json")
            queue = TranslationQueue(RecordingProvider(), cache, "Balanced", start_worker=False)
            controller = TranslatorController(
                TranslatorSettings(), translation_queue=queue, translation_cache=cache,
            )
            controller._observation_events.put(TextObservation("push mid", "eng", 0.95, 1.0))
            controller._observation_events.put(TextObservation("иди мид", "rus", 0.95, 1.0))

            controller.poll()

            self.assertEqual([row.source_text for row in controller.rows], ["иди мид"])
            queue.stop()

    def test_identical_message_is_not_shown_again_after_visible_rows_trim(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            cache = TranslationCache(Path(tmp) / "cache.json")
            queue = TranslationQueue(RecordingProvider(), cache, "Balanced", start_worker=False)
            controller = TranslatorController(
                TranslatorSettings(max_rows=20), translation_queue=queue, translation_cache=cache,
            )
            for index in range(21):
                controller.process_observation(TextObservation(f"message {index}", "eng", 0.9, float(index)))
            controller.process_observation(TextObservation("message 0", "eng", 0.9, 30.0))

            self.assertEqual(len(controller.rows), 20)
            self.assertEqual(controller.rows[0].source_text, "message 1")
            queue.stop()

    def test_clearing_cache_requeues_visible_pending_text(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            cache = TranslationCache(Path(tmp) / "cache.json")
            queue = TranslationQueue(RecordingProvider(), cache, "Balanced", start_worker=False)
            controller = TranslatorController(
                TranslatorSettings(), translation_queue=queue, translation_cache=cache,
            )
            controller.process_observation(TextObservation("hello team", "eng", 0.9, 1.0))

            controller.clear_cache()

            self.assertEqual(controller.rows[0].status, "queued")
            self.assertEqual(queue._jobs.qsize(), 2)
            queue.stop()

    def test_resuming_network_queues_paused_visible_text(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            cache = TranslationCache(Path(tmp) / "cache.json")
            queue = TranslationQueue(RecordingProvider(), cache, "Balanced", paused=True, start_worker=False)
            controller = TranslatorController(
                TranslatorSettings(network_paused=True), translation_queue=queue, translation_cache=cache,
            )
            controller.process_observation(TextObservation("hello team", "eng", 0.9, 1.0))

            controller.set_network_paused(False)

            self.assertEqual(controller.rows[0].status, "queued")
            self.assertEqual(queue._jobs.qsize(), 1)
            queue.stop()

    def test_cached_translation_uses_current_observation_time(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            cache = TranslationCache(Path(tmp) / "cache.json")
            cache.put(TranslationEntry("eng:hello", "hello", "eng", "你好", "translated", 1.0))
            queue = TranslationQueue(RecordingProvider(), cache, "Balanced", start_worker=False)
            controller = TranslatorController(
                TranslatorSettings(), translation_queue=queue, translation_cache=cache,
            )

            controller.process_observation(TextObservation("hello", "eng", 0.9, 20.0))

            self.assertEqual(controller.rows[0].observed_at, 20.0)
            queue.stop()

    def test_unchanged_foreign_text_is_not_displayed_as_chinese_translation(self) -> None:
        entry = TranslationEntry("eng:hello", "hello", "eng", "hello", "translated", 1.0)

        row = _row_from_entry(entry)

        self.assertEqual(row.translated_text, "")
        self.assertEqual(row.status, "unchanged")

    def test_source_is_visible_before_translation_result(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            cache = TranslationCache(Path(tmp) / "cache.json")
            queue = TranslationQueue(
                RecordingProvider(),
                cache,
                "Balanced",
                start_worker=False,
            )
            controller = TranslatorController(
                TranslatorSettings(),
                translation_queue=queue,
                translation_cache=cache,
            )

            controller.process_observation(TextObservation("Hola", "spa", 0.9, 1.0))

            self.assertEqual(controller.rows[-1].source_text, "Hola")
            self.assertIn(controller.rows[-1].status, {"queued", "untranslated", "paused"})
            queue.stop()

    def test_paused_source_can_be_queued_after_unpausing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            cache = TranslationCache(Path(tmp) / "cache.json")
            queue = TranslationQueue(
                RecordingProvider(),
                cache,
                "Balanced",
                paused=True,
                start_worker=False,
            )
            controller = TranslatorController(
                TranslatorSettings(network_paused=True),
                translation_queue=queue,
                translation_cache=cache,
            )
            observation = TextObservation("Hola", "spa", 0.9, 1.0)

            controller.process_observation(observation)
            controller.set_network_paused(False)
            controller.process_observation(TextObservation("Hola", "spa", 0.9, 2.0))

            self.assertEqual(controller.rows[-1].status, "queued")
            queue.stop()

    def test_near_duplicate_chat_line_does_not_create_another_row(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            cache = TranslationCache(Path(tmp) / "cache.json")
            queue = TranslationQueue(RecordingProvider(), cache, "Balanced", start_worker=False)
            controller = TranslatorController(
                TranslatorSettings(),
                translation_queue=queue,
                translation_cache=cache,
            )

            controller.process_observation(TextObservation("wy EX : Вы, стайка поросят, идите домой", "rus", 0.8, 1.0))
            controller.process_observation(TextObservation("Вы, стайка поросят, идите домой", "rus", 0.9, 2.0))

            self.assertEqual(len(controller.rows), 1)
            queue.stop()

    def test_standalone_dota_term_is_translated_without_network(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            cache = TranslationCache(Path(tmp) / "cache.json")
            queue = TranslationQueue(RecordingProvider(), cache, "Balanced", start_worker=False)
            controller = TranslatorController(
                TranslatorSettings(),
                translation_queue=queue,
                translation_cache=cache,
            )

            controller.process_observation(TextObservation("DS", "eng", 0.9, 1.0))

            self.assertEqual(controller.rows[-1].translated_text, "黑暗贤者")
            self.assertEqual(controller.rows[-1].status, "translated")
            self.assertEqual(queue._jobs.qsize(), 0)
            queue.stop()


if __name__ == "__main__":
    unittest.main()
