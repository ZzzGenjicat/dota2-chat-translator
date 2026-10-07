import sys
import unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from dota2_map_assistant.outgoing_translation import OutgoingTranslator

class RecordingProvider:
    def __init__(self): self.calls = []
    def translate(self, text, language):
        self.calls.append((text, language))
        return 'иди на мид'

class OutgoingTranslationTests(unittest.TestCase):
    def test_old_backend_labels_still_use_the_injected_local_provider(self):
        provider = RecordingProvider()
        outgoing = OutgoingTranslator(provider)
        for label in ('offline', 'free', 'chatgpt'):
            self.assertEqual(outgoing.translate('  去中路  ', label, 'old-model'), 'иди на мид')
        self.assertEqual(provider.calls, [('去中路', 'zho')] * 3)

    def test_empty_source_is_rejected_before_inference(self):
        provider = RecordingProvider()
        with self.assertRaisesRegex(ValueError, '中文'):
            OutgoingTranslator(provider).translate('   ')
        self.assertEqual(provider.calls, [])

if __name__ == '__main__': unittest.main()
