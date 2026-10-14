"""Selected, independently checked meanings from the supplied 2026 glossary."""
import json
from pathlib import Path
import tempfile
import unittest

from dota2_map_assistant.chat_lexicon import ChatLexicon, russian_latin_messages
from dota2_map_assistant.offline_translation import OfflineTranslationProvider
from dota2_map_assistant.sqlite_cache import SQLiteTranslationCache


class NoModel:
    def validate_text(self, text):
        raise AssertionError(f'dictionary message reached model: {text}')

    def translate_segments(self, texts, source, target):
        raise AssertionError(f'dictionary message reached model: {texts}')


class UserGlossaryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.lexicon = ChatLexicon()

    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        cache = SQLiteTranslationCache(Path(temporary.name) / 'cache.sqlite3')
        self.provider = OfflineTranslationProvider(NoModel(), cache, 'zh', self.lexicon)

    def test_new_hero_and_equipment_aliases_translate_without_loading_a_model(self):
        # Missing aliases, wrong heroes, and cross-item collisions must fail here.
        pairs = (
            ('магина', '敌法师'), ('бейн', '祸乱之源'), ('рилай', '水晶室女'),
            ('дарктеррор', '虚空假面'), ('примал', '兽'), ('кез', '凯'),
            ('ларго', '朗戈'), ('шэдоу блейд', '影刃'), ('сильвер эдж', '白银之锋'),
            ('сатаник', '撒旦之邪力'), ('диффуза', '净魂之刃'), ('радик', '辉耀'),
        )
        for text, expected in pairs:
            with self.subTest(text=text):
                self.assertEqual(self.provider.translate(text, 'ru'), expected)

    def test_shared_short_hero_names_are_not_guessed(self):
        for text in ('ск', 'сс', 'бист', 'жук'):
            with self.subTest(text=text):
                self.assertIsNone(self.lexicon.exact(text, 'ru'))
                self.assertEqual(self.lexicon.split(text, 'ru'), [(text, False)])

    def test_literal_animal_and_place_words_are_not_replaced_inside_sentences(self):
        for text, forbidden in (('лягушка в пруду', '朗戈'), ('белка на дереве', '森海飞霞'),
                                ('я помылся в бане', '祸乱之源'), ('эта земля плодородная', '大地之灵')):
            with self.subTest(text=text):
                self.assertIsNone(self.lexicon.exact(text, 'ru'))
                self.assertNotIn((forbidden, True), self.lexicon.split(text, 'ru'))

    def test_clear_equipment_name_is_protected_without_matching_inside_other_words(self):
        self.assertIn(('白银之锋', True), self.lexicon.split('купите сильвер эдж', 'ru'))
        self.assertNotIn(('辉耀', True), self.lexicon.split('радикальный', 'ru'))

    def test_latin_whitelist_requires_an_existing_incoming_translation(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / 'lexicon.json'
            path.write_text(json.dumps({
                'schema_version': 1,
                'phrases': [{'zh': '滚你妈的', 'ru': 'idi nahui', 'directions': ['ru']}],
                'terms': [{'zh': '某词', 'ru': 'outgoingonly', 'directions': ['zh']}],
                'russian_latin_aliases': ['idi nahui', 'unknownlatin', 'outgoingonly', 'кириллица'],
            }), encoding='utf-8')
            self.assertEqual(russian_latin_messages(path), frozenset({'idi nahui'}))

    def test_astonishment_and_milder_euphemisms_do_not_inherit_unrelated_swears(self):
        # The old alias group incorrectly gave all these words the same expletive.
        for text, expected in (('охуеть', '卧槽'), ('нахер', '去他的'), ('нахера', '干嘛啊')):
            with self.subTest(text=text):
                self.assertEqual(self.provider.translate(text, 'ru'), expected)

    def test_latin_obscene_command_keeps_target_and_strength(self):
        self.assertEqual(self.provider.translate('idi nahui', 'ru'), '滚你妈的')

    def test_recovery_and_refresh_are_not_forced_to_be_equipment_names(self):
        self.assertEqual(self.provider.translate('реген', 'ru'), '恢复')
        self.assertEqual(self.provider.translate('рефреш', 'ru'), '刷新')
        self.assertIsNone(self.lexicon.exact('токсик', 'ru'))

    def test_complete_game_messages_preserve_owner_negation_and_lotus_context(self):
        pairs = (
            ('го рош, возьми щит', '走，打肉山，你拿盾'),
            ('у них нет бкб, го файт', '他们没BKB，开打'),
            ('мид контролит воду, а я заберу баунти', '中单控水符，我去拿赏金符'),
            ('забери лотос с пруда', '去莲花池拿莲花'),
            ('дай лотус на керри', '给大哥套莲花'),
            ('щас приду, не деритесь без меня', '我马上到，没我别开打'),
            ('сбей ему тп и ставь сентри', '打断他的TP，再插真眼'),
        )
        for text, expected in pairs:
            with self.subTest(text=text):
                self.assertEqual(self.provider.translate(text, 'ru'), expected)

    def test_positive_profanity_and_neutral_requests_are_not_made_into_abuse(self):
        for text, expected in (('охуенно', '他妈牛逼'), ('заебись', '他妈爽'),
                               ('хуёво', '狗屎'), ('похуй', '管他妈的'),
                               ('похер', '管他的'), ('отъебись', '别他妈烦我'),
                               ('отстань', '别烦我')):
            with self.subTest(text=text):
                self.assertEqual(self.provider.translate(text, 'ru'), expected)

    def test_supplied_instruction_is_not_used_as_a_replacement_inside_a_quote(self):
        text = 'я не говорил «щас приду, не деритесь без меня»'
        self.assertIsNone(self.lexicon.exact(text, 'ru'))
        self.assertNotIn(('我马上到，没我别开打', True), self.lexicon.split(text, 'ru'))

    def test_standalone_uncertainty_does_not_remove_existing_obscene_spans(self):
        self.assertEqual(self.lexicon.exact('сука', 'ru'), '妈的／婊子')
        self.assertIn(('婊子', True), self.lexicon.split('ты сука', 'ru'))
        self.assertIn(('婊子', True), self.lexicon.split('суки, хватит', 'ru'))
        self.assertIn(('他妈的', True), self.lexicon.split('ты блядь', 'ru'))

    def test_clown_insult_is_not_a_hero_and_boiler_is_not_a_hero_span(self):
        self.assertIsNone(self.lexicon.exact('клоун афк', 'ru'))
        self.assertNotIn(('光之守卫', True), self.lexicon.split('котёл не работает', 'ru'))
        self.assertNotIn(('光之守卫', True), self.lexicon.split('новый котел для дома', 'ru'))

    def test_screenshot_short_slang_returns_a_preset_instead_of_overlong_model_output(self):
        for text in ('чет изи', 'чёт изи'):
            with self.subTest(text=text):
                self.assertEqual(self.provider.translate(text, 'ru'), '怎么这么轻松')

    def test_preset_punctuation_is_not_duplicated(self):
        self.assertEqual(self.provider.translate('где саппорты?', 'ru'), '辅助在哪?')
        self.assertEqual(self.provider.translate('заебись, мы взяли рошу!', 'ru'),
                         '牛逼，我们拿下肉山了!')

    def test_ambiguous_rooster_nickname_keeps_the_ambiguity(self):
        self.assertEqual(self.provider.translate('петух без ульта, фокус его', 'ru'),
                         '“公鸡”没大，集火他（可能指天怒）')


if __name__ == '__main__':
    unittest.main()
