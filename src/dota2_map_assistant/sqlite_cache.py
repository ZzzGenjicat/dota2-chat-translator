"""Bounded disk cache; reads never rewrite the full chat history."""
from __future__ import annotations

import sqlite3
import threading
from collections import OrderedDict
from contextlib import closing
from pathlib import Path

from .translator_models import TranslationEntry


class SQLiteTranslationCache:
    def __init__(self, path: Path, max_entries: int = 2000, *, legacy_path: Path | None = None) -> None:
        self.path = path
        self.max_entries = max(1, max_entries)
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
            with closing(self._connect()) as destination, destination:
                # Preserve any new entries written by another application instance.
                destination.executemany('INSERT OR IGNORE INTO translations VALUES (?, ?, ?, ?, ?)', reversed(rows))
        except (OSError, sqlite3.Error):
            pass  # An unreadable legacy cache must not prevent local translation.

    @property
    def generation(self) -> int:
        with self._lock:
            return self._generation

    def _connect(self) -> sqlite3.Connection:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.path, timeout=1)
        connection.execute('CREATE TABLE IF NOT EXISTS translations '
                           '(key TEXT PRIMARY KEY, source TEXT, language TEXT, translated TEXT, observed REAL)')
        return connection

    def get(self, key: str) -> TranslationEntry | None:
        with self._lock:
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

    def put(self, entry: TranslationEntry, expected_generation: int | None = None) -> None:
        if entry.status != 'translated' or not entry.translated_text:
            return
        with self._lock:
            if expected_generation is not None and expected_generation != self._generation:
                return
            self._migrate_legacy()
            try:
                with closing(self._connect()) as connection, connection:
                    connection.execute('INSERT OR REPLACE INTO translations VALUES (?, ?, ?, ?, ?)',
                                       (entry.key, entry.source_text, entry.language, entry.translated_text, entry.observed_at))
                    evicted = connection.execute('SELECT key FROM translations ORDER BY rowid DESC LIMIT -1 OFFSET ?',
                                                 (self.max_entries,)).fetchall()
                    connection.executemany('DELETE FROM translations WHERE key=?', evicted)
                for (key,) in evicted:
                    self._memory.pop(key, None)
                self._remember(entry)
            except sqlite3.Error as exc:
                raise OSError('本地翻译缓存无法写入') from exc

    def clear(self) -> None:
        with self._lock:
            self._migrate_legacy()
            self._generation += 1
            self._memory.clear()
            if self.path.exists():
                try:
                    with closing(self._connect()) as connection, connection:
                        connection.execute('DELETE FROM translations')
                except sqlite3.Error as exc:
                    raise OSError('本地翻译缓存无法清空') from exc
