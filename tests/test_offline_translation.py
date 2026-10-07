import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))


class OfflineTranslationTests(unittest.TestCase):
    def engine(self, root):
        from dota2_map_assistant.offline_translation import M2M100Engine
        return M2M100Engine(root)

    def test_missing_model_never_attempts_network(self):
        with tempfile.TemporaryDirectory() as tmp, patch('urllib.request.urlopen', side_effect=AssertionError('network')):
            engine = self.engine(Path(tmp))
            with self.assertRaisesRegex(RuntimeError, '模型'):
                engine.translate_segments(['测试消息'], 'zh', 'ru')

    def test_language_prefix_and_low_resource_inference(self):
        with tempfile.TemporaryDirectory() as tmp:
            engine = self.engine(Path(tmp))
            calls = []
            class Tokenizer:
                def encode(self, text, out_type): return [text]
                def decode(self, tokens): return ''.join(tokens)
            class Translator:
                def translate_batch(self, sources, **kwargs):
                    calls.append((sources, kwargs))
                    return [SimpleNamespace(hypotheses=[['__ru__', 'готово', '</s>']])]
            engine._translator = Translator()
            engine._tokenizer = Tokenizer()
            self.assertEqual(engine.translate_segments(['测试'], 'zh', 'ru'), ['готово'])
            self.assertEqual(calls[0][0], [['__zh__', '测试', '</s>']])
            self.assertEqual(calls[0][1]['target_prefix'], [['__ru__']])
            self.assertEqual(calls[0][1]['beam_size'], 1)
            self.assertFalse(calls[0][1]['return_scores'])

    def test_long_input_is_rejected_without_silent_truncation(self):
        with tempfile.TemporaryDirectory() as tmp:
            engine = self.engine(Path(tmp))
            engine._tokenizer = SimpleNamespace(encode=lambda text, out_type: ['x'] * 193)
            engine._translator = SimpleNamespace(translate_batch=lambda *args, **kwargs: self.fail('must reject before inference'))
            with self.assertRaisesRegex(ValueError, '过长'):
                engine.translate_segments(['长消息'], 'zh', 'ru')

    def test_directions_serialize_inference_on_one_engine(self):
        with tempfile.TemporaryDirectory() as tmp:
            engine = self.engine(Path(tmp))
            active = 0
            peak = 0
            class Translator:
                def translate_batch(self, sources, **kwargs):
                    nonlocal active, peak
                    active += 1
                    peak = max(peak, active)
                    time.sleep(.03)
                    active -= 1
                    return [SimpleNamespace(hypotheses=[[kwargs['target_prefix'][0][0], 'ok']])]
            engine._translator = Translator()
            engine._tokenizer = SimpleNamespace(encode=lambda text, out_type: [text], decode=lambda tokens: ''.join(tokens))
            threads = [threading.Thread(target=engine.translate_segments, args=(['text'], 'zh', 'ru')) for _ in range(3)]
            for thread in threads: thread.start()
            for thread in threads: thread.join()
            self.assertEqual(peak, 1)

    def test_idle_release_does_not_reload_or_hold_weights(self):
        with tempfile.TemporaryDirectory() as tmp:
            engine = self.engine(Path(tmp))
            engine._translator = object()
            engine._tokenizer = object()
            engine._last_used = time.monotonic() - 181
            engine.release_if_idle()
            self.assertIsNone(engine._translator)

    def test_phrase_strength_and_neutral_chat_without_model(self):
        from dota2_map_assistant.chat_lexicon import ChatLexicon
        lexicon = ChatLexicon()
        self.assertEqual(lexicon.exact('你是傻逼', 'zh'), 'ты долбоёб')
        self.assertEqual(lexicon.exact('你是白痴', 'zh'), 'ты идиот')
        self.assertEqual(lexicon.exact('别送了', 'zh'), 'не фидь')
        self.assertEqual(lexicon.exact('иди нахуй', 'ru'), '滚你妈的')
        self.assertIsNone(lexicon.exact('这句不在词库中', 'zh'))

    def test_swears_are_protected_in_mixed_sentences_without_append(self):
        from dota2_map_assistant.chat_lexicon import ChatLexicon
        lexicon = ChatLexicon()
        chunks = lexicon.split('你他妈的别送了', 'zh')
        self.assertIn('блять', ' '.join(chunk for chunk, protected in chunks if protected))
        self.assertNotIn(('долбоёб', True), chunks)
        self.assertNotIn(('долбоёб', True), lexicon.split('去中路', 'zh'))

    def test_cache_bounds_persistence_and_read_only_hit(self):
        from dota2_map_assistant.sqlite_cache import SQLiteTranslationCache
        from dota2_map_assistant.translator_models import TranslationEntry
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'cache.sqlite3'
            cache = SQLiteTranslationCache(path, max_entries=3)
            for i in range(5): cache.put(TranslationEntry(str(i), 'text', 'rus', '中文', 'translated', 1))
            self.assertIsNone(cache.get('0'))
            self.assertEqual(SQLiteTranslationCache(path).get('4').translated_text, '中文')
            cache.clear()
            self.assertIsNone(cache.get('4'))

    def test_offline_provider_cache_hit_avoids_model_and_network(self):
        from dota2_map_assistant.offline_translation import OfflineTranslationProvider
        from dota2_map_assistant.sqlite_cache import SQLiteTranslationCache
        class Engine:
            calls = 0
            def translate_segments(self, texts, source, target):
                self.calls += 1
                return ['мы готовы'] * len(texts)
        with tempfile.TemporaryDirectory() as tmp:
            engine = Engine()
            cache = SQLiteTranslationCache(Path(tmp) / 'cache.sqlite3')
            provider = OfflineTranslationProvider(engine, cache, target_language='ru')
            with patch('urllib.request.urlopen', side_effect=AssertionError('network')):
                self.assertEqual(provider.translate('准备开始比赛', 'zho'), 'мы готовы')
                self.assertEqual(provider.translate('准备开始比赛', 'zho'), 'мы готовы')
            self.assertEqual(engine.calls, 1)

    def test_offline_queue_has_no_network_budget_wait(self):
        from dota2_map_assistant.translation import TranslationCache, TranslationQueue
        with tempfile.TemporaryDirectory() as tmp:
            provider = SimpleNamespace(is_offline=True)
            queue = TranslationQueue(provider, TranslationCache(Path(tmp) / 'cache.json'), start_worker=False,
                                     sleeper=lambda seconds: self.fail('offline throttled'))
            queue._last_request_at = queue._clock()
            self.assertTrue(queue._wait_for_budget(100))

    def test_legacy_backend_setting_cannot_enable_network(self):
        from dota2_map_assistant.translator_controller import TranslatorController
        from dota2_map_assistant.translator_settings import TranslatorSettings
        from dota2_map_assistant.offline_translation import OfflineTranslationProvider
        for backend in ('free', 'chatgpt', 'service', 'offline'):
            provider = TranslatorController._make_provider(TranslatorSettings(translation_backend=backend))
            self.assertIsInstance(provider, OfflineTranslationProvider)

    def test_controller_outgoing_and_incoming_share_engine(self):
        from dota2_map_assistant.translator_controller import TranslatorController
        from dota2_map_assistant.translator_settings import TranslatorSettings
        controller = TranslatorController(TranslatorSettings())
        try:
            self.assertIs(controller.outgoing_provider.engine, controller.translation_queue.provider.engine)
            self.assertEqual(controller.translation_queue.request_delay_seconds, 0)
        finally:
            controller.close()

    def test_cache_changes_when_lexicon_changes(self):
        import json
        from dota2_map_assistant.chat_lexicon import ChatLexicon, LEXICON_PATH
        from dota2_map_assistant.offline_translation import cache_path
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'lexicon.json'
            path.write_bytes(LEXICON_PATH.read_bytes())
            first = cache_path(Path(tmp), ChatLexicon(path))
            data = json.loads(path.read_text(encoding='utf-8'))
            data['phrases'][0]['ru'] = 'test'
            path.write_text(json.dumps(data), encoding='utf-8')
            self.assertNotEqual(first, cache_path(Path(tmp), ChatLexicon(path)))

    def test_neutral_chinese_words_do_not_gain_swears_or_game_terms(self):
        from dota2_map_assistant.chat_lexicon import ChatLexicon
        lexicon = ChatLexicon()
        for text in ('他妈妈来接他', '这是我妈妈的建议', '我眼睛疼', '我是他的粉丝', '他去买香烟了'):
            self.assertEqual(lexicon.split(text, 'zh'), [(text, False)])

    def test_colloquial_phrases_work_inside_longer_sentences(self):
        from dota2_map_assistant.chat_lexicon import ChatLexicon
        lexicon = ChatLexicon()
        chunks = lexicon.split('傻逼中单，你到底在干什么', 'zh')
        self.assertIn(('ты что делаешь', True), chunks)
        self.assertIn(('долбоёб', True), chunks)
        self.assertIn(('сначала жми бкб', True), lexicon.split('先开BKB等一会打肉山', 'zh'))

    def test_fragmented_input_has_one_total_token_budget(self):
        with tempfile.TemporaryDirectory() as tmp:
            engine = self.engine(Path(tmp))
            engine._tokenizer = SimpleNamespace(encode=lambda text, out_type: ['x'] * 100)
            engine._translator = SimpleNamespace(translate_batch=lambda *args, **kwargs: self.fail('must reject'))
            with self.assertRaisesRegex(ValueError, '过长'):
                engine.translate_segments(['first', 'second'], 'zh', 'ru')

    def test_cache_clear_prevents_inflight_translation_refill(self):
        from dota2_map_assistant.offline_translation import OfflineTranslationProvider
        from dota2_map_assistant.sqlite_cache import SQLiteTranslationCache
        entered, release = threading.Event(), threading.Event()
        class Engine:
            def translate_segments(self, texts, source, target):
                entered.set()
                release.wait(2)
                return ['новая фраза']
        with tempfile.TemporaryDirectory() as tmp:
            cache = SQLiteTranslationCache(Path(tmp) / 'cache.sqlite3')
            provider = OfflineTranslationProvider(Engine(), cache, 'ru')
            result = []
            worker = threading.Thread(target=lambda: result.append(provider.translate('这句需要完整翻译', 'zho')))
            worker.start()
            self.assertTrue(entered.wait(2))
            cache.clear()
            release.set()
            worker.join(2)
            self.assertEqual(result, ['новая фраза'])
            self.assertEqual(len(cache._memory), 0)

    def test_close_returns_without_waiting_for_active_inference(self):
        with tempfile.TemporaryDirectory() as tmp:
            engine = self.engine(Path(tmp))
            entered, release = threading.Event(), threading.Event()
            class Translator:
                def translate_batch(self, texts, **kwargs):
                    entered.set()
                    release.wait(2)
                    return [SimpleNamespace(hypotheses=[['__ru__', 'ok']])]
            engine._translator = Translator()
            engine._tokenizer = SimpleNamespace(encode=lambda text, out_type: [text], decode=lambda tokens: ''.join(tokens))
            thread = threading.Thread(target=engine.translate_segments, args=(['text'], 'zh', 'ru'))
            thread.start()
            self.assertTrue(entered.wait(2))
            started = time.monotonic()
            engine.close()
            elapsed = time.monotonic() - started
            release.set()
            thread.join(2)
            self.assertLess(elapsed, .1)
            self.assertIsNone(engine._translator)

    def test_two_engine_instances_cannot_infer_at_same_time(self):
        with tempfile.TemporaryDirectory() as tmp:
            active = 0
            peak = 0
            class Translator:
                def translate_batch(self, texts, **kwargs):
                    nonlocal active, peak
                    active += 1
                    peak = max(peak, active)
                    time.sleep(.05)
                    active -= 1
                    return [SimpleNamespace(hypotheses=[['__ru__', 'ok']])]
            engines = [self.engine(Path(tmp)) for _ in range(2)]
            for engine in engines:
                engine._translator = Translator()
                engine._tokenizer = SimpleNamespace(encode=lambda text, out_type: [text], decode=lambda tokens: ''.join(tokens))
            threads = [threading.Thread(target=engine.translate_segments, args=(['text'], 'zh', 'ru')) for engine in engines]
            for thread in threads: thread.start()
            for thread in threads: thread.join()
            self.assertEqual(peak, 1)

    def test_provider_validates_whole_message_before_splitting(self):
        from dota2_map_assistant.offline_translation import OfflineTranslationProvider
        from dota2_map_assistant.sqlite_cache import SQLiteTranslationCache
        with tempfile.TemporaryDirectory() as tmp:
            engine = self.engine(Path(tmp))
            engine._tokenizer = SimpleNamespace(encode=lambda text, out_type: ['x'] * (480 if len(text) > 500 else 3))
            engine._translator = SimpleNamespace(translate_batch=lambda *args, **kwargs: self.fail('must reject'))
            provider = OfflineTranslationProvider(engine, SQLiteTranslationCache(Path(tmp) / 'cache.sqlite3'), 'ru')
            with self.assertRaisesRegex(ValueError, '过长'):
                provider.translate('我们现在必须去中路，然后 ' * 60, 'zho')

    def test_untranslated_latin_is_not_presented_as_russian(self):
        from dota2_map_assistant.offline_translation import OfflineTranslationProvider
        from dota2_map_assistant.sqlite_cache import SQLiteTranslationCache
        class Engine:
            def translate_segments(self, texts, source, target): return ['untranslated']
        with tempfile.TemporaryDirectory() as tmp:
            provider = OfflineTranslationProvider(Engine(), SQLiteTranslationCache(Path(tmp) / 'cache.sqlite3'), 'ru')
            with self.assertRaisesRegex(ValueError, '俄语'):
                provider.translate('这句需要翻译', 'zho')

    def test_russian_chat_uses_russian_punctuation(self):
        from dota2_map_assistant.offline_translation import OfflineTranslationProvider
        from dota2_map_assistant.sqlite_cache import SQLiteTranslationCache
        class Engine:
            def translate_segments(self, texts, source, target): self.fail('phrases should handle this')
        with tempfile.TemporaryDirectory() as tmp:
            provider = OfflineTranslationProvider(Engine(), SQLiteTranslationCache(Path(tmp) / 'cache.sqlite3'), 'ru')
            self.assertEqual(provider.translate('傻逼中单，你到底在干什么？', 'zho'), 'долбоёб мидер, ты что делаешь?')

    def test_close_during_source_validation_also_releases_weights(self):
        with tempfile.TemporaryDirectory() as tmp:
            engine = self.engine(Path(tmp))
            entered, release = threading.Event(), threading.Event()
            def encode(text, out_type):
                entered.set()
                release.wait(2)
                return ['x']
            engine._translator = object()
            engine._tokenizer = SimpleNamespace(encode=encode)
            thread = threading.Thread(target=engine.validate_text, args=('text',))
            thread.start()
            self.assertTrue(entered.wait(2))
            engine.close()
            release.set()
            thread.join(2)
            self.assertIsNone(engine._translator)

    def test_retirement_during_idle_check_releases_weights(self):
        with tempfile.TemporaryDirectory() as tmp:
            engine = self.engine(Path(tmp))
            engine._translator = object()
            engine._last_used = time.monotonic() - 181
            lock = threading.RLock()
            class RefreshingLock:
                refreshed = False
                def acquire(self, blocking=False):
                    acquired = lock.acquire(blocking=blocking)
                    if acquired and not self.refreshed:
                        self.refreshed = True
                        engine._last_used = time.monotonic()
                        closer = threading.Thread(target=engine.close)
                        closer.start()
                        closer.join(1)
                    return acquired
                def release(self): lock.release()
            engine._lock = RefreshingLock()
            engine.release_if_idle()
            self.assertIsNone(engine._translator)


if __name__ == '__main__': unittest.main()
