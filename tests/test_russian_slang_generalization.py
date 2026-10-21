"""Unseen combinations and literal counterexamples for common chat grammar."""
import json
from pathlib import Path
import tempfile
import unittest
from dota2_map_assistant.chat_lexicon import ChatLexicon
from dota2_map_assistant.offline_translation import OfflineTranslationProvider
from dota2_map_assistant.sqlite_cache import SQLiteTranslationCache

FIXTURE=Path(__file__).parent/'fixtures/russian_slang_generalization.json'

class RussianSlangGeneralizationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):cls.lex=ChatLexicon()

    def test_short_and_composed_messages_keep_game_meanings(self):
        class NoModel:
            def validate_text(self,text):raise AssertionError('Unexpected fallback: '+text)
        with tempfile.TemporaryDirectory() as d:
            p=OfflineTranslationProvider(NoModel(),SQLiteTranslationCache(Path(d)/'cache.db'),'zh',self.lex)
            for source,expected in json.loads(FIXTURE.read_text(encoding='utf-8')):
                with self.subTest(source=source):self.assertEqual(p.translate(source,'ru'),expected)

    def test_literal_quotes_negation_and_unknown_numbers_are_not_guessed(self):
        for source in (
            'белый цвет', 'больной в больнице', 'я больной', 'мой брат больной',
            'обезьяна живет в зоопарке', 'я не говорил «дай мне спейс»',
            'я не говорил репорт инвокера за афк', 'не говори что у меня 7к птс',
            'у меня 7к', '7к рублей', 'у меня 600 рублей на руках',
            '2.345к птс', '1000000к ммр', 'у меня -5к птс',
            'спейс шаттл', 'если что случится позвони', 'спайс)№!',
            '7к птс это не мой рейтинг', 'репорт инвокера за хорошую игру',
        ):
            with self.subTest(source=source):self.assertIsNone(self.lex.exact(source,'ru'))

    def test_rating_abbreviation_and_decimal_punctuation_survive_clause_composition(self):
        for source,expected in (
            ('10 тыс. птс, не тильтуй','10000分，别心态爆炸'),
            ('10 тыс. mmr, не тильтуй','10000分，别心态爆炸'),
            ('у меня 10 тыс. птс, дай спейс','我有10000分，给点发育空间'),
            ('У ТЕБЯ 9 ТЫС. ММР, ЧИЛЬНИ','你有9000分，冷静点'),
            ('у тебя 3,25к птс, не тильтуй','你有3250分，别心态爆炸'),
        ):
            with self.subTest(source=source):self.assertEqual(self.lex.exact(source,'ru'),expected)

    def test_grammar_also_protects_a_clause_inside_unfamiliar_prose(self):
        for source,meaning in (
            ('я думаю, у тебя 823 урона с руки, надо идти','你有823普攻伤害'),
            ('ну да, не давайте им спейс, это важно','别给他们发育空间'),
            ('хорошо, репорт снайпера за читы, потом поговорим','举报狙击手开挂'),
        ):
            with self.subTest(source=source):self.assertIn((meaning,True),self.lex.split(source,'ru'))

    def test_sell_typos_keep_item_and_negation(self):
        for source,expected in (
            ('продавай','卖掉吧'),('прадовай','卖掉吧'),('продовой','卖掉吧'),
            ('продай манту','卖掉幻影斧'),('не прадавай бкб','别卖黑皇杖'),
            ('снайпер продовай атос','狙击手，卖掉阿托斯之棍'),
            ('не продай скади','别卖冰眼'),
            ('не продавай','别卖'),
        ):
            with self.subTest(source=source):self.assertEqual(self.lex.exact(source,'ru'),expected)
        for source in ('я не говорил продавай манту','продавай квартиру','не надо говорить продай манту'):
            with self.subTest(source=source):self.assertIsNone(self.lex.exact(source,'ru'))

    def test_purchase_questions_deaths_lag_and_building_orders(self):
        cases=(
            ('нахуй ты бкб купил','你他妈买黑皇杖干嘛'),
            ('зачем ты манту купил','你买幻影斧干嘛'),
            ('зачем снайпер купил атос','狙击手买阿托斯之棍干嘛'),
            ('я лагал','我刚才卡了'),('мы лагаем','我们在卡'),
            ('я не лагаю','我不卡'),
            ('я не лагал','我刚才没卡'),('ты три раза сдох','你死了3次'),
            ('он 7 раз умер','他死了7次'),('я опять сдох','我又死了'),
            ('— Ты три раза сдох','你死了3次'),
            ('трон сломайте','拆掉基地'),('сломай трон','拆掉基地'),
            ('не ломайте трон','别拆基地'),('cломай бараки','拆掉兵营'),
            ('снайпер снеси трон','狙击手，拆掉基地'),
        )
        for source,expected in cases:
            with self.subTest(source=source):self.assertEqual(self.lex.exact(source,'ru'),expected)
        for source in ('я не говорил сломай трон','не говори что я лагал','зачем ты купил квартиру','я лагал в прошлом году'):
            with self.subTest(source=source):self.assertIsNone(self.lex.exact(source,'ru'))
