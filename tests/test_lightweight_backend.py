import json
import hashlib
from pathlib import Path
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from dota2_map_assistant.chat_lexicon import ChatLexicon
from dota2_map_assistant.offline_translation import M2M100Engine, resolve_model_path
from dota2_map_assistant.translator_settings import load_settings


class LightweightBackendTests(unittest.TestCase):
    def test_model_picker_selects_a_directory_without_opening_gguf_dialog(self):
        from dota2_map_assistant.translator_app import TranslatorApp
        with tempfile.TemporaryDirectory() as tmp:
            selected = []
            app = SimpleNamespace(settings_window=object(), model_path_var=SimpleNamespace(set=selected.append))
            with patch('dota2_map_assistant.translator_app.filedialog.askdirectory', return_value=tmp), \
                    patch('dota2_map_assistant.translator_app.filedialog.askopenfilename',
                          side_effect=AssertionError('GGUF file picker must not open')):
                TranslatorApp._choose_model_path(app)
            self.assertEqual(len(selected), 1)
            self.assertEqual(Path(selected[0]).resolve(), Path(tmp).resolve())

    def test_reopening_large_model_settings_selects_small_model_preserving_display(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'settings.json'
            path.write_text(json.dumps({'offline_model_path': 'models/hy-mt2-30b-a3b-q4',
                                       'font_size': 18, 'always_on_top': False}), encoding='utf8')
            settings = load_settings(path)
            self.assertEqual(settings.offline_model_path, 'models/m2m100-418m-ct2-int8')
            self.assertEqual(settings.font_size, 18)
            self.assertFalse(settings.always_on_top)

    def test_default_model_is_resolved_to_writable_data_when_not_bundled(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with patch('dota2_map_assistant.offline_translation.APP_ROOT', root / 'app'), \
                    patch('dota2_map_assistant.offline_translation.user_data_dir', return_value=root / 'data'):
                self.assertEqual(resolve_model_path('models/m2m100-418m-ct2-int8'),
                                 root / 'data/models/m2m100-418m-ct2-int8')

    def test_controller_and_composer_use_one_small_native_engine(self):
        from dota2_map_assistant.translator_controller import TranslatorController
        from dota2_map_assistant.translator_settings import TranslatorSettings
        from dota2_map_assistant.sqlite_cache import SQLiteTranslationCache
        with tempfile.TemporaryDirectory() as tmp:
            controller = TranslatorController(TranslatorSettings(),
                                              translation_cache=SQLiteTranslationCache(Path(tmp) / 'cache.sqlite3'))
            try:
                self.assertIsInstance(controller.engine, M2M100Engine)
                self.assertIs(controller.outgoing_provider.engine, controller.engine)
                self.assertIs(controller.translation_queue.provider.engine, controller.engine)
                self.assertEqual(controller.outgoing_provider.translate('你是傻逼', 'zho'), 'ты долбоёб')
            finally:
                controller.close()

    def test_bundled_model_validation_does_not_modify_receipt_or_download(self):
        from dota2_map_assistant import lightweight_model_install as model
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            folder = root / 'models/m2m100-418m-ct2-int8'
            folder.mkdir(parents=True)
            fixture = b'validated model'
            (folder / 'model.bin').write_bytes(fixture)
            files = {'model.bin': (len(fixture), hashlib.sha256(fixture).hexdigest())}
            with patch('dota2_map_assistant.offline_translation.APP_ROOT', root), \
                    patch.dict(model.SPECIFICATION, {'files': files}), \
                    patch.object(model, 'install', side_effect=AssertionError('must not mutate signed bundle')):
                M2M100Engine(folder).prepare_model()
            self.assertFalse((folder / 'manifest.json').exists())


class StrictChatTemplateTests(unittest.TestCase):
    def lexicon(self, folder, templates):
        path = Path(folder) / 'lexicon.json'
        path.write_text(json.dumps({'schema_version': 1, 'phrases': [], 'terms': [
            {'zh': '火猫', 'ru': 'эмбер', 'kind': 'hero'},
            {'zh': '黑皇杖', 'ru': 'бкб', 'kind': 'item'},
        ], 'templates': templates}, ensure_ascii=False), encoding='utf8')
        return ChatLexicon(path)

    def test_integer_slots_translate_new_timer_values_in_both_directions(self):
        with tempfile.TemporaryDirectory() as tmp:
            lex = self.lexicon(tmp, [{'zh': '肉山{seconds}秒后刷新', 'ru': 'рошан через {seconds} сек',
                                      'slots': {'seconds': 'number'}}])
            self.assertEqual(lex.exact('肉山83秒后刷新', 'zh'), 'рошан через 83 сек')
            self.assertEqual(lex.exact('РОШАН ЧЕРЕЗ 17 СЕК', 'ru'), '肉山17秒后刷新')

    def test_hero_slots_use_controlled_aliases_without_fuzzy_matching(self):
        with tempfile.TemporaryDirectory() as tmp:
            lex = self.lexicon(tmp, [{'zh': '{hero}没大', 'ru': '{hero} без ульта',
                                      'slots': {'hero': 'hero'}}])
            self.assertEqual(lex.exact('火猫没大', 'zh'), 'эмбер без ульта')
            self.assertEqual(lex.exact('эмбер без ульта', 'ru'), '火猫没大')
            for text in ('火猫不是没大', '别说火猫没大', '“火猫没大”', '火猫没大但他有买活', '火狗没大'):
                self.assertIsNone(lex.exact(text, 'zh'))

    def test_templates_do_not_match_ranges_decimal_values_or_quoted_fragments(self):
        with tempfile.TemporaryDirectory() as tmp:
            lex = self.lexicon(tmp, [{'zh': '等{seconds}秒', 'ru': 'подожди {seconds} сек',
                                      'slots': {'seconds': 'number'}}])
            for text in ('等1.5秒', '等-3秒', '别等13秒', '他说“等13秒”', '等10000秒', '等1到3秒'):
                self.assertIsNone(lex.exact(text, 'zh'))

    def test_template_slot_definition_errors_are_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaisesRegex(RuntimeError, '模板.*参数'):
                self.lexicon(tmp, [{'zh': '等{seconds}秒', 'ru': 'подожди {minutes} сек',
                                   'slots': {'seconds': 'number'}}])

    def test_item_cooldown_template_preserves_owner(self):
        self.assertEqual(ChatLexicon().exact('我的黑皇杖还有12秒冷却', 'zh'),
                         'у меня бкб: кд 12 сек')

    def test_common_profane_feeding_request_keeps_intensity(self):
        lex = ChatLexicon()
        self.assertEqual(lex.exact('你他妈能不能别送了', 'zh'), 'блять, хватит фидить')
        self.assertEqual(lex.exact('блять, хватит фидить', 'ru'), '你他妈能不能别送了')


if __name__ == '__main__':
    unittest.main()
