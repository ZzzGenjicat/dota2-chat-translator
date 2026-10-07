"""Run regressions; isolate macOS Tk lifetimes in separate processes."""
from __future__ import annotations

import faulthandler
import os
from pathlib import Path
import subprocess
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]


def cases(suite):
    for item in suite:
        if isinstance(item, unittest.TestSuite):
            yield from cases(item)
        else:
            yield item


def main() -> int:
    sys.path.insert(0, str(ROOT / 'src'))
    all_cases = list(cases(unittest.defaultTestLoader.discover(str(ROOT / 'tests'))))
    # Tk Aqua shares native application/event state across Tcl interpreters.
    # Repeatedly creating/destroying Tk roots across unrelated tests can hang.
    isolated = [case for case in all_cases if sys.platform == 'darwin' and
                case.__class__.__module__ == 'test_translator_ui_text']
    regular = [case for case in all_cases if case not in isolated]
    faulthandler.dump_traceback_later(45, repeat=True)
    result = unittest.TextTestRunner(verbosity=2).run(unittest.TestSuite(regular))
    faulthandler.cancel_dump_traceback_later()
    failed = not result.wasSuccessful()
    environment = dict(os.environ)
    environment['PYTHONPATH'] = os.pathsep.join([str(ROOT / 'src'), str(ROOT / 'tests'), environment.get('PYTHONPATH', '')])
    for case in isolated:
        print(f'Isolated Mac UI test: {case.id()}', flush=True)
        command = ('import faulthandler, unittest; faulthandler.dump_traceback_later(45); '
                   f'unittest.main(module=None, argv=["unittest", {case.id()!r}, "-v"])')
        try:
            child = subprocess.run([sys.executable, '-u', '-X', 'utf8', '-c', command],
                                   cwd=ROOT, env=environment, timeout=75)
            failed |= child.returncode != 0
        except subprocess.TimeoutExpired:
            print(f'FAIL: {case.id()} exceeded 75 seconds', flush=True)
            failed = True
    print(f'Regression cases: {len(all_cases)}; isolated Mac UI cases: {len(isolated)}; passed: {not failed}', flush=True)
    return int(failed)


if __name__ == '__main__':
    sys.exit(main())
