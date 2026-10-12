"""Explicit one-time model download. The translator never imports this tool."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
from dota2_map_assistant.local_model_install import install as _install, MODEL_FOLDER, NATIVE_FIXTURE


def install(destination: Path, verify_only: bool = False, *, native_fixture=False) -> None:
    _install(destination, verify_only, progress=lambda message: print(message, flush=True),
             specification=NATIVE_FIXTURE if native_fixture else None)
    print('Local offline model verified.', flush=True)


if __name__ == '__main__':
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, 'reconfigure'):
            stream.reconfigure(encoding='utf-8')
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--model-dir', type=Path)
    parser.add_argument('--verify-only', action='store_true')
    parser.add_argument('--native-fixture', action='store_true', help=argparse.SUPPRESS)
    options = parser.parse_args()
    try:
        destination=options.model_dir or ROOT / 'models' / (NATIVE_FIXTURE['folder'] if options.native_fixture else MODEL_FOLDER)
        install(destination.resolve(), options.verify_only, native_fixture=options.native_fixture)
    except Exception as exc:
        print(str(exc), file=sys.stderr)
        sys.exit(1)
