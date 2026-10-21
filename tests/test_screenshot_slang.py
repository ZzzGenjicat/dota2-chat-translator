"""Real chat regressions, including negative controls for ambiguous words."""
from pathlib import Path
import tempfile
import unittest
from dota2_map_assistant.chat_lexicon import ChatLexicon
from dota2_map_assistant.offline_translation import OfflineTranslationProvider
from dota2_map_assistant.sqlite_cache import SQLiteTranslationCache

CASES = (
    ('бьольной', '有病吧'), ('бъольной', '有病吧'),
    ('оьезбяна', '猴子'), ('обезьяна', '猴子'),
    ('чильни', '冷静点'), ('чиллни!', '冷静点!'),
    ('6 тыщ птс', '6千分'), ('9 тысяч птс', '9千分'),
    ('4500 птс', '4500分'), ('У тебя линия падает', '你那一路要崩了'),
    ('Если че', '跟你说一声'), ('если чё', '跟你说一声'),
    ('подспейсил', '创造了点发育空间'), ('спейс...', '发育空间...'),
)

class ScreenshotSlangTests(unittest.TestCase):
    def test_real_messages_do_not_need_unreliable_model_guesses(self):
        class NoModel:
            def validate_text(self, text):
                raise AssertionError('Unexpected model fallback: '+text)
        with tempfile.TemporaryDirectory() as d:
            provider=OfflineTranslationProvider(NoModel(), SQLiteTranslationCache(Path(d)/'cache.db'), 'zh', ChatLexicon())
            for source, expected in CASES:
                with self.subTest(source=source):
                    self.assertEqual(provider.translate(source, 'ru'), expected)
                    self.assertEqual(provider.translate(source, 'ru'), expected)

    def test_ambiguous_words_are_not_forced_inside_literal_prose(self):
        lex=ChatLexicon()
        for source in ('белый', 'больной в больнице', 'я больной', 'спайс', 'спайс)№!'):
            with self.subTest(source=source):
                self.assertIsNone(lex.exact(source,'ru'))
                self.assertNotIn(('有病吧',True),lex.split(source,'ru'))
        self.assertEqual(lex.exact('Свен','ru'),'斯温')
