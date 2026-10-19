"""GSI input must not be censored by translation or persistent caching."""
from contextlib import closing
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest

from dota2_map_assistant.gsi_capture import GsiChatReader
from dota2_map_assistant.offline_translation import OfflineTranslationProvider
from dota2_map_assistant.sqlite_cache import SQLiteTranslationCache


class CacheSourcePreservationTests(unittest.TestCase):
    def test_original_swears_and_existing_asterisks_are_preserved_independently(self):
        class Model:
            def translate_segments(self, texts, source, target):
                return ['测试译文'] * len(texts)

        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / 'cache.sqlite3'
            cache = SQLiteTranslationCache(path)
            provider = OfflineTranslationProvider(Model(), cache)
            originals = ['ты долбоёб и я ухожу', 'ты ****** и я ухожу']
            payload = {'events': [dict(event_type='chat_message',player_id=1,game_time=index,
                                      channel_type=11,message=text) for index,text in enumerate(originals)]}
            observations = GsiChatReader().read(json.dumps(payload,ensure_ascii=False).encode('utf-8'))
            self.assertEqual([row.text for row in observations], originals)
            for row in observations:
                provider.translate(row.text, row.language)
            with closing(sqlite3.connect(path)) as connection:
                stored = connection.execute('SELECT key,source FROM translations ORDER BY rowid').fetchall()
            self.assertEqual([row[1] for row in stored], originals)
            self.assertNotIn('*', stored[0][0])
            self.assertIn('******', stored[1][0])
