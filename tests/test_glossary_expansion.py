"""Selected October glossary additions through the shipped offline pipeline."""
import json
from pathlib import Path
import tempfile
import unittest

from dota2_map_assistant.chat_lexicon import ChatLexicon
from dota2_map_assistant.offline_translation import OfflineTranslationProvider
from dota2_map_assistant.sqlite_cache import SQLiteTranslationCache


class UnavailableModel:
    def validate_text(self, text):
        raise AssertionError(f'glossary translation unexpectedly needs model: {text}')

    def translate_segments(self, texts, source, target):
        raise AssertionError(f'glossary translation unexpectedly needs model: {texts}')


class GlossaryExpansionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.lexicon = ChatLexicon()

    def test_named_aliases_and_new_phrases_keep_their_selected_meanings(self):
        cases = (
            ('могул-хан', '斧王'), ('болтозвяк', '发条技师'),
            ('стригвир', '血魔'), ('кестрель', '凯'),
            ('пнх', '滚你妈的'), ('проебали рошу', '肉山丢了'),
            ('разъебали их на роше', '在肉山团把他们打爆了'),
            ('разъебали нас на роше', '我们在肉山团被打爆了'),
            ('не фиди, сука', '别送了，操'),
            ('изи минус', '轻松击杀'), ('тупой мув', '蠢操作'),
            ('сап без вардов', '辅助没眼'), ('сап говно', '辅助是垃圾'),
            ('керри без фарма', '大哥没经济'), ('кор мусор', '核心是废物'),
            ('заебался', '他妈累死了'), ('заебательски', '真他妈不错'),
            ('обосрал лайн', '把对线搞砸了'), ('засрал линию', '把对线搞砸了'),
        )
        for source, expected in cases:
            with self.subTest(source=source):
                self.assertEqual(self.lexicon.exact(source, 'ru'), expected)

    def test_context_aliases_work_in_existing_hero_templates_with_new_combinations(self):
        # These combinations are not the document's 50 fixed regression inputs.
        cases = (
            ('карл без байбека', '祈求者没买活'),
            ('автобус на боте', '马格纳斯在下路'),
            ('паджеро в миде', '帕吉在中路'),
            ('дрова без ульта', '卓尔游侠没大招'),
            ('пит: респ через 37 сек', '孽主还有37秒复活'),
            ('коза на топе', '魅惑魔女在上路'),
            ('карл не читер', '祈求者没开挂'),
            ('на автобус', '玩马格纳斯的'),
            ('пикни паджеро', '选帕吉'),
            ('не пикай карл', '别选祈求者'),
        )
        with tempfile.TemporaryDirectory() as temporary:
            provider = OfflineTranslationProvider(
                UnavailableModel(), SQLiteTranslationCache(Path(temporary) / 'cache.sqlite3'),
                'zh', self.lexicon)
            for source, expected in cases:
                with self.subTest(source=source):
                    self.assertEqual(provider.translate(source, 'ru'), expected)

    def test_literal_and_shared_aliases_are_not_forced_to_be_heroes(self):
        for word in ('карл', 'автобус', 'паджеро', 'дрова', 'пит', 'коза',
                     'ск', 'сс', 'бист', 'жук', 'петух', 'сало', 'купол',
                     'гусеница', 'ye', 'yo', 'e', 'fy[eq'):
            with self.subTest(word=word):
                self.assertIsNone(self.lexicon.exact(word, 'ru'))
                self.assertEqual(self.lexicon.split(word, 'ru'), [(word, False)])
        for source in ('карл читает книгу', 'автобус приехал', 'купили паджеро',
                       'дрова для печки', 'коза ест траву',
                       'я не говорил «карл без байбека»', 'карл не без ульта'):
            with self.subTest(source=source):
                self.assertIsNone(self.lexicon.exact(source, 'ru'))
                protected = [part for part, fixed in self.lexicon.split(source, 'ru') if fixed]
                for hero in ('祈求者', '马格纳斯', '帕吉', '卓尔游侠', '魅惑魔女'):
                    self.assertNotIn(hero, protected)

    def test_new_commands_do_not_replace_quoted_or_negated_sentence_fragments(self):
        for source in ('я не говорил «не фиди, сука»',
                       'он сказал «проебали рошу», но это неправда',
                       'я не сказал «пикни паджеро»'):
            with self.subTest(source=source):
                self.assertIsNone(self.lexicon.exact(source, 'ru'))
                protected = [part for part, fixed in self.lexicon.split(source, 'ru') if fixed]
                self.assertNotIn('别送了，操', protected)
                self.assertNotIn('肉山丢了', protected)
                self.assertNotIn('选帕吉', protected)

    def test_context_aliases_do_not_enter_literal_sentence_hints(self):
        for text in ('я опоздал на автобус', 'билет на автобус',
                     'я смотрю на паджеро', 'смотрю на дрова',
                     'я не говорил «на карл»'):
            with self.subTest(text=text):
                hints = '\n'.join(self.lexicon.hints(text, 'ru'))
                for hero in ('马格纳斯', '帕吉', '卓尔游侠', '祈求者'):
                    self.assertNotIn(hero, hints)

    def test_new_latin_spellings_are_explicit_incoming_messages_only(self):
        from dota2_map_assistant.chat_lexicon import russian_latin_messages
        incoming = russian_latin_messages()
        for source, expected in (('zaebis', '他妈爽'), ('zaebali', '他妈烦死了'),
                                 ('naxui', '去他妈的')):
            with self.subTest(source=source):
                self.assertIn(source, incoming)
                self.assertEqual(self.lexicon.exact(source, 'ru'), expected)
        for source in ('ye', 'yo', 'e', 'blt', 'pzd', 'rak', 'lfg'):
            self.assertNotIn(source, incoming)
        self.assertEqual(self.lexicon.exact('祈求者', 'zh'), 'инвокер')
        self.assertEqual(self.lexicon.exact('别送了', 'zh'), 'не фидь')
        self.assertEqual(self.lexicon.exact('谢谢', 'zh'), 'спс')


