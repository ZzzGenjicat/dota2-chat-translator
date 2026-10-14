"""Headless regression contracts from independently reviewed chat failures."""
import os
import json
import tempfile
import unittest
from pathlib import Path

from dota2_map_assistant.chat_lexicon import ChatLexicon
from dota2_map_assistant.llm_translation import build_hymt_messages, LocalTranslationProvider, normalize_russian_keyboard
from dota2_map_assistant.sqlite_cache import SQLiteTranslationCache


class ChatQualityHintTests(unittest.TestCase):
    def setUp(self):
        self.lex = ChatLexicon()

    def test_common_game_names_are_understood_without_running_the_model(self):
        class NoInference:
            def translate_message(self, *args, **kwargs):
                raise AssertionError('A standalone known game name should not load 17 GB of weights')
        with tempfile.TemporaryDirectory() as tmp:
            provider = LocalTranslationProvider(NoInference(), SQLiteTranslationCache(Path(tmp) / 'cache.sqlite3'))
            for source, expected in (('обсервер', '假眼'), ('респ', '复活'),
                                     ('глиф', '塔防'), ('шейкера', '撼地者'),
                                     ('курой', '信使'), ('т1', '一塔')):
                with self.subTest(source=source):
                    self.assertEqual(provider.translate(source, 'rus'), expected)

    def test_hero_item_and_stacking_hints_work_inside_new_sentences(self):
        hints = self.lex.hints('Шейкер без блинка, пятёрка стакни большой кемп.', 'ru')
        rendered = '\n'.join(hints)
        for meaning in ('撼地者', '跳刀', '五号位', '叠野'):
            with self.subTest(meaning=meaning):
                self.assertIn(meaning, rendered)

    def test_negative_buyback_action_is_distinct_from_unavailability(self):
        command = '\n'.join(self.lex.hints('Пока байбек не жми, мы можем отойти.', 'ru'))
        unavailable = '\n'.join(self.lex.hints('Байбека нет, я подожду команду.', 'ru'))
        self.assertIn('别使用买活', command)
        self.assertIn('没有买活可用', unavailable)
        self.assertNotIn('别使用买活', unavailable)

    def test_natural_respawn_timer_does_not_become_buyback_timer(self):
        hints = '\n'.join(self.lex.hints('我还要25秒才能复活，买活的钱不够，先守。', 'zh'))
        self.assertIn('25 сек', hints)
        self.assertIn('респ', hints)

    def test_respawn_means_refresh_for_monsters_instead_of_player_resurrection(self):
        hints = '\n'.join(self.lex.hints('Респ рошана через минуту.', 'ru'))
        self.assertIn('刷新', hints)
        self.assertNotIn('респ = 复活', hints)

    def test_later_roshan_action_does_not_change_player_respawn_into_refresh(self):
        hints = '\n'.join(self.lex.hints('Респ через 20 сек, потом убью рошана.', 'ru'))
        self.assertIn('респ = 复活', hints)
        self.assertNotIn('респ = 刷新', hints)

    def test_lion_finger_skill_is_scoped_to_game_context(self):
        game = '\n'.join(self.lex.hints('Палец готов, пусть Лион начинает.', 'ru'))
        literal = '\n'.join(self.lex.hints('У Лиона игрока болит палец после тренировки.', 'ru'))
        self.assertIn('死亡一指', game)
        self.assertNotIn('死亡一指', literal)

    def test_role_number_is_not_added_to_an_ordinary_count(self):
        role = '\n'.join(self.lex.hints('Пятёрка, купи сентри после респа.', 'ru'))
        count = '\n'.join(self.lex.hints('У них пять игроков живы.', 'ru'))
        self.assertIn('五号位', role)
        self.assertNotIn('五号位', count)

    def test_game_level_and_roaming_do_not_mean_role_six_and_quitting(self):
        hints = '\n'.join(self.lex.hints('Шестой взял, выйду с линии помочь команде.', 'ru'))
        self.assertIn('六级', hints)
        self.assertIn('游走', hints)

    def test_observer_reference_does_not_change_explicit_streaming_software(self):
        hints = '\n'.join(self.lex.hints('Обс для стрима снова завис.', 'ru'))
        self.assertNotIn('假眼', hints)

    def test_phrase_references_do_not_copy_explanatory_negations_into_the_translation(self):
        movement = '\n'.join(self.lex.hints('Шестой взял, могу выйти.', 'ru'))
        timer = '\n'.join(self.lex.hints('Байбек через 35 сек.', 'ru'))
        self.assertNotIn('退出游戏', movement)
        self.assertNotIn('已按下', timer)

    def test_stacking_instruction_keeps_the_camp_size(self):
        small = '\n'.join(self.lex.hints('Четвёрка, стакни малый рядом с линией.', 'ru'))
        big = '\n'.join(self.lex.hints('Пятёрка, стакай большой пока я фармлю.', 'ru'))
        self.assertIn('叠小野', small)
        self.assertIn('叠大野', big)

    def test_common_item_and_hero_aliases_are_references_for_new_messages(self):
        zh = '\n'.join(self.lex.hints('火猫带宝石，先破林肯再接羊，大鱼等我。', 'zh'))
        for word in ('эмбер', 'гем', 'линка', 'хекс', 'слардар'):
            self.assertIn(word, zh)
        ru = '\n'.join(self.lex.hints('Эмбер, сохрани гем, орчид отдай Слардару.', 'ru'))
        for word in ('灰烬之灵', '宝石', '紫苑', '斯拉达'):
            self.assertIn(word, ru)

    def test_short_item_nicknames_require_game_constructions(self):
        game = '\n'.join(self.lex.hints('雾我买，你带粉，先破林肯再接羊。', 'zh'))
        for word in ('смок', 'даст', 'хекс'):
            self.assertIn(word, game)
        for source in ('粉丝买粉色衣服，雾太浓了看不清山羊。',
                       '我带粉底，你买羊肉，明早可能有雾。',
                       '我买雾化器给孩子。', '先羊肉后白菜，别一起下锅。'):
            literal = '\n'.join(self.lex.hints(source, 'zh'))
            for word in ('даст', 'смок', 'хекс'):
                self.assertNotIn(word, literal)

    def test_breaking_smoke_is_not_destroying_weather_fog(self):
        hints = '\n'.join(self.lex.hints('现在拆雾会暴露位置，先等队友。', 'zh'))
        self.assertIn('сломать смок', hints)

    def test_farming_triangle_and_slardar_ultimate_keep_game_meanings(self):
        hints = '\n'.join(self.lex.hints('大哥先刷三角，大鱼点灯了就散开。', 'zh'))
        self.assertIn('фарм', hints)
        self.assertIn('треугольник', hints)
        self.assertIn('дать ульт', hints)
        self.assertIn('слардар', hints)

    def test_generic_shield_is_not_forced_to_be_aegis(self):
        generic = '\n'.join(self.lex.hints('盾没了才撤。', 'zh'))
        aegis = '\n'.join(self.lex.hints('肉山盾没了才撤。', 'zh'))
        self.assertNotIn('аегис', generic)
        self.assertIn('аегис', aegis)

    def test_creep_variants_and_ancient_camps_are_not_player_heroes(self):
        ru = '\n'.join(self.lex.hints('Добей ренжевого, потом стакни древних.', 'ru'))
        zh = '\n'.join(self.lex.hints('反补旗兵，辅助去叠远古野。', 'zh'))
        self.assertIn('远程兵', ru)
        self.assertIn('远古野', ru)
        self.assertIn('знаменосец', zh)
        self.assertIn('древние', zh)

    def test_lifestealer_doom_and_slark_are_distinct_from_pudge_and_slardar(self):
        hints = '\n'.join(self.lex.hints('小狗钻我，末日先给小鱼大。', 'zh'))
        for word in ('найкс', 'дум', 'сларк'):
            self.assertIn(word, hints)
        self.assertNotIn('пудж', hints)
        self.assertNotIn('слардар', hints)

    def test_manta_dispels_silence_and_salve_is_active_healing(self):
        hints = '\n'.join(self.lex.hints('Мантой сниму сало, салва на мне, не бей крипов.', 'ru'))
        for word in ('幻影斧', '解除沉默', '治疗药膏', '回血'):
            self.assertIn(word, hints)
        literal = '\n'.join(self.lex.hints('Сало на тарелке, возьми хлеб.', 'ru'))
        self.assertNotIn('沉默', literal)

    def test_dazzle_grave_and_right_click_are_game_actions(self):
        hints = '\n'.join(self.lex.hints('戴泽离远点，薄葬留给核心，别为了点人走上去。', 'zh'))
        for word in ('даззл', 'грейв', 'бить с руки'):
            self.assertIn(word, hints)

    def test_force_staff_and_morphling_combo_are_known_game_objects(self):
        hints = '\n'.join(self.lex.hints('Форс готов, Морфа одним прокастом не убить.', 'ru'))
        for word in ('原力法杖', '变体精灵', '一套技能爆发'):
            self.assertIn(word, hints)

    def test_nested_apology_denial_is_distinct_from_a_comma_separated_apology(self):
        denial = '\n'.join(self.lex.hints('别误会我是在道歉。', 'zh'))
        apology = '\n'.join(self.lex.hints('别误会，我是在道歉。', 'zh'))
        self.assertIn('не думай, что', denial)
        self.assertNotIn('не думай, что', apology)

    def test_hero_ultimate_shorthand_is_not_a_coat_or_university(self):
        ultimate = '\n'.join(self.lex.hints('末日的大只交给那个核心。', 'zh'))
        self.assertIn('大 = ульт', ultimate)
        for text in ('火女的大衣很好看。', '末日的大学同学来了。'):
            self.assertNotIn('大 = ульт', '\n'.join(self.lex.hints(text, 'zh')))

    def test_empty_custom_hero_table_cannot_turn_ordinary_big_into_ultimate(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'words.json'
            path.write_text(json.dumps(dict(schema_version=1, phrases=[], terms=[])), encoding='utf-8')
            self.assertEqual(ChatLexicon(path).hints('体型大一点。', 'zh'), [])

    def test_explanatory_labels_do_not_get_copied_into_game_translations(self):
        hints = '\n'.join(self.lex.hints('У Лиона палец готов, смок не ломай.', 'ru'))
        self.assertNotIn('（', hints)

    def test_vulgar_quality_contrast_keeps_the_swear_with_the_game_object(self):
        hints = '\n'.join(self.lex.hints('«Говно, а не сейв» написал не я.', 'ru'))
        self.assertIn('不是救人，是狗屎', hints)

    def test_isolated_chinese_body_word_interjection_is_not_a_noun_in_russian(self):
        self.assertEqual(list(self.lex.protect_profanity('鸡巴，别碰神符！', 'zh')[1].values()), ['блять'])
        self.assertEqual(list(self.lex.protect_profanity('“鸡巴”是身体部位的粗俗叫法。', 'zh')[1].values()), ['хуй'])

    def test_rhetorical_game_action_body_word_is_an_expletive_not_anatomy(self):
        for text in ('推个鸡巴塔，先守家。', '买个鸡巴装备，先打团。', '打个鸡巴团，队友没来。'):
            with self.subTest(text=text):
                self.assertEqual(list(self.lex.protect_profanity(text, 'zh')[1].values()), ['нахуй'])
        for text in ('画个鸡巴在纸上。', '“鸡巴”是身体部位的粗俗叫法。'):
            self.assertEqual(list(self.lex.protect_profanity(text, 'zh')[1].values()), ['хуй'])

    def test_vulgar_rhetorical_fight_phrase_is_not_an_order_to_gather(self):
        hints = '\n'.join(self.lex.hints('打个鸡巴团，队友还在泉水里，先退。', 'zh'))
        self.assertIn('какой нахуй файт', hints)
        self.assertNotIn('соберите', hints)

    def test_obscene_idioms_are_longest_spans_not_literal_body_parts(self):
        for text in ('Иди на хуй со спамом.', 'Он написал «иди на хуй», не я.',
                     'Не пиши «иди на хуй».'):
            _, replacements = self.lex.protect_profanity(text, 'ru')
            self.assertEqual(list(replacements.values()), ['滚你妈的'])
        _, surprise = self.lex.protect_profanity('Да ну нахуй, он выжил?', 'ru')
        self.assertEqual(list(surprise.values()), ['我操'])

    def test_new_swear_forms_and_transliteration_retain_vulgar_register(self):
        for text in ('Blyat, где TP?', 'Suka, где TP?', 'Пиздец, ноль маны.',
                     'Ты охуел или лагнул?', 'Не хуёво сыграл.', 'Заебись, спас.'):
            with self.subTest(text=text):
                self.assertTrue(self.lex.protect_profanity(text, 'ru')[1])

    def test_literal_female_dog_and_player_name_are_not_forced_insults(self):
        for text in ('Сука родила щенков.', 'Игрок Suka купился.', 'Это ник Suka, а не ругань.'):
            self.assertFalse(self.lex.protect_profanity(text, 'ru')[1])

    def test_chinese_quoted_swear_is_not_confused_with_operation_or_control(self):
        self.assertTrue(self.lex.protect_profanity('别把“操”译成操作。', 'zh')[1])
        for text in ('操作没问题，操控镜头也正常。', '体操运动员和操场。',
                     '这是我妈妈的建议，我操作一下就好。'):
            self.assertFalse(self.lex.protect_profanity(text, 'zh')[1])

    def test_boasting_idiom_is_not_replaced_with_vulgar_praise(self):
        _, boast = self.lex.protect_profanity('别吹牛逼，先看战绩。', 'zh')
        _, praise = self.lex.protect_profanity('牛逼，这波救得好。', 'zh')
        self.assertEqual(list(boast.values()), ['выёбываться'])
        self.assertEqual(list(praise.values()), ['охуенно'])

    def test_kill_trade_and_wasted_ultimate_keep_game_actions(self):
        hints = '\n'.join(self.lex.hints('这波二换五，还白交大了。', 'zh'))
        self.assertIn('размен 2 на 5', hints)
        self.assertIn('ульт', hints)
        ordinary = '\n'.join(self.lex.hints('我在交大毕业，大家看过吗？', 'zh'))
        self.assertNotIn('ульт', ordinary)

    def test_romanized_negation_is_understood_between_game_abbreviations(self):
        self.assertEqual(normalize_russian_keyboard('bkb est, tp net'), 'bkb есть, tp нет')
        self.assertEqual(normalize_russian_keyboard('go def hg bez bb'), 'go def hg без bb')

    def test_romanized_game_normalizer_keeps_software_names_and_unrelated_english(self):
        self.assertEqual(normalize_russian_keyboard('Мы используем .NET, а не networth.'),
                         'Мы используем .NET, а не networth.')
        self.assertEqual(normalize_russian_keyboard('net income is high'), 'net income is high')

    def test_unrelated_messages_share_the_stable_background_before_term_references(self):
        first = build_hymt_messages('Акса не бей до бкб.', 'ru', 'zh', self.lex, (), {})[0]['content']
        second = build_hymt_messages('Сентри рядом с троном.', 'ru', 'zh', self.lex, (), {})[0]['content']
        self.assertGreaterEqual(len(os.path.commonprefix((first, second))), 80)
        self.assertIn('Акса не бей до бкб.', first)
        self.assertIn('Сентри рядом с троном.', second)
        # Background and current message still have distinct roles.
        contextual = build_hymt_messages('Палец готов.', 'ru', 'zh', self.lex, ('Я на Лионе.',), {})[0]['content']
        self.assertIn('〖背景信息〗', contextual)
        self.assertIn('〖待翻译文本〗', contextual)


if __name__ == '__main__':
    unittest.main()
