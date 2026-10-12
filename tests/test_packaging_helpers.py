"""Headless packaging helper checks; no native builders or UI are executed."""
import importlib.util
from pathlib import Path
import subprocess
import sys
import unittest
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]


class PackagingHelperTests(unittest.TestCase):
    def module(self):
        with patch.object(sys,'path',[str(ROOT/'tools'),*sys.path]):
            spec=importlib.util.spec_from_file_location('dota_macos_packaging',ROOT/'tools/build_macos.py')
            module=importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            return module

    def test_transient_dmg_resource_busy_is_retried(self):
        m=self.module()
        results=[subprocess.CompletedProcess([],1,'','hdiutil: create failed - Resource busy'),
                 subprocess.CompletedProcess([],0,'','')]
        with patch.object(m.subprocess,'run',side_effect=results) as run,patch.object(m.time,'sleep'):
            m.create_dmg(Path('stage'),Path('package.dmg'))
            self.assertEqual(run.call_count,2)

    def test_other_dmg_errors_are_reported_immediately(self):
        m=self.module()
        with patch.object(m.subprocess,'run',return_value=subprocess.CompletedProcess([],1,'','No space left')) as run:
            with self.assertRaisesRegex(RuntimeError,'No space left'):
                m.create_dmg(Path('stage'),Path('package.dmg'))
            run.assert_called_once()
