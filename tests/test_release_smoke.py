import socket
import sys
import unittest
import importlib.util
from dataclasses import replace
from pathlib import Path
import tempfile
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))


class OfflineSmokeGuardTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(importlib.util.find_spec('dota2_map_assistant.release_smoke'), 'Offline release self-test is missing')

    def test_remote_connections_are_rejected_before_network_access(self):
        from dota2_map_assistant import release_smoke
        with release_smoke.offline_network_guard():
            with socket.socket() as client:
                with self.assertRaisesRegex(RuntimeError, 'remote network'):
                    client.connect(('203.0.113.1', 443))
            with self.assertRaisesRegex(RuntimeError, 'remote network'):
                socket.create_connection(('example.com', 443))

    def test_loopback_gsi_still_works_and_guard_restores_socket(self):
        from dota2_map_assistant import release_smoke
        original = socket.socket.connect
        with socket.socket() as server:
            server.bind(('127.0.0.1', 0))
            server.listen(1)
            with release_smoke.offline_network_guard(), socket.socket() as client:
                client.connect(server.getsockname())
                connection, _ = server.accept()
                with connection:
                    client.sendall(b'GSI')
                    self.assertEqual(connection.recv(3), b'GSI')
        self.assertIs(socket.socket.connect, original)


class PersistentChatCacheSmokeTests(unittest.TestCase):
    def setUp(self):
        from dota2_map_assistant.chat_lexicon import ChatLexicon
        from dota2_map_assistant.offline_translation import M2M100Engine
        from dota2_map_assistant.sqlite_cache import SQLiteTranslationCache
        self.scratch = tempfile.TemporaryDirectory()
        self.addCleanup(self.scratch.cleanup)
        self.cache = SQLiteTranslationCache(Path(self.scratch.name) / 'cache.sqlite3')
        self.lexicon = ChatLexicon()
        # Exact shipped phrases require no native model load. This tests cache
        # validation only; actual inference evidence belongs to native smoke.
        self.engine = M2M100Engine(Path(self.scratch.name) / 'uninstalled-model')
        self.addCleanup(self.engine.close)

    def check_quality(self):
        from dota2_map_assistant.release_smoke import check_chat_quality
        return check_chat_quality(self.engine, self.lexicon, self.cache)

    def test_reopened_cache_reports_both_persistent_directions(self):
        result = self.check_quality()
        self.assertEqual(result['persistent_cache'], {
            'reopened': True, 'recomputation_disabled': True, 'hits': 15,
            'direction_hits': {'ru>zh': 11, 'zh>ru': 4},
        })
        self.assertEqual(result['chat_quality'][0], {
            'source': 'У ТИМБЕРА СОФТ', 'translation': '伐木机开挂了',
        })
        self.assertEqual(result['chat_quality'][-1], {
            'source': '我刚才按错技能了', 'translation': 'я мискликнул скилл',
        })

    def test_silently_dropped_writes_cannot_pass_smoke(self):
        from dota2_map_assistant.sqlite_cache import SQLiteTranslationCache
        with patch.object(SQLiteTranslationCache, 'put', return_value=None):
            with self.assertRaisesRegex(AssertionError, 'Persistent cache'):
                self.check_quality()

    def test_swallowed_write_errors_cannot_pass_smoke(self):
        from dota2_map_assistant.sqlite_cache import SQLiteTranslationCache
        with patch.object(SQLiteTranslationCache, 'put', side_effect=OSError('disk unavailable')):
            with self.assertRaisesRegex(AssertionError, 'Persistent cache'):
                self.check_quality()

    def test_all_read_misses_cannot_pass_smoke(self):
        from dota2_map_assistant.sqlite_cache import SQLiteTranslationCache
        with patch.object(SQLiteTranslationCache, 'get', return_value=None):
            with self.assertRaisesRegex(AssertionError, 'Persistent cache'):
                self.check_quality()

    def test_swapped_direction_keys_cannot_pass_smoke(self):
        from dota2_map_assistant.sqlite_cache import SQLiteTranslationCache
        original_put = SQLiteTranslationCache.put
        def put_with_reverse_direction(cache, entry, expected_generation=None):
            source, tail = entry.key.split('>', 1)
            target, fingerprint = tail.split(':', 1)
            original_put(cache, replace(entry, key=f'{target}>{source}:{fingerprint}'),
                         expected_generation=expected_generation)
        with patch.object(SQLiteTranslationCache, 'put', put_with_reverse_direction):
            with self.assertRaisesRegex(AssertionError, 'Persistent cache'):
                self.check_quality()

    def test_provider_cache_miss_cannot_recompute_from_lexicon(self):
        from dota2_map_assistant.sqlite_cache import SQLiteTranslationCache
        original_get = SQLiteTranslationCache.get
        reads = {}
        def miss_after_stored_entry_check(cache, key):
            if cache is not self.cache:
                reads[key] = reads.get(key, 0) + 1
                if reads[key] > 1:
                    return None
            return original_get(cache, key)
        with patch.object(SQLiteTranslationCache, 'get', miss_after_stored_entry_check):
            with self.assertRaisesRegex(AssertionError, 'recomputation is disabled'):
                self.check_quality()
