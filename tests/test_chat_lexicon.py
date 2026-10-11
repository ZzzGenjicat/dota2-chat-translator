"""Hand-checked chat regressions; these test dictionary/rule behavior, not M2M quality."""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))

from dota2_map_assistant.chat_lexicon import ChatLexicon


class ChatLexiconTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.lexicon = ChatLexicon()

    def test_screenshot_cheat_claim_and_hero_case(self):
        self.assertEqual(self.lexicon.exact('У ТИМБЕРА СОФТ', 'ru'), '伐木机开挂了')

    def test_screenshot_profanity_keeps_intensity(self):
        for text, expected in [('чЕРТ', '该死'), ('софт', '外挂'),
                               ('выблядки с читами', '开挂的杂种'), ('соси', '含屌去')]:
            with self.subTest(text=text):
                self.assertEqual(self.lexicon.exact(text, 'ru'), expected)

    def test_screenshot_playing_axe_is_not_an_item_or_location(self):
        self.assertEqual(self.lexicon.exact('на аксе', 'ru'), '玩斧王的')

    def test_ambiguous_screenshot_messages_do_not_invent_context(self):
        for text, expected in [('мы все накинем', '我们都会加上（具体所指不明）'),
                               ('потипай', 'потипай（含义不明）')]:
            with self.subTest(text=text):
                self.assertEqual(self.lexicon.exact(text, 'ru'), expected)

    def test_unseen_hero_cheat_claims_compose_from_aliases(self):
        for text, expected in [('у тинкера читы', '修补匠开挂了'),
                               ('у инвокера есть софт', '祈求者开挂了'),
                               ('сларк с читами', '斯拉克开挂了'),
                               ('пудж на софте', '帕吉开挂了'),
                               ('лион читер', '莱恩开挂了'),
                               ('у лины софт', '莉娜开挂了'), ('лина софт', '莉娜开挂了')]:
            with self.subTest(text=text):
                self.assertEqual(self.lexicon.exact(text, 'ru'), expected)

    def test_composed_cheat_insults_keep_their_obscenity(self):
        for text, expected in [('рубик ебаный читер', '拉比克是狗日的开挂者'),
                               ('соси ебаный читер', '含屌去，狗日的开挂者'),
                               ('соси читер', '含屌去，开挂者')]:
            with self.subTest(text=text):
                self.assertEqual(self.lexicon.exact(text, 'ru'), expected)

    def test_explicit_negation_is_preserved_in_composition(self):
        for text, expected in [('у тимбера нет софта', '伐木机没开挂'),
                               ('у тинкера нет читов', '修补匠没开挂'),
                               ('инвокер не читер', '祈求者没开挂'),
                               ('акс без читов', '斧王没开挂'),
                               ('у меня нет читов', '我没开挂'), ('он не читер', '他没开挂'),
                               ('у акса нет читов', '斧王没开挂')]:
            with self.subTest(text=text):
                self.assertEqual(self.lexicon.exact(text, 'ru'), expected)

    def test_negated_phrases_remain_protected_in_longer_chat(self):
        for text in ('нет читов', 'читов нет', 'не читер', 'без читов', 'не использую читы'):
            with self.subTest(text=text):
                chunks = self.lexicon.split('ребята, ' + text + ', честно', 'ru')
                self.assertIn(('没开挂', True), chunks)
                self.assertNotIn(('开挂者', True), chunks)

    def test_case_whitespace_and_sentence_punctuation(self):
        for text, expected in [('  У\tТИМБЕРА  СОФТ!!  ', '伐木机开挂了!!'),
                               ('ЧЁРТ?', '该死?'), ('СОСИ!', '含屌去!'),
                               ('НА АКСЕ.', '玩斧王的.'), ('У ЛИОНА НЕТ ЧИТОВ？', '莱恩没开挂?')]:
            with self.subTest(text=text):
                self.assertEqual(self.lexicon.exact(text, 'ru'), expected)

    def test_playing_templates_generalize_to_other_heroes_and_subjects(self):
        for text, expected in [('на тинкере', '玩修补匠的'), ('я на аксе', '我玩斧王'),
                               ('он играет на инвокере', '他玩祈求者'),
                               ('мы играем на спектре', '我们玩幽鬼')]:
            with self.subTest(text=text):
                self.assertEqual(self.lexicon.exact(text, 'ru'), expected)

    def test_hero_inflections_are_protected_as_names(self):
        for text, expected in [('тимбером', '伐木机'), ('аксу', '斧王'), ('тинкере', '修补匠'),
                               ('инвокера', '祈求者'), ('лиона', '莱恩'), ('спектре', '幽鬼')]:
            with self.subTest(text=text):
                self.assertEqual(self.lexicon.split(text, 'ru'), [(expected, True)])

    def test_words_and_hero_templates_have_explicit_boundaries(self):
        for text in ('софтвер', 'софтинка', 'читатель', 'тимбератор', 'акселератор',
                     'у тимбера софтвер', 'не уверен что у тимбера софт', 'на акселе'):
            with self.subTest(text=text):
                self.assertIsNone(self.lexicon.exact(text, 'ru'))
        for text in ('софтвер', 'читатель', 'тимбератор', 'акселератор'):
            self.assertEqual(self.lexicon.split(text, 'ru'), [(text, False)])

    def test_ordinary_software_context_does_not_become_a_cheat_accusation(self):
        for text in ('я обновил софт', 'установи софт для работы', 'бесплатный софт',
                     'этот софт работает', 'версия софта', 'софт для монтажа видео'):
            with self.subTest(text=text):
                self.assertIsNone(self.lexicon.exact(text, 'ru'))
                chunks = self.lexicon.split(text, 'ru')
                self.assertIn(('软件', True), chunks)
                self.assertNotIn(('外挂', True), chunks)

    def test_literal_sucking_does_not_gain_an_obscene_object(self):
        for text in ('соси леденец', 'сосите молоко', 'ребёнок сосёт молоко'):
            with self.subTest(text=text):
                self.assertIsNone(self.lexicon.exact(text, 'ru'))
                self.assertNotIn(('含屌去', True), self.lexicon.split(text, 'ru'))

    def test_negated_ordinary_software_is_not_a_cheat_denial(self):
        for text in ('нет софта для работы', 'у меня нет софта для монтажа видео',
                     'без софта для работы'):
            with self.subTest(text=text):
                self.assertIsNone(self.lexicon.exact(text, 'ru'))
                chunks = self.lexicon.split(text, 'ru')
                self.assertIn(('没有软件', True), chunks)
                self.assertNotIn(('没开挂', True), chunks)

    def test_reversed_software_negation_keeps_explicit_literal_context(self):
        for text in ('софта нет для работы', 'у меня софта нет для работы',
                     'софта нет для монтажа видео', 'софта нет для учёбы'):
            with self.subTest(text=text):
                self.assertIsNone(self.lexicon.exact(text, 'ru'))
                chunks = self.lexicon.split(text, 'ru')
                self.assertIn(('没有软件', True), chunks)
                self.assertNotIn(('没开挂', True), chunks)
        self.assertEqual(self.lexicon.exact('софта нет', 'ru'), '没开挂')
        self.assertIn(('没开挂', True), self.lexicon.split('ребята, софта нет', 'ru'))

    def test_directional_entries_do_not_overwrite_reverse_translations(self):
        for text, expected in [('傻逼', 'долбоёб'), ('混蛋', 'мудак'), ('他妈的', 'блять'),
                               ('帕吉', 'пудж'), ('谢谢', 'спс')]:
            with self.subTest(text=text):
                self.assertEqual(self.lexicon.exact(text, 'zh'), expected)

    def test_neutral_slang_is_not_strengthened_into_an_insult(self):
        for text, expected in [('читер', '开挂者'), ('репорт', '举报'), ('крыса', '老鼠'),
                               ('руинер', '故意搞砸比赛的人')]:
            with self.subTest(text=text):
                result = self.lexicon.exact(text, 'ru')
                self.assertEqual(result, expected)
                self.assertNotRegex(result, '傻逼|杂种|狗日|操')

    def test_unknown_ordinary_chat_still_reaches_model(self):
        self.assertIsNone(self.lexicon.exact('сегодня хорошая погода', 'ru'))
        self.assertEqual(self.lexicon.split('сегодня хорошая погода', 'ru'),
                         [('сегодня хорошая погода', False)])

    def test_common_short_game_phrases_do_not_need_a_model(self):
        for text, expected in [('го мид', '去中路'), ('го топ', '去上路'),
                               ('го бот', '去下路'), ('спасибо за игру', '谢谢这局比赛'),
                               ('поставь вард у рошана', '在肉山附近插眼')]:
            with self.subTest(text=text):
                self.assertEqual(self.lexicon.exact(text, 'ru'), expected)

    def test_outgoing_game_instructions_use_colloquial_russian(self):
        for text, expected in [('请跟我一起守住高地', 'дефайте хг со мной'),
                               ('等我把装备出完再打', 'дайте мне дофармить, потом дерёмся'),
                               ('对面只有一个人能买活', 'у них байбек только у одного'),
                               ('我刚才按错技能了', 'я мискликнул скилл')]:
            with self.subTest(text=text):
                self.assertEqual(self.lexicon.exact(text, 'zh'), expected)


if __name__ == '__main__':
    unittest.main()
