"""Hand-authored preset semantics; no model weights or evaluation corpus needed."""
import json
import re
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))

from dota2_map_assistant.chat_lexicon import ChatLexicon, LEXICON_PATH
from dota2_map_assistant.offline_translation import OfflineTranslationProvider
from dota2_map_assistant.sqlite_cache import SQLiteTranslationCache


class ForbiddenEngine:
    """A preset must work even when model validation and inference are unavailable."""

    def validate_text(self, text):
        raise AssertionError(f'preset unexpectedly reached model validation: {text}')

    def translate_segments(self, texts, source, target):
        raise AssertionError(f'preset unexpectedly reached model inference: {texts}')


class LightweightPhrasebookTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.lexicon = ChatLexicon()

    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        cache = SQLiteTranslationCache(Path(temporary.name) / 'preset-cache.sqlite3')
        self.providers = {
            'zh': OfflineTranslationProvider(ForbiddenEngine(), cache, 'ru', self.lexicon),
            'ru': OfflineTranslationProvider(ForbiddenEngine(), cache, 'zh', self.lexicon),
        }

    def test_common_team_messages_bypass_model_in_both_directions(self):
        # Removing a preset or reversing its subject/action must fail this test.
        pairs = (
            ('我玩五号位', 'я на пятёрке'),
            ('别推线', 'не пушь линию'),
            ('帮我拉野', 'сделай мне отвод'),
            ('别追太深', 'не гонитесь слишком далеко'),
            ('先拆塔再打肉山', 'сначала сносим башню, потом рошу'),
            ('等我复活再打', 'ждите мой респ, потом дерёмся'),
            ('帮我破林肯', 'сбейте ему линку для меня'),
            ('我马上回来', 'я скоро вернусь'),
            ('这波是我的错', 'в этом файте мой косяк'),
        )
        for zh, ru in pairs:
            for source, text, expected in (('zh', zh, ru), ('ru', ru, zh)):
                with self.subTest(source=source, text=text):
                    self.assertEqual(self.providers[source].translate(text, source), expected)

    def test_neutral_and_profane_orders_keep_their_intensity(self):
        # A profanity fallback or an accidentally neutral alias would change the speech act.
        pairs = (
            ('别抢我的兵', 'не забирай моих крипов'),
            ('别他妈抢我的兵', 'не забирай моих крипов, блять'),
            ('先打后排', 'сначала бейте заднюю линию'),
            ('先他妈打后排', 'сначала бейте заднюю линию, блять'),
            ('别怪队友', 'не вини тиммейтов'),
            ('别他妈怪队友', 'не вини тиммейтов, блять'),
            ('这辅助是傻逼', 'этот саппорт долбоёб'),
        )
        for zh, ru in pairs:
            for source, text, expected in (('zh', zh, ru), ('ru', ru, zh)):
                with self.subTest(source=source, text=text):
                    self.assertEqual(self.providers[source].translate(text, source), expected)

    def test_question_preset_preserves_question_and_does_not_become_an_order(self):
        self.assertEqual(self.providers['zh'].translate('你能来吗？', 'zh'), 'можешь прийти?')
        self.assertEqual(self.providers['ru'].translate('Можешь прийти?', 'ru'), '你能来吗?')

    def test_explicit_aliases_bypass_model_without_changing_the_reverse_key(self):
        self.assertEqual(self.providers['zh'].translate('我打5号位', 'zh'), 'я на пятёрке')
        self.assertEqual(self.providers['ru'].translate('я на пятерке', 'ru'), '我玩五号位')
        self.assertEqual(self.providers['zh'].translate('  别追太深！！  ', 'zh'),
                         'не гонитесь слишком далеко!!')
        self.assertEqual(self.lexicon.exact('谢谢', 'zh'), 'спс')
        self.assertEqual(self.lexicon.exact('别送了', 'zh'), 'не фидь')

    def test_preset_inside_negation_or_quote_is_not_an_exact_or_protected_message(self):
        # Removing whole_message_only would blindly substitute the quoted instruction.
        cases = (
            ('zh', '我没说别推线', 'не пушь линию'),
            ('zh', '他说“别他妈抢我的兵”，我不同意', 'не забирай моих крипов, блять'),
            ('zh', '为什么有人说我马上回来', 'я скоро вернусь'),
            ('ru', 'я не говорил «не пушь линию»', '别推线'),
            ('ru', 'он сказал «не вини тиммейтов, блять», это цитата', '别他妈怪队友'),
            ('ru', 'почему он написал «я скоро вернусь»', '我马上回来'),
        )
        for source, text, forbidden_translation in cases:
            with self.subTest(source=source, text=text):
                self.assertIsNone(self.lexicon.exact(text, source))
                protected = [chunk for chunk, fixed in self.lexicon.split(text, source) if fixed]
                self.assertNotIn(forbidden_translation, protected)

    def test_aliases_never_silently_override_another_translation(self):
        # Conflicting normalized aliases otherwise depend on JSON insertion order.
        data = json.loads(LEXICON_PATH.read_text(encoding='utf-8'))
        translations = {}
        for table in ('phrases', 'terms'):
            translations[table] = {'zh': {}, 'ru': {}}
            for entry in data[table]:
                for source in entry.get('directions', ('zh', 'ru')):
                    target = 'ru' if source == 'zh' else 'zh'
                    for alias in (entry[source], *entry.get(source + '_aliases', ())):
                        key = re.sub(r'\s+', ' ', alias.strip()).casefold().rstrip('。.!！?？')
                        with self.subTest(table=table, source=source, alias=alias):
                            previous = translations[table][source].setdefault(key, entry[target])
                            self.assertEqual(previous, entry[target], f'conflicting {source} alias: {alias}')
        for source in ('zh', 'ru'):
            for key, phrase in translations['phrases'][source].items():
                term = translations['terms'][source].get(key)
                if term is not None and term != phrase:
                    # Existing, intentional phrase precedence: do not expand this exception.
                    self.assertEqual((source, key, phrase, term), ('zh', '开雾', 'го смок', 'смокаемся'))
                    self.assertEqual(self.lexicon.exact(key, source), phrase)


if __name__ == '__main__':
    unittest.main()
