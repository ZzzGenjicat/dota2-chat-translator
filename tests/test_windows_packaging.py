import sys
import unittest
from unittest.mock import patch

from dota2_map_assistant.platform_runtime import offline_install_hint


class WindowsPackagingTests(unittest.TestCase):
    def test_frozen_windows_missing_model_points_to_installer(self):
        with patch.object(sys, 'platform', 'win32'), patch.object(sys, 'frozen', True, create=True):
            hint = offline_install_hint()
        self.assertIn('Windows 安装包', hint)
        self.assertNotIn('.ps1', hint)
        self.assertNotIn('Python', hint)


if __name__ == '__main__':
    unittest.main()
