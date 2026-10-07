import sys
import ssl
import tempfile
import unittest
from urllib.error import HTTPError, URLError
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from dota2_map_assistant.translation import (
    ChatCompletionTranslationProvider,
    FallbackTranslationProvider,
    GoogleTranslateProvider,
    LocalPassthroughProvider,
    MyMemoryTranslateProvider,
    TranslationCache,
    TranslationQueue,
    UnconfiguredProvider,
)
from dota2_map_assistant.translator_models import TextObservation, TranslationEntry


class RecordingProvider:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []

    def translate(self, text: str, source_language: str) -> str:
        self.calls.append((text, source_language))
        return f"翻译:{text}"


class FakeResponse:
    def __init__(self, payload: str) -> None:
        self.payload = payload.encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, _type, _value, _traceback) -> None:
        return None

    def read(self) -> bytes:
        return self.payload


class TranslationTests(unittest.TestCase):
    def test_network_failure_on_primary_uses_backup(self) -> None:
        class OfflineProvider:
            def translate(self, _text, _language):
                raise URLError("primary unavailable")

        class BackupProvider:
            def translate(self, _text, _language):
                return "左边有目标"

        provider = FallbackTranslationProvider(OfflineProvider(), BackupProvider())
        self.assertEqual(provider.translate("слева мясо", "rus"), "左边有目标")

    def test_chat_model_translation_sends_dota_context_and_only_chat_body(self) -> None:
        requests = []

        def opener(req, timeout, context):
            requests.append((req, timeout, context))
            return FakeResponse('{"choices":[{"message":{"content":"左边有好打的目标"}}]}')

        provider = ChatCompletionTranslationProvider(
            "https://example.test/chat/completions", "dota-model", "secret", opener=opener,
        )
        self.assertEqual(provider.translate("слева мясо", "rus"), "左边有好打的目标")
        req, timeout, context = requests[0]
        self.assertEqual(req.get_header("Authorization"), "Bearer secret")
        self.assertEqual(timeout, 8.0)
        self.assertEqual(context.verify_mode, ssl.CERT_REQUIRED)
        payload = __import__("json").loads(req.data.decode("utf-8"))
        self.assertEqual(payload["model"], "dota-model")
        self.assertEqual(payload["messages"][1]["content"], "слева мясо")
        self.assertIn("Dota 2", payload["messages"][0]["content"])

    def test_chat_model_rejects_empty_content(self) -> None:
        provider = ChatCompletionTranslationProvider(
            "https://example.test/chat/completions", "dota-model", "secret",
            opener=lambda *_args, **_kwargs: FakeResponse('{"choices":[{"message":{"content":""}}]}'),
        )
        with self.assertRaisesRegex(ValueError, "译文"):
            provider.translate("слева мясо", "rus")

    def test_non_chinese_russian_result_tries_backup_translation(self) -> None:
        class LatinProvider:
            def translate(self, _text, _source_language):
                return "network carry"

        class ChineseProvider:
            def translate(self, _text, _source_language):
                return "核心净资产"

        provider = FallbackTranslationProvider(LatinProvider(), ChineseProvider())
        self.assertEqual(provider.translate("керри нетворса", "rus"), "核心净资产")

    def test_two_non_chinese_results_report_failure_instead_of_gibberish(self) -> None:
        class LatinProvider:
            def translate(self, _text, _source_language):
                return "BIE RCC"

        provider = FallbackTranslationProvider(LatinProvider(), LatinProvider())
        with self.assertRaisesRegex(ValueError, "中文"):
            provider.translate("слева мясо", "rus")

    def test_chinese_left_untranslated_tries_russian_backup(self) -> None:
        class ChineseEchoProvider:
            def translate(self, _text, _language):
                return "去中路"

        class RussianProvider:
            def translate(self, _text, _language):
                return "Иди на мид"

        provider = FallbackTranslationProvider(ChineseEchoProvider(), RussianProvider(), target_language="ru")
        self.assertEqual(provider.translate("去中路", "zho"), "Иди на мид")

    def test_google_provider_parses_translation_segments(self) -> None:
        def opener(request, timeout, context):
            self.assertIn("q=hello", request.full_url)
            self.assertEqual(timeout, 8.0)
            self.assertEqual(context.verify_mode, ssl.CERT_REQUIRED)
            return FakeResponse('[[["你好","hello",null,null,1]],null,"en"]')

        provider = GoogleTranslateProvider(opener=opener)

        self.assertEqual(provider.translate("hello", "eng"), "你好")

    def test_google_provider_translates_chinese_into_russian(self) -> None:
        from urllib.parse import parse_qs, urlparse

        def opener(req, timeout, context):
            query = parse_qs(urlparse(req.full_url).query)
            self.assertEqual(query["sl"], ["zh-CN"])
            self.assertEqual(query["tl"], ["ru"])
            return FakeResponse('[[["Иди на мид","去中路",null,null,1]],null,"zh-CN"]')

        provider = GoogleTranslateProvider(target_language="ru", opener=opener)
        self.assertEqual(provider.translate("去中路", "zho"), "Иди на мид")

    def test_mymemory_provider_parses_translation(self) -> None:
        def opener(request, timeout, context):
            self.assertIn("langpair=ru%7Czh-CN", request.full_url)
            return FakeResponse('{"responseStatus":200,"responseData":{"translatedText":"你好"}}')

        provider = MyMemoryTranslateProvider(opener=opener)

        self.assertEqual(provider.translate("Привет", "rus"), "你好")

    def test_mymemory_provider_translates_chinese_into_russian(self) -> None:
        from urllib.parse import parse_qs, urlparse

        def opener(req, timeout, context):
            query = parse_qs(urlparse(req.full_url).query)
            self.assertEqual(query["langpair"], ["zh-CN|ru"])
            return FakeResponse('{"responseStatus":200,"responseData":{"translatedText":"Иди на мид"}}')

        provider = MyMemoryTranslateProvider(target_language="ru", opener=opener)
        self.assertEqual(provider.translate("去中路", "zho"), "Иди на мид")

    def test_429_switches_to_fallback_without_repeating_primary_request(self) -> None:
        class RateLimitedProvider:
            calls = 0

            def translate(self, text, source_language):
                self.calls += 1
                raise HTTPError("https://example.test", 429, "Too Many Requests", {}, None)

        primary = RateLimitedProvider()
        fallback = RecordingProvider()
        provider = FallbackTranslationProvider(primary, fallback, clock=lambda: 0.0)

        self.assertEqual(provider.translate("hello", "eng"), "翻译:hello")
        self.assertEqual(provider.translate("world", "eng"), "翻译:world")
        self.assertEqual(primary.calls, 1)

    def test_free_primary_failure_uses_backup_for_http_and_empty_response(self) -> None:
        class FailingProvider:
            def __init__(self, error):
                self.error = error

            def translate(self, _text, _language):
                raise self.error

        for error in (RuntimeError("没有返回译文"),
                      HTTPError("https://example.test", 403, "Forbidden", {}, None)):
            with self.subTest(error=type(error).__name__):
                provider = FallbackTranslationProvider(FailingProvider(error), RecordingProvider())
                self.assertEqual(provider.translate("привет", "rus"), "翻译:привет")

    def test_failed_fallback_waits_before_another_network_request(self) -> None:
        class RateLimitedProvider:
            calls = 0

            def translate(self, text, source_language):
                self.calls += 1
                raise HTTPError("https://example.test", 429, "Too Many Requests", {}, None)

        current_time = [0.0]
        primary = RateLimitedProvider()
        fallback = RateLimitedProvider()
        provider = FallbackTranslationProvider(primary, fallback, clock=lambda: current_time[0])

        with self.assertRaises(RuntimeError):
            provider.translate("hello", "eng")
        with self.assertRaises(RuntimeError):
            provider.translate("world", "eng")
        self.assertEqual((primary.calls, fallback.calls), (1, 1))

        current_time[0] = 61.0
        with self.assertRaises(RuntimeError):
            provider.translate("again", "eng")
        self.assertEqual((primary.calls, fallback.calls), (1, 2))

    def test_failed_translation_is_not_read_from_cache(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            cache = TranslationCache(Path(tmp) / "cache.json")
            failed = TranslationEntry("eng:hello", "hello", "eng", "", "failed: 429", 1.0)
            cache.put(failed)

            self.assertIsNone(cache.get("eng:hello"))

    def test_unconfigured_provider_reports_missing_translation_service(self) -> None:
        with self.assertRaises(RuntimeError):
            UnconfiguredProvider().translate("hello", "eng")

    def test_cache_round_trip_returns_translated_text(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            cache = TranslationCache(Path(tmp) / "cache.json")
            entry = TranslationEntry("eng:hello", "hello", "eng", "你好", "translated", 1.0)
            cache.put(entry)
            self.assertEqual(cache.get("eng:hello"), entry)

    def test_paused_queue_does_not_call_provider(self) -> None:
        provider = RecordingProvider()
        with tempfile.TemporaryDirectory() as tmp:
            queue = TranslationQueue(
                provider,
                TranslationCache(Path(tmp) / "cache.json"),
                "Balanced",
                paused=True,
                start_worker=False,
            )
            self.assertFalse(queue.submit(TextObservation("hello", "eng", 0.9, 1.0)))
            self.assertEqual(provider.calls, [])
            queue.stop()

    def test_pause_while_waiting_for_budget_cancels_pending_request(self) -> None:
        provider = RecordingProvider()
        with tempfile.TemporaryDirectory() as tmp:
            def pause_during_wait(_seconds: float) -> None:
                queue.set_paused(True)
                queue._stop_event.set()

            queue = TranslationQueue(
                provider, TranslationCache(Path(tmp) / "cache.json"), "Balanced",
                start_worker=False, clock=lambda: 0.0, sleeper=pause_during_wait,
            )
            queue._last_request_at = 0.0
            self.assertTrue(queue.submit(TextObservation("hello", "eng", 0.9, 1.0)))

            queue._run()

            self.assertEqual(provider.calls, [])
            self.assertEqual(queue.poll_result().status, "paused")

    def test_same_observation_is_sent_once(self) -> None:
        provider = RecordingProvider()
        with tempfile.TemporaryDirectory() as tmp:
            queue = TranslationQueue(
                provider,
                TranslationCache(Path(tmp) / "cache.json"),
                "Full",
                start_worker=False,
            )
            item = TextObservation("hello", "eng", 0.9, 1.0)
            self.assertTrue(queue.submit(item))
            self.assertFalse(queue.submit(TextObservation(" hello ", "eng", 0.9, 2.0)))
            queue.stop()

    def test_pending_chat_is_translated_in_screen_order(self) -> None:
        now = [0.0]
        with tempfile.TemporaryDirectory() as tmp:
            provider = RecordingProvider()
            queue = TranslationQueue(
                provider, TranslationCache(Path(tmp) / "cache.json"), "Full",
                start_worker=False, clock=lambda: now[0], sleeper=lambda seconds: now.__setitem__(0, now[0] + seconds),
            )
            queue.submit(TextObservation("older chat", "eng", 0.9, 1.0))
            queue.submit(TextObservation("newer chat", "eng", 0.9, 2.0))

            class StopAfterTwo:
                def translate(self, text, language):
                    result = provider.translate(text, language)
                    if len(provider.calls) == 2:
                        queue._stop_event.set()
                    return result

            queue.provider = StopAfterTwo()
            queue._run()

            self.assertEqual([text for text, _language in provider.calls], ["older chat", "newer chat"])

    def test_clear_cache_invalidates_translation_already_in_progress(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            cache = TranslationCache(Path(tmp) / "cache.json")

            class ClearsDuringRequest:
                def translate(self, _text, _language):
                    queue.clear_memory()
                    queue._stop_event.set()
                    return "你好"

            queue = TranslationQueue(ClearsDuringRequest(), cache, "Full", start_worker=False)
            queue.submit(TextObservation("hello", "eng", 0.9, 1.0))

            queue._run()

            self.assertIsNone(cache.get("eng:hello"))
            self.assertIsNone(queue.poll_result())

    def test_cache_write_error_does_not_lose_translated_text(self) -> None:
        class ReadOnlyCache:
            def get(self, _key):
                return None

            def put(self, _entry):
                raise OSError("read only")

        provider = RecordingProvider()
        queue = TranslationQueue(provider, ReadOnlyCache(), "Full", start_worker=False)
        queue.submit(TextObservation("hello", "eng", 0.9, 1.0))

        class Once:
            def translate(self, text, language):
                result = provider.translate(text, language)
                queue._stop_event.set()
                return result

        queue.provider = Once()
        queue._run()

        self.assertEqual(queue.poll_result().translated_text, "翻译:hello")

    def test_profile_controls_request_delay(self) -> None:
        provider = RecordingProvider()
        with tempfile.TemporaryDirectory() as tmp:
            queue = TranslationQueue(provider, TranslationCache(Path(tmp) / "cache.json"), "Low", start_worker=False)
            self.assertEqual(queue.request_delay_seconds, 8.0)
            queue.set_profile("Full")
            self.assertEqual(queue.request_delay_seconds, 0.8)
            queue.stop()


if __name__ == "__main__":
    unittest.main()