class ContextHeroAliasContractTests(unittest.TestCase):
    def load(self, terms):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / 'lexicon.json'
            path.write_text(json.dumps({
                'schema_version': 1, 'phrases': [], 'terms': terms,
                'templates': [{'zh': '{hero}没大招', 'ru': '{hero} без ульта',
                               'slots': {'hero': 'hero'}, 'directions': ['ru']}],
            }), encoding='utf-8')
            return ChatLexicon(path)

    def test_context_alias_enters_hero_grammar_without_becoming_a_standalone_term(self):
        lexicon = self.load([{'zh': '马格纳斯', 'ru': 'магнус', 'kind': 'hero',
                             'directions': ['ru'], 'ru_context_aliases': ['автобус']}])
        self.assertEqual(lexicon.exact('АВТОБУС без ульта!', 'ru'), '马格纳斯没大招!')
        self.assertEqual(lexicon.exact('автобус не читер', 'ru'), '马格纳斯没开挂')
        self.assertIsNone(lexicon.exact('автобус', 'ru'))
        self.assertEqual(lexicon.split('автобус приехал', 'ru'), [('автобус приехал', False)])

    def test_invalid_or_ambiguous_context_aliases_are_rejected(self):
        hero = {'zh': '马格纳斯', 'ru': 'магнус', 'kind': 'hero', 'directions': ['ru']}
        cases = [
            [{**hero, 'ru_context_aliases': 'автобус'}],
            [{**hero, 'ru_context_aliases': [' ']}],
            [{**hero, 'ru_context_aliases': ['автобус\n']}],
            [{**hero, 'ru_context_aliases': [12]}],
            [{**hero, 'kind': 'item', 'ru_context_aliases': ['автобус']}],
            [{**hero, 'directions': ['zh'], 'ru_context_aliases': ['автобус']}],
            [{**hero, 'ru_context_aliases': ['жук']},
             {'zh': '编织者', 'ru': 'вивер', 'kind': 'hero', 'directions': ['ru'],
              'ru_context_aliases': ['ЖУК']}],
            [{**hero, 'ru_context_aliases': ['вард']},
             {'zh': '侦察守卫', 'ru': 'вард', 'kind': 'item', 'directions': ['ru']}],
            [{**hero, 'ru_context_aliases': ['пока']},
             {'zh': '再见', 'ru': 'пока', 'kind': 'game', 'directions': ['ru']}],
        ]
        for terms in cases:
            with self.subTest(terms=terms):
                with self.assertRaises(RuntimeError):
                    self.load(terms)
