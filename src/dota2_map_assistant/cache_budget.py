"""Share a persisted-byte budget between versioned application caches."""
from contextlib import contextmanager
from pathlib import Path
import os
import re
import threading
import time

MAX_CACHE_BYTES = 10_000_000
_NAMESPACE = re.compile(r'translation_cache_(?:offline|llm)_[0-9a-f]{16}\.sqlite3\Z')
_DIRECTORY_LOCK = threading.RLock()


class CacheDirectoryBudget:
    def __init__(self, path: Path, maximum: int):
        if not _NAMESPACE.fullmatch(path.name):
            raise ValueError('Unrecognized application cache filename')
        self.path, self.maximum = path, maximum

    @contextmanager
    def locked(self):
        """Coordinate readers/eviction and writers, including other app processes."""
        with _DIRECTORY_LOCK:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with (self.path.parent / '.translation-cache.lock').open('a+b') as lock:
                lock.seek(0, 2)
                if lock.tell() == 0:
                    lock.write(b'0')
                    lock.flush()
                deadline = time.monotonic() + 1
                while True:
                    try:
                        if os.name == 'nt':
                            import msvcrt
                            lock.seek(0)
                            msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
                        else:
                            import fcntl
                            fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                        break
                    except OSError:
                        if time.monotonic() >= deadline:
                            raise OSError('本地翻译缓存正在使用中')
                        time.sleep(.02)
                try:
                    yield
                finally:
                    if os.name == 'nt':
                        lock.seek(0)
                        msvcrt.locking(lock.fileno(), msvcrt.LK_UNLCK, 1)
                    else:
                        fcntl.flock(lock.fileno(), fcntl.LOCK_UN)

    def siblings(self):
        return [p for p in self.path.parent.iterdir()
                if p != self.path and _NAMESPACE.fullmatch(p.name) and p.is_file()]

    def available(self):
        # Sidecars of another/older SQLite client are counted but never deleted.
        others = sum(p.stat().st_size + sum(side.stat().st_size for suffix in ('-wal', '-shm', '-journal')
                     if (side := Path(str(p) + suffix)).exists()) for p in self.siblings())
        return self.maximum - others - (self.path.parent / '.translation-cache.lock').stat().st_size

    def reclaim_oldest(self):
        for path in sorted(self.siblings(), key=lambda p: p.stat().st_mtime_ns):
            if path.is_symlink() or path.resolve().parent != self.path.parent.resolve():
                continue
            if any(Path(str(path) + suffix).exists() for suffix in ('-wal', '-shm', '-journal')):
                continue
            try:
                path.unlink()
                return True
            except OSError:
                continue  # Locked files remain charged against the budget.
        return False
