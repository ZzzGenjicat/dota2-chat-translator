"""Disk-size bounds, not character counts, for persistent translations."""
from pathlib import Path
from contextlib import closing
import sqlite3
import tempfile
import unittest

from dota2_map_assistant.sqlite_cache import SQLiteTranslationCache
from dota2_map_assistant.translator_models import TranslationEntry


def entry(number, size=1500):
    return TranslationEntry(str(number), '俄' * size, 'rus', '译' * size, 'translated', number)


class CacheSizeLimitTests(unittest.TestCase):
    def test_utf8_growth_evicts_old_records_without_exceeding_disk_budget(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / 'cache.sqlite3'
            cache = SQLiteTranslationCache(path, max_bytes=65536)
            for number in range(30):
                cache.put(entry(number))
                self.assertLessEqual(path.stat().st_size, 65536)
                self.assertEqual(cache.get(str(number)), entry(number))
            self.assertIsNone(cache.get('0'))
            self.assertEqual(SQLiteTranslationCache(path, max_bytes=65536).get('29'), entry(29))
            cache.clear()
            self.assertLessEqual(path.stat().st_size, 16384)

    def test_single_oversized_record_does_not_displace_existing_translation(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / 'cache.sqlite3'
            cache = SQLiteTranslationCache(path, max_bytes=65536)
            cache.put(entry(1, 20))
            cache.put(entry(2, 65536))
            self.assertIsNone(cache.get('2'))
            self.assertEqual(cache.get('1'), entry(1, 20))
            self.assertLessEqual(path.stat().st_size, 65536)

    def test_existing_large_database_and_legacy_migration_are_bounded(self):
        with tempfile.TemporaryDirectory() as temporary:
            old = Path(temporary) / 'legacy.sqlite3'
            source = SQLiteTranslationCache(old)
            for number in range(30):
                source.put(entry(number))
            before = old.read_bytes()
            destination = Path(temporary) / 'migrated.sqlite3'
            cache = SQLiteTranslationCache(destination, legacy_path=old, max_bytes=65536)
            self.assertEqual(cache.get('29'), entry(29))
            self.assertLessEqual(destination.stat().st_size, 65536)
            self.assertEqual(old.read_bytes(), before)
            reopened = SQLiteTranslationCache(old, max_bytes=65536)
            self.assertEqual(reopened.get('29'), entry(29))
            self.assertIsNone(reopened.get('0'))
            self.assertLessEqual(old.stat().st_size, 65536)
            with closing(sqlite3.connect(old)) as connection:
                self.assertEqual(connection.execute('PRAGMA integrity_check').fetchone()[0], 'ok')

    def test_default_limit_is_decimal_ten_megabytes(self):
        cache = SQLiteTranslationCache(Path('unused.sqlite3'))
        self.assertEqual(cache.max_bytes, 10_000_000)

    def test_versions_share_budget_and_only_discard_known_old_cache_files(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            unrelated = root / 'personal.sqlite3'
            unrelated.write_bytes(b'personal data')
            old = root / ('translation_cache_offline_' + 'a' * 16 + '.sqlite3')
            current = root / ('translation_cache_offline_' + 'b' * 16 + '.sqlite3')
            previous = SQLiteTranslationCache(old, max_bytes=65536, directory_budget=True)
            previous.put(entry(1))
            cache = SQLiteTranslationCache(current, max_bytes=65536, directory_budget=True)
            cache.put(entry(2))
            self.assertTrue(old.exists(), 'small old caches should be retained')
            for number in range(3, 30):
                cache.put(entry(number))
                total = sum(p.stat().st_size for p in root.iterdir() if p.name.startswith(('translation_cache_', '.translation-cache')))
                self.assertLessEqual(total, 65536)
                self.assertEqual(cache.get(str(number)), entry(number))
            self.assertFalse(old.exists())
            self.assertEqual(unrelated.read_bytes(), b'personal data')

    def test_locked_or_journaled_old_namespace_is_retained_and_charged(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            old = root / ('translation_cache_llm_' + 'a' * 16 + '.sqlite3')
            SQLiteTranslationCache(old).put(entry(1))
            journal = Path(str(old) + '-journal')
            journal.write_bytes(b'x' * 4096)
            before = old.read_bytes()
            current = root / ('translation_cache_offline_' + 'b' * 16 + '.sqlite3')
            cache = SQLiteTranslationCache(current, max_bytes=65536, directory_budget=True)
            for number in range(2, 12):
                cache.put(entry(number, 100))
                self.assertLessEqual(sum(p.stat().st_size for p in root.iterdir()), 65536)
            self.assertEqual(old.read_bytes(), before)
            self.assertEqual(journal.stat().st_size, 4096)

    def test_no_new_database_is_created_when_non_evictable_sibling_fills_budget(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            old = root / ('translation_cache_llm_' + 'a' * 16 + '.sqlite3')
            SQLiteTranslationCache(old).put(entry(1))
            Path(str(old) + '-journal').write_bytes(b'x' * 32768)
            current = root / ('translation_cache_offline_' + 'b' * 16 + '.sqlite3')
            cache = SQLiteTranslationCache(current, max_bytes=65536, directory_budget=True)
            with self.assertRaises(OSError):
                cache.maintain()
            self.assertFalse(current.exists())
            self.assertLessEqual(sum(p.stat().st_size for p in root.iterdir()), 65536)

    def test_active_wal_reader_cannot_cause_unbounded_cache_writes(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            path = root / 'cache.sqlite3'
            SQLiteTranslationCache(path).put(entry(1, 20))
            with closing(sqlite3.connect(path)) as reader:
                reader.execute('PRAGMA journal_mode=WAL')
                reader.execute('BEGIN')
                reader.execute('SELECT * FROM translations').fetchall()
                before = sum(p.stat().st_size for p in root.iterdir())
                with self.assertRaises(OSError):
                    SQLiteTranslationCache(path, max_bytes=65536).put(entry(2, 1500))
                self.assertLessEqual(sum(p.stat().st_size for p in root.iterdir()), before)

    def test_record_larger_than_rounded_shared_page_budget_preserves_history(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / ('translation_cache_offline_' + 'a' * 16 + '.sqlite3')
            cache = SQLiteTranslationCache(path, max_bytes=65536, directory_budget=True)
            old = TranslationEntry('old', 'short', 'rus', 'ok', 'translated', 1)
            cache.put(old)
            cache.put(TranslationEntry('new', 'x' * 49100, 'rus', 'ok', 'translated', 2))
            self.assertEqual(cache.get('old'), old)
            self.assertIsNone(cache.get('new'))

    def test_record_cannot_evict_history_when_locked_sibling_uses_remaining_space(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            old = root / ('translation_cache_llm_' + 'a' * 16 + '.sqlite3')
            SQLiteTranslationCache(old).put(entry(1))
            Path(str(old) + '-journal').write_bytes(b'x' * 4096)
            path = root / ('translation_cache_offline_' + 'b' * 16 + '.sqlite3')
            cache = SQLiteTranslationCache(path, max_bytes=65536, directory_budget=True)
            previous = TranslationEntry('old', 'short', 'rus', 'ok', 'translated', 1)
            cache.put(previous)
            cache.put(TranslationEntry('new', 'x' * 32000, 'rus', 'ok', 'translated', 2))
            self.assertEqual(cache.get('old'), previous)
            self.assertIsNone(cache.get('new'))
