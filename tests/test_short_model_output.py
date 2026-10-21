"""Reject unfinished model output without blaming a short source message."""
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest

from dota2_map_assistant.chat_lexicon import ChatLexicon
from dota2_map_assistant.offline_translation import M2M100Engine, OfflineTranslationProvider
from dota2_map_assistant.sqlite_cache import SQLiteTranslationCache


class ShortModelOutputTests(unittest.TestCase):
    def test_unknown_model_symbols_are_not_shown_or_cached_as_translations(self):
        for tokens, decoded in ((['__zh__', '<unk>', '子'], ' ⁇ 子'),
                                (['__zh__', '▁<', 'unk', '>子'], '<unk>子'),
                                (['__zh__', '▁⁇', '子'], '⁇子')):
            with self.subTest(tokens=tokens), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                lexicon_path = root / 'lexicon.json'
                lexicon_path.write_text(json.dumps({'schema_version': 1, 'phrases': [], 'terms': []}), encoding='utf-8')
                cache = SQLiteTranslationCache(root / 'cache.sqlite3')
                engine = M2M100Engine(root)
                self.addCleanup(engine.close)
                engine._tokenizer = SimpleNamespace(encode=lambda text, out_type: ['source'],
                                                     decode=lambda pieces: decoded)
                engine._translator = SimpleNamespace(translate_batch=lambda *args, **kwargs: [
                    SimpleNamespace(hypotheses=[tokens]),
                ])
                provider = OfflineTranslationProvider(engine, cache, 'zh', ChatLexicon(lexicon_path))
                with self.assertRaisesRegex(ValueError, '未能可靠完成翻译'):
                    provider.translate('Какая же ты обуза.', 'rus')
                self.assertIsNone(cache.get('ru>zh:rus:какая же ты обуза.'))

    def test_saturated_short_output_reports_failure_without_caching_or_retrying(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            lexicon_path = root / 'lexicon.json'
            lexicon_path.write_text(json.dumps({
                'schema_version': 1, 'phrases': [], 'terms': [],
            }), encoding='utf-8')
            cache = SQLiteTranslationCache(root / 'cache.sqlite3')
            engine = M2M100Engine(root)
            self.addCleanup(engine.close)
            engine._tokenizer = SimpleNamespace(
                encode=lambda text, out_type: ['▁чет', '▁изи'],
                decode=lambda tokens: self.fail('unfinished output must not be decoded'),
            )
            calls = []

            def translate_batch(sources, **kwargs):
                calls.append(sources)
                # Actual local INT8 failure: the short slang loops until the cap.
                tokens = ['__zh__'] + ['▁', '四'] * 63 + ['▁']
                return [SimpleNamespace(hypotheses=[tokens])]

            engine._translator = SimpleNamespace(translate_batch=translate_batch)
            provider = OfflineTranslationProvider(engine, cache, 'zh', ChatLexicon(lexicon_path))
            for attempt in (1, 2):
                with self.assertRaisesRegex(ValueError, '可靠') as caught:
                    provider.translate('чет изи', 'ru')
                self.assertNotIn('过长', str(caught.exception))
                self.assertNotIn('拆', str(caught.exception))
                self.assertEqual(len(calls), attempt)
                self.assertEqual(len(cache._memory), 0)

    def test_output_below_the_limit_is_decoded_completely(self):
        with tempfile.TemporaryDirectory() as temporary:
            engine = M2M100Engine(Path(temporary))
            self.addCleanup(engine.close)
            engine._tokenizer = SimpleNamespace(
                encode=lambda text, out_type: ['source'],
                decode=lambda tokens: ''.join(tokens),
            )
            engine._translator = SimpleNamespace(translate_batch=lambda *args, **kwargs: [
                SimpleNamespace(hypotheses=[['__zh__'] + [str(i) for i in range(126)]]),
            ])
            self.assertEqual(engine.translate_segments(['source'], 'ru', 'zh'), [''.join(str(i) for i in range(126))])

    def test_decode_loops_below_token_limit_are_rejected_but_source_laughter_is_allowed(self):
        with tempfile.TemporaryDirectory() as temporary:
            engine=M2M100Engine(Path(temporary))
            self.addCleanup(engine.close)
            engine._tokenizer=SimpleNamespace(encode=lambda text,out_type:['source'],decode=lambda tokens:'哈哈'*40)
            engine._translator=SimpleNamespace(translate_batch=lambda *args,**kwargs:[
                SimpleNamespace(hypotheses=[['__zh__','laughter']])])
            with self.assertRaisesRegex(ValueError,'可靠'):
                engine.translate_segments(['я тебе уже сказал'], 'ru','zh')
            engine._tokenizer.decode=lambda tokens:'哈'*12+'，'+'好'*80
            with self.assertRaisesRegex(ValueError,'可靠'):
                engine.translate_segments(['я тебе уже сказал'], 'ru','zh')
            engine._tokenizer.decode=lambda tokens:'是的,'*15
            with self.assertRaisesRegex(ValueError,'可靠'):
                engine.translate_segments(['для диспела'], 'ru','zh')
            engine._tokenizer.decode=lambda tokens:'哈哈'*40
            self.assertEqual(engine.translate_segments(['ах'*40], 'ru','zh'),['哈哈'*40])


if __name__ == '__main__':
    unittest.main()
