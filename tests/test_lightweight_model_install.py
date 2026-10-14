import hashlib
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

from dota2_map_assistant import local_model_install


class LightweightModelInstallTests(unittest.TestCase):
    def fixture(self):
        contents = {
            'model.bin': b'int8 model fixture',
            'spm.128k.model': b'tokenizer fixture',
            'config.json': b'{"decoder_start_token":"</s>"}',
            'shared_vocabulary.txt': b'__ru__\n__zh__\n',
        }
        specification = {
            'folder': 'm2m100-418m-ct2-int8',
            'repository': 'fixture/m2m100',
            'revision': 'pinned-fixture-revision',
            'quantization': 'int8',
            'files': {name: (len(data), hashlib.sha256(data).hexdigest())
                      for name, data in contents.items()},
        }
        return contents, specification

    def write_fixture(self, destination, contents):
        destination.mkdir(parents=True, exist_ok=True)
        for name, data in contents.items():
            (destination / name).write_bytes(data)

    def lightweight_installer(self):
        self.assertIsNotNone(importlib.util.find_spec('dota2_map_assistant.lightweight_model_install'),
                             'The lightweight model must have its own verified provisioning specification')
        from dota2_map_assistant import lightweight_model_install
        return lightweight_model_install

    def test_generic_install_receipt_records_int8_specification(self):
        contents, specification = self.fixture()
        with tempfile.TemporaryDirectory() as scratch:
            destination = Path(scratch)
            self.write_fixture(destination, contents)
            local_model_install.install(destination, verify_only=True, specification=specification)
            receipt = json.loads((destination / 'manifest.json').read_text(encoding='utf-8'))
            self.assertEqual(receipt['quantization'], 'int8')
            self.assertEqual(receipt['repository'], 'fixture/m2m100')
            self.assertEqual(set(receipt['files']), set(contents))

    def test_verified_lightweight_files_are_reused_without_network(self):
        installer = self.lightweight_installer()
        contents, specification = self.fixture()
        with tempfile.TemporaryDirectory() as scratch, patch.object(installer, 'SPECIFICATION', specification):
            destination = Path(scratch)
            self.write_fixture(destination, contents)
            with patch('urllib.request.OpenerDirector.open', side_effect=AssertionError('network forbidden')):
                installer.install(destination)
            with patch.object(local_model_install, 'checksum', side_effect=AssertionError('startup rehash forbidden')):
                self.assertTrue(installer.is_installed(destination))
            self.assertFalse(local_model_install.is_installed(destination))

    def test_lightweight_receipt_rejects_wrong_quantization(self):
        installer = self.lightweight_installer()
        contents, specification = self.fixture()
        with tempfile.TemporaryDirectory() as scratch, patch.object(installer, 'SPECIFICATION', specification):
            destination = Path(scratch)
            self.write_fixture(destination, contents)
            installer.install(destination, verify_only=True)
            receipt_path = destination / 'manifest.json'
            receipt = json.loads(receipt_path.read_text(encoding='utf-8'))
            receipt['quantization'] = 'Q4_K_M'
            receipt_path.write_text(json.dumps(receipt), encoding='utf-8')
            self.assertFalse(installer.is_installed(destination))

    def test_lightweight_receipt_rejects_missing_tokenizer(self):
        installer = self.lightweight_installer()
        contents, specification = self.fixture()
        with tempfile.TemporaryDirectory() as scratch, patch.object(installer, 'SPECIFICATION', specification):
            destination = Path(scratch)
            self.write_fixture(destination, contents)
            installer.install(destination, verify_only=True)
            (destination / 'spm.128k.model').unlink()
            self.assertFalse(installer.is_installed(destination))

    def test_lightweight_verify_only_preserves_existing_file_on_bad_hash(self):
        installer = self.lightweight_installer()
        contents, specification = self.fixture()
        with tempfile.TemporaryDirectory() as scratch, patch.object(installer, 'SPECIFICATION', specification):
            destination = Path(scratch)
            self.write_fixture(destination, contents)
            model = destination / 'model.bin'
            model.write_bytes(b'x' * len(contents['model.bin']))
            with self.assertRaisesRegex(RuntimeError, 'SHA256 mismatch'):
                installer.install(destination, verify_only=True)
            self.assertEqual(model.read_bytes(), b'x' * len(contents['model.bin']))
            self.assertFalse((destination / 'manifest.json').exists())

    def test_read_only_verification_preserves_packaged_receipt(self):
        installer = self.lightweight_installer()
        self.assertTrue(callable(getattr(installer, 'verify', None)),
                        'Packaged weights need verification without rewriting signed app contents')
        contents, specification = self.fixture()
        with tempfile.TemporaryDirectory() as scratch, patch.object(installer, 'SPECIFICATION', specification):
            destination = Path(scratch)
            self.write_fixture(destination, contents)
            manifest = destination / 'manifest.json'
            manifest.write_bytes(b'packaged receipt with old modification times')
            before = {path.name: (path.read_bytes(), path.stat().st_mtime_ns) for path in destination.iterdir()}
            with patch('urllib.request.OpenerDirector.open', side_effect=AssertionError('network forbidden')):
                installer.verify(destination)
            after = {path.name: (path.read_bytes(), path.stat().st_mtime_ns) for path in destination.iterdir()}
            self.assertEqual(after, before)

    def test_read_only_verification_rejects_corrupt_packaged_weight(self):
        installer = self.lightweight_installer()
        self.assertTrue(callable(getattr(installer, 'verify', None)),
                        'Packaged weights need verification without rewriting signed app contents')
        contents, specification = self.fixture()
        with tempfile.TemporaryDirectory() as scratch, patch.object(installer, 'SPECIFICATION', specification):
            destination = Path(scratch)
            self.write_fixture(destination, contents)
            (destination / 'model.bin').write_bytes(b'x' * len(contents['model.bin']))
            with self.assertRaisesRegex(RuntimeError, 'model.bin.*SHA256 mismatch'):
                installer.verify(destination)
            self.assertFalse((destination / 'manifest.json').exists())

    def test_cli_default_and_native_checks_verify_the_same_m2m_files(self):
        installer = self.lightweight_installer()
        contents, specification = self.fixture()
        tools = Path(__file__).resolve().parents[1] / 'tools'
        with patch.object(sys, 'path', [str(tools), *sys.path]):
            import install_offline_model
        for native_fixture in (False, True):
            with self.subTest(native_fixture=native_fixture), tempfile.TemporaryDirectory() as scratch:
                destination = Path(scratch)
                self.write_fixture(destination, contents)
                with patch.object(installer, 'SPECIFICATION', specification):
                    error = None
                    try:
                        install_offline_model.install(destination, verify_only=True, native_fixture=native_fixture)
                    except RuntimeError as exc:
                        error = str(exc)
                    self.assertIsNone(error, f'CLI selected a different model: {error}')
                    self.assertTrue(installer.is_installed(destination))

    def test_native_model_population_supplies_complete_verified_m2m_directory(self):
        installer = self.lightweight_installer()
        contents, specification = self.fixture()
        tools = Path(__file__).resolve().parents[1] / 'tools'
        with patch.object(sys, 'path', [str(tools), *sys.path]):
            import native_model_fixture
        with tempfile.TemporaryDirectory() as scratch, patch.object(installer, 'SPECIFICATION', specification):
            scratch = Path(scratch)
            source = scratch / 'source'
            self.write_fixture(source / 'models' / 'm2m100-418m-ct2-int8', contents)
            data = scratch / 'isolated-data'
            with patch.object(native_model_fixture, 'ROOT', source):
                error = None
                try:
                    environment = native_model_fixture.smoke_environment({'EXISTING': 'preserved'}, data)
                except FileNotFoundError as exc:
                    error = str(exc)
                self.assertIsNone(error, f'Native validation selected a different model: {error}')
            destination = data / 'models' / 'm2m100-418m-ct2-int8'
            self.assertEqual(environment['DOTA2_TRANSLATOR_SMOKE_MODEL'], str(destination))
            self.assertEqual(environment['EXISTING'], 'preserved')
            self.assertTrue(installer.is_installed(destination))
            for name, expected in contents.items():
                self.assertEqual((destination / name).read_bytes(), expected)

    def test_release_cache_validation_runs_without_llm_runtime(self):
        from dota2_map_assistant.chat_lexicon import ChatLexicon
        from dota2_map_assistant.offline_translation import M2M100Engine
        from dota2_map_assistant.release_smoke import check_chat_quality
        from dota2_map_assistant.sqlite_cache import SQLiteTranslationCache
        with tempfile.TemporaryDirectory() as scratch:
            scratch = Path(scratch)
            engine = M2M100Engine(scratch / 'uninstalled-model')
            self.addCleanup(engine.close)
            cache = SQLiteTranslationCache(scratch / 'cache.sqlite3')
            with patch.dict(sys.modules, {'dota2_map_assistant.llm_translation': None}):
                error = None
                try:
                    result = check_chat_quality(engine, ChatLexicon(), cache)
                except ImportError as exc:
                    error = str(exc)
                self.assertIsNone(error, f'Lightweight release checks depend on the optional LLM runtime: {error}')
            self.assertTrue(result['persistent_cache']['recomputation_disabled'])


if __name__ == '__main__':
    unittest.main()
