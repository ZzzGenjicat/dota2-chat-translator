import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from dota2_map_assistant.sqlite_cache import SQLiteTranslationCache
from dota2_map_assistant.translator_models import TranslationEntry


class CacheMigrationTests(unittest.TestCase):
    def test_old_cache_is_copied_without_changing_original(self):
        with tempfile.TemporaryDirectory() as tmp:
            old, new = Path(tmp) / "legacy.sqlite3", Path(tmp) / "user" / "cache.sqlite3"
            entry = TranslationEntry("ru>zh:hello", "привет", "rus", "你好", "translated", 1)
            SQLiteTranslationCache(old).put(entry)
            before = old.read_bytes()
            migrated = SQLiteTranslationCache(new, legacy_path=old)
            self.assertEqual(migrated.get(entry.key), entry)
            self.assertTrue(new.exists())
            self.assertEqual(old.read_bytes(), before)
            migrated.clear()
            self.assertIsNone(migrated.get(entry.key))
            self.assertIsNotNone(SQLiteTranslationCache(old).get(entry.key))

    def test_existing_user_cache_wins_and_bad_legacy_database_is_ignored(self):
        with tempfile.TemporaryDirectory() as tmp:
            old, new = Path(tmp) / "legacy.sqlite3", Path(tmp) / "user.sqlite3"
            entry = TranslationEntry("key", "a", "rus", "new", "translated", 1)
            old.write_bytes(b"broken database")
            cache = SQLiteTranslationCache(new, legacy_path=old)
            self.assertIsNone(cache.get("key"))
            cache.put(entry)
            self.assertEqual(cache.get("key"), entry)
            old.unlink()
            SQLiteTranslationCache(old).put(TranslationEntry("key", "a", "rus", "old", "translated", 1))
            self.assertEqual(SQLiteTranslationCache(new, legacy_path=old).get("key"), entry)

    def test_migration_preserves_recency_when_new_entries_evict_oldest(self):
        with tempfile.TemporaryDirectory() as tmp:
            old, new = Path(tmp) / "legacy.sqlite3", Path(tmp) / "new.sqlite3"
            original = SQLiteTranslationCache(old)
            for number in range(4):
                original.put(TranslationEntry(str(number), "a", "rus", "translated", "translated", number))
            migrated = SQLiteTranslationCache(new, max_entries=2, legacy_path=old)
            self.assertIsNotNone(migrated.get("3"))
            migrated.put(TranslationEntry("4", "a", "rus", "translated", "translated", 4))
            self.assertIsNotNone(migrated.get("3"))
            self.assertIsNone(migrated.get("2"))
