"""Run local inference with network calls blocked and write measured results."""
from __future__ import annotations
import json
import socket
import sys
import tempfile
import threading
import time
import urllib.request
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'vendor' / 'offline'), str(ROOT / 'src')]
import psutil
from dota2_map_assistant.offline_translation import M2M100Engine, OfflineTranslationProvider
from dota2_map_assistant.sqlite_cache import SQLiteTranslationCache


def main():
    process = psutil.Process()
    measurements = {'cpu': process.cpu_affinity(), 'threads': 2, 'rss_before_mib': process.memory_info().rss / 2**20,
                    'network_blocked': True, 'samples': []}
    peak = [process.memory_info().rss]
    stop = threading.Event()
    def sample_memory():
        while not stop.wait(.02): peak[0] = max(peak[0], process.memory_info().rss)
    threading.Thread(target=sample_memory, daemon=True).start()
    def blocked(*args, **kwargs): raise AssertionError('Unexpected network call')
    with tempfile.TemporaryDirectory() as temporary, patch.object(socket, 'create_connection', blocked), patch.object(urllib.request, 'urlopen', blocked):
        engine = M2M100Engine()
        cache = SQLiteTranslationCache(Path(temporary) / 'cache.sqlite3')
        incoming = OfflineTranslationProvider(engine, cache, 'zh')
        outgoing = OfflineTranslationProvider(engine, cache, 'ru')
        started = time.perf_counter()
        engine.warmup()
        measurements['cold_warmup_ms'] = (time.perf_counter() - started) * 1000
        measurements['rss_loaded_mib'] = process.memory_info().rss / 2**20
        for provider, source, language in [
            (incoming, 'у нас нет шансов выиграть', 'rus'),
            (incoming, 'я вернусь через минуту', 'rus'),
            (outgoing, '需要更多时间准备这场比赛', 'zho'),
            (outgoing, '队友还没有准备好', 'zho'),
            (incoming, 'ты долбоеб, купи бкб', 'rus'),
            (outgoing, '你他妈的别送了', 'zho'),
            (outgoing, '傻逼中单，你到底在干什么', 'zho'),
            (outgoing, '先开BKB然后打肉山', 'zho'),
            (incoming, 'не фидь, блять', 'rus'),
            (outgoing, '你是白痴', 'zho'),
            (outgoing, '你是傻逼', 'zho'),
        ]:
            started = time.perf_counter()
            translated = provider.translate(source, language)
            elapsed = (time.perf_counter() - started) * 1000
            started = time.perf_counter()
            assert provider.translate(source, language) == translated
            cached_ms = (time.perf_counter() - started) * 1000
            measurements['samples'].append({'source': source, 'translation': translated,
                                             'first_ms': elapsed, 'cache_ms': cached_ms})
        measurements['rss_after_translation_mib'] = process.memory_info().rss / 2**20
        engine.close()
        time.sleep(.1)
        measurements['rss_released_mib'] = process.memory_info().rss / 2**20
    stop.set()
    measurements['peak_rss_mib'] = peak[0] / 2**20
    output = ROOT / 'docs' / 'offline_benchmark.json'
    output.write_text(json.dumps(measurements, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(measurements, ensure_ascii=False, indent=2))


if __name__ == '__main__': main()
