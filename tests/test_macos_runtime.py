import os
import sys
import unittest
import tempfile
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from dota2_map_assistant import platform_runtime
from dota2_map_assistant import translator_app
from dota2_map_assistant.offline_translation import M2M100Engine


class MacRuntimeTests(unittest.TestCase):
    def test_reloading_model_does_not_keep_lowering_priority(self):
        priority = [0]
        def set_priority(_which, _who, value):
            priority[0] = value
        with patch.object(sys, 'platform', 'darwin'), \
             patch.object(os, 'PRIO_PROCESS', 0, create=True), \
             patch.object(os, 'getpriority', side_effect=lambda *_: priority[0], create=True), \
             patch.object(os, 'setpriority', side_effect=set_priority, create=True), \
             patch.object(os, 'nice', side_effect=AssertionError('relative priority accumulates'), create=True):
            platform_runtime.configure_low_impact_runtime()
            platform_runtime.configure_low_impact_runtime()
        self.assertEqual(priority[0], 5)

    def test_existing_lower_priority_is_preserved(self):
        with patch.object(sys, 'platform', 'darwin'), \
             patch.object(os, 'PRIO_PROCESS', 0, create=True), \
             patch.object(os, 'getpriority', return_value=12, create=True), \
             patch.object(os, 'setpriority', side_effect=AssertionError('must not raise priority'), create=True), \
             patch.object(os, 'nice', side_effect=AssertionError('must not accumulate'), create=True):
            platform_runtime.configure_low_impact_runtime()

    def test_mac_uses_available_chinese_font_and_real_default_fallback(self):
        choose = getattr(translator_app, '_choose_ui_font', None)
        self.assertIsNotNone(choose, 'Mac font selection is missing')
        self.assertEqual(choose({'PingFang SC', 'Helvetica'}, 'Helvetica', 'darwin'), 'PingFang SC')
        self.assertEqual(choose({'Helvetica'}, 'Helvetica', 'darwin'), 'Helvetica')
        self.assertEqual(choose({'Microsoft YaHei UI', 'Segoe UI'}, 'Arial', 'win32'), 'Microsoft YaHei UI')

    def test_mac_missing_model_guidance_does_not_name_windows_installer(self):
        hint = getattr(platform_runtime, 'offline_install_hint', None)
        self.assertIsNotNone(hint, 'Platform installation guidance is missing')
        with patch.object(sys, 'platform', 'darwin'):
            self.assertIn('Mac', hint())
            self.assertNotIn('.ps1', hint())
        with patch.object(sys, 'platform', 'win32'):
            self.assertIn('install_offline.ps1', hint())

    def test_mac_missing_tokenizer_has_actionable_mac_guidance(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(sys, 'platform', 'darwin'):
            engine = M2M100Engine(Path(tmp))
            try:
                with self.assertRaisesRegex(RuntimeError, 'Mac'):
                    engine.validate_text('短句')
            finally:
                engine.close()
