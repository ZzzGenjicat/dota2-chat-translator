"""Russian-first regressions: command intent, inflections, mixing and tone."""
from pathlib import Path
import tempfile
import unittest

from dota2_map_assistant.chat_lexicon import ChatLexicon
from dota2_map_assistant.offline_translation import OfflineTranslationProvider
from dota2_map_assistant.sqlite_cache import SQLiteTranslationCache


class NoModel:
    def validate_text(self, text):
        raise AssertionError('known command should not need model: ' + text)


class RussianChatRepairTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.lex = ChatLexicon()

    def translate(self, text):
        with tempfile.TemporaryDirectory() as temporary:
            provider = OfflineTranslationProvider(NoModel(), SQLiteTranslationCache(Path(temporary)/'cache.sqlite3'), 'zh', self.lex)
            return provider.translate(text, 'ru')

    def test_purchases_keep_negation_and_accept_new_hero_item_combinations(self):
        for text, expected in (
            ('купи скади', '买冰眼'), ('не покупай манту', '别买幻影斧'),
            ('акс купи бкб', '斧王，买黑皇杖'), ('kupi bkb a ne mantu', '买黑皇杖，别买幻影斧'),
            ('вр kupi skadi pls', '风行者，买冰眼，拜托了'),
            ('купи бкб, а не манту', '买黑皇杖，别买幻影斧'),
            ('не покупайте манту', '别买幻影斧'),
        ):
            with self.subTest(text=text):
                self.assertEqual(self.translate(text), expected)

    def test_objectives_keep_negative_and_contrastive_actions(self):
        for text, expected in (
            ('го тор', '去打魔方'), ('go tormentor ne rosh', '打魔方，别打肉山'),
            ('go rosh ne tormentor', '打肉山，别打魔方'),
            ('забираем терзателя и отходим', '打完魔方就撤'),
            ('не ходи на хг без аеги', '没盾别上高地'),
            ('го рошан пока они мертвые', '趁他们还没复活，去打肉山'),
            ('го рош, а не тор', '打肉山，别打魔方'),
        ):
            with self.subTest(text=text):
                self.assertEqual(self.translate(text), expected)

    def test_skill_and_item_statuses_do_not_drop_hero_or_cooldown(self):
        for text, expected in (
            ('у него рефрешер два ульта', '他有刷新球，可以放两次大招'),
            ('у акса есть рефрешер', '斧王有刷新球'),
            ('не трогайте энигму у нее бх', '别惹谜团，谜团有黑洞'),
            ('у войда хроно через 43 сек', '虚空假面的大招还有43秒'),
        ):
            with self.subTest(text=text):
                self.assertEqual(self.translate(text), expected)

    def test_black_hole_abbreviation_is_resolved_by_the_skill_owner(self):
        self.assertEqual(self.translate('у энигмы бх'), '谜团有黑洞')
        self.assertEqual(self.lex.exact('бх', 'ru'), '赏金猎人')

    def test_two_enemy_ultimates_keep_the_no_highground_warning_in_long_chat(self):
        for source, expected in (
            ('пацаны не идите хг у них энигма с бх и войд с хроно давайте лучше рошана сначала',
             '兄弟们，别上高地，对面谜团有黑洞，虚空假面有时间结界，先打肉山'),
            ('ребята не идите хг у них рубик с бх и войд с хроно давайте лучше терзателя сначала',
             '兄弟们，别上高地，对面拉比克有黑洞，虚空假面有时间结界，先打魔方'),
        ):
            with self.subTest(source=source):
                self.assertEqual(self.translate(source), expected)

    def test_gsi_accepts_known_transliterated_commands_but_leaves_english_chat_alone(self):
        import json
        from dota2_map_assistant.gsi_capture import GsiChatReader
        texts = ['kupi bkb a ne mantu', 'go tormentor ne rosh', 'where are you', 'kupi oboi dlya doma']
        payload = {'map':{'matchid': 'repairs'}, 'events':[
            {'event_type':'chat_message', 'player_id':0, 'game_time':index, 'message':text}
            for index,text in enumerate(texts)]}
        actual = GsiChatReader().read(json.dumps(payload))
        self.assertEqual([(row.text,row.language) for row in actual], [(texts[0],'rus'),(texts[1],'rus')])

    def test_punctuationless_target_change_generalizes_beyond_the_supplied_example(self):
        for text, expected in (
            ('мы не можем убить этого тимбера сейчас вообще давайте заберем терзателя потом распушим линии',
             '我们现在杀不掉这个伐木机，先打魔方，然后把兵线推出去'),
            ('мы не можем убить этого акса сейчас давайте заберем рошана потом распушим линии',
             '我们现在杀不掉这个斧王，先打肉山，然后把兵线推出去'),
        ):
            with self.subTest(text=text):
                self.assertEqual(self.translate(text), expected)

    def test_transliterated_swears_keep_the_question_or_annoyance(self):
        self.assertEqual(self.translate('blyat gde wards?'), '操，眼在哪?')
        self.assertEqual(self.translate('blyat, gde wards?'), '操，眼在哪?')
        self.assertEqual(self.translate('kupi bkb, a ne mantu'), '买黑皇杖，别买幻影斧')
        self.assertEqual(self.translate('go tormentor, a ne rosh'), '打魔方，别打肉山')
        self.assertEqual(self.translate('zayebal etot sniper'), '这狙击手真他妈烦')

    def test_literal_prose_and_quoted_or_negated_commands_are_not_claimed_as_whole_commands(self):
        for text in ('обновил тор браузер', 'я не говорил «го тор»', 'я не говорил купи манту',
                     'kupi oboi dlya doma', 'я смотрел фильм Тор', 'дайте лотос из магазина цветов'):
            with self.subTest(text=text):
                self.assertIsNone(self.lex.exact(text, 'ru'))
                self.assertNotIn(('去打魔方', True), self.lex.split(text, 'ru'))

    def test_case_forms_and_ambiguous_lotus_keep_their_meanings_inside_longer_prose(self):
        for text, expected in (
            ('мы заберем терзателя', '痛苦魔方'), ('ты спросил где лотос', '莲花'),
            ('вижу энигму', '谜团'), ('лотус орба у него нет', '清莲宝珠'),
        ):
            with self.subTest(text=text):
                self.assertIn((expected, True), self.lex.split(text, 'ru'))

    def test_neutral_generated_insult_is_rejected_without_caching(self):
        class BadModel:
            def translate_segments(self, texts, source, target):
                return ['傻瓜，我们马上回来'] * len(texts)
        with tempfile.TemporaryDirectory() as temporary:
            cache = SQLiteTranslationCache(Path(temporary)/'cache.sqlite3')
            provider = OfflineTranslationProvider(BadModel(), cache, 'zh', self.lex)
            with self.assertRaisesRegex(ValueError, '辱骂'):
                provider.translate('пацаны скоро вернемся', 'ru')
            self.assertFalse(cache._memory)

    def test_source_insults_are_preserved_and_not_blanket_censored(self):
        class Model:
            def translate_segments(self, texts, source, target):
                return ['你真是个傻瓜'] * len(texts)
        with tempfile.TemporaryDirectory() as temporary:
            provider = OfflineTranslationProvider(Model(), SQLiteTranslationCache(Path(temporary)/'cache.sqlite3'), 'zh', self.lex)
            self.assertEqual(provider.translate('ты такой глупый', 'ru'), '你真是个傻瓜')

    def test_guard_preserves_known_slang_aliases_and_literal_ordinary_nouns(self):
        for source, output in (
            ('ты ебанат', '你是傻逼'), ('ты кретин', '你这个蠢货'),
            ('не будь мудаком', '别当混蛋'), ('это промышленные отходы', '这是工业废物'),
            ('мусорное ведро сломалось', '垃圾桶坏了'), ('камера мыльница', '傻瓜相机'),
        ):
            with self.subTest(source=source):
                self.lex.validate_no_added_insults(source, output, 'ru', 'zh')
