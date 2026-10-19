"""Bounded disk cache; reads never rewrite the full chat history."""
from __future__ import annotations

import sqlite3
import threading
from collections import OrderedDict
from contextlib import closing, nullcontext
from pathlib import Path

from .translator_models import TranslationEntry
from .cache_budget import CacheDirectoryBudget, MAX_CACHE_BYTES


class SQLiteTranslationCache:
    def __init__(self, path: Path, max_entries: int = 2000, *, legacy_path: Path | None = None,
                 max_bytes: int = MAX_CACHE_BYTES, directory_budget: bool = False) -> None:
        self.path = path
        self.max_entries = max(1, max_entries)
        self.max_bytes = int(max_bytes)
        if self.max_bytes < 16384:
            raise ValueError('Cache budget must allow at least 16384 bytes')
        self._budget = CacheDirectoryBudget(path, self.max_bytes) if directory_budget else None
        self._lock = threading.Lock()
        self._memory: OrderedDict[str, TranslationEntry] = OrderedDict()
        self._generation = 0
        self._legacy_path = legacy_path
        self._migration_checked = False

    def _migrate_legacy(self) -> None:
        if self._migration_checked:
            return
        self._migration_checked = True
        legacy = self._legacy_path
        if self.path.exists() or legacy is None or not legacy.is_file():
            return
        try:
            with closing(sqlite3.connect(legacy.resolve().as_uri() + '?mode=ro', uri=True, timeout=1)) as source:
                rows = source.execute('SELECT key, source, language, translated, observed FROM translations '
                                      'ORDER BY rowid DESC LIMIT ?', (self.max_entries,)).fetchall()
            with closing(self._connect()) as destination:
                for row in reversed(rows):
                    # Preserve new entries written by another application instance.
                    if not destination.execute('SELECT 1 FROM translations WHERE key=?', (row[0],)).fetchone():
                        self._store(destination, row)
        except (OSError, sqlite3.Error):
            pass  # An unreadable legacy cache must not prevent local translation.

    @property
    def generation(self) -> int:
        with self._lock:
            return self._generation

    def _connect(self) -> sqlite3.Connection:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        # Check before creating a database: even an empty SQLite file needs pages.
        self._available_bytes()
        connection = sqlite3.connect(self.path, timeout=1)
        try:
            # A persistent WAL can grow past the page cap while another client
            # holds a snapshot. If it cannot be checkpointed, skip this cache use.
            connection.execute('PRAGMA journal_mode=DELETE')
            if connection.execute('PRAGMA auto_vacuum').fetchone()[0] != 1:
                connection.execute('PRAGMA auto_vacuum=FULL')
                connection.execute('VACUUM')
            connection.execute('CREATE TABLE IF NOT EXISTS translations '
                               '(key TEXT PRIMARY KEY, source TEXT, language TEXT, translated TEXT, observed REAL)')
            self._set_limit(connection)
            return connection
        except BaseException:
            connection.close()
            raise

    def _guard(self):
        return self._budget.locked() if self._budget else nullcontext()

    def _set_limit(self, connection):
        page_size = connection.execute('PRAGMA page_size').fetchone()[0]
        available = self._available_bytes()
        pages = available // page_size
        while connection.execute('PRAGMA page_count').fetchone()[0] > pages:
            if not self._evict(connection):
                raise OSError('翻译缓存无法缩小到容量上限')
        connection.execute(f'PRAGMA max_page_count={pages}')

    def _available_bytes(self):
        available = self._budget.available() if self._budget else self.max_bytes
        while self._budget and (available < 16384 or (self.path.exists() and self.path.stat().st_size > available)):
            if not self._budget.reclaim_oldest():
                break
            available = self._budget.available()
        if available < 16384:
            raise OSError('翻译缓存已达到容量上限')
        return available

    def _evict(self, connection):
        count = connection.execute('SELECT COUNT(*) FROM translations').fetchone()[0]
        with connection:
            removed = connection.execute('DELETE FROM translations WHERE rowid IN '
                                         '(SELECT rowid FROM translations ORDER BY rowid LIMIT ?)',
                                         (max(1, min(self.max_entries // 20, count // 10)),)).rowcount
        if removed:
            self._memory.clear()
        return bool(removed)

    def _store(self, connection, row):
        # The primary key is stored twice by SQLite. Oversized entries are not
        # cached, and must not evict useful history merely to fail insertion.
        size = sum(len(str(value).encode('utf-8')) for value in row[:4]) + len(row[0].encode('utf-8'))
        page_size = connection.execute('PRAGMA page_size').fetchone()[0]
        overhead = ((self.path.parent / '.translation-cache.lock').stat().st_size if self._budget else 0)
        complete_pages = (self.max_bytes - overhead) // page_size
        if size > (complete_pages - 4) * page_size:
            return False
        while size > (connection.execute('PRAGMA max_page_count').fetchone()[0] - 4) * page_size:
            if not self._budget or not self._budget.reclaim_oldest():
                return False
            self._set_limit(connection)
        while True:
            try:
                with connection:
                    connection.execute('INSERT OR REPLACE INTO translations VALUES (?, ?, ?, ?, ?)', row)
                    evicted = connection.execute('SELECT key FROM translations ORDER BY rowid DESC LIMIT -1 OFFSET ?',
                                                 (self.max_entries,)).fetchall()
                    connection.executemany('DELETE FROM translations WHERE key=?', evicted)
                for (key,) in evicted:
                    self._memory.pop(key, None)
                return True
            except sqlite3.OperationalError as exc:
                if getattr(exc, 'sqlite_errorcode', None) != sqlite3.SQLITE_FULL:
                    raise
                connection.rollback()
                if self._budget and self._budget.reclaim_oldest():
                    self._set_limit(connection)
                elif not self._evict(connection):
                    return False

    def get(self, key: str) -> TranslationEntry | None:
        with self._lock, self._guard():
            self._migrate_legacy()
            if key in self._memory:
                self._memory.move_to_end(key)
                return self._memory[key]
            if not self.path.exists():
                return None
            try:
                with closing(self._connect()) as connection:
                    row = connection.execute('SELECT source, language, translated, observed FROM translations WHERE key=?', (key,)).fetchone()
            except sqlite3.Error as exc:
                raise OSError('本地翻译缓存无法读取') from exc
            if not row:
                return None
            entry = TranslationEntry(key, row[0], row[1], row[2], 'translated', row[3])
            self._remember(entry)
            return entry

    def _remember(self, entry: TranslationEntry) -> None:
        self._memory[entry.key] = entry
        self._memory.move_to_end(entry.key)
        while len(self._memory) > min(256, self.max_entries):
            self._memory.popitem(last=False)

    def maintain(self) -> None:
        """Create/check the cache without inventing translated chat entries."""
        with self._lock, self._guard():
            self._migrate_legacy()
            with closing(self._connect()):
                pass

    def put(self, entry: TranslationEntry, expected_generation: int | None = None) -> None:
        if entry.status != 'translated' or not entry.translated_text:
            return
        size = sum(len(value.encode('utf-8')) for value in
                   (entry.key, entry.key, entry.source_text, entry.language, entry.translated_text))
        if size > self.max_bytes - 16384:
            return
        with self._lock, self._guard():
            if expected_generation is not None and expected_generation != self._generation:
                return
            self._migrate_legacy()
            try:
                with closing(self._connect()) as connection:
                    stored = self._store(connection, (entry.key, entry.source_text, entry.language, entry.translated_text, entry.observed_at))
                if stored:
                    self._remember(entry)
            except sqlite3.Error as exc:
                raise OSError('本地翻译缓存无法写入') from exc

    def clear(self) -> None:
        with self._lock, self._guard():
            self._migrate_legacy()
            self._generation += 1
            self._memory.clear()
            if self.path.exists():
                try:
                    with closing(self._connect()) as connection, connection:
                        connection.execute('DELETE FROM translations')
                except sqlite3.Error as exc:
                    raise OSError('本地翻译缓存无法清空') from exc
