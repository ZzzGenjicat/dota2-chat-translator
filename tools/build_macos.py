"""Build, relocate-test and package a native offline macOS app."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import platform
import shutil
import subprocess
import sys
import tempfile
import tomllib

from install_offline_model import install
from package_licenses import collect_licenses

ROOT = Path(__file__).resolve().parents[1]


def build(expected_arch: str) -> None:
    if sys.platform != 'darwin' or platform.machine() != expected_arch:
        raise RuntimeError(f'Build on a native {expected_arch} Mac; cross-compilation is not supported')
    import ctranslate2
    import tkinter as tk
    if 'int8' not in ctranslate2.get_supported_compute_types('cpu'):
        raise RuntimeError('The native CTranslate2 wheel does not support CPU INT8')
    test_root = tk.Tk()
    test_root.destroy()
    install(ROOT / 'models' / 'm2m100-418m-ct2-int8', verify_only=True)
    collect_licenses(ROOT)
    subprocess.run([sys.executable, '-m', 'PyInstaller', '--noconfirm',
                    '--distpath', str(ROOT / 'dist'), '--workpath', str(ROOT / 'build' / 'pyinstaller'),
                    str(ROOT / 'packaging' / 'macos.spec')], cwd=ROOT, check=True)
    app = ROOT / 'dist' / 'Dota2ChatTranslator.app'
    subprocess.run(['codesign', '--verify', '--deep', '--strict', str(app)], check=True)
    release = ROOT / 'release'
    release.mkdir(exist_ok=True)
    version = tomllib.loads((ROOT / 'pyproject.toml').read_text(encoding='utf-8'))['project']['version']
    stem = f'Dota2ChatTranslator-{version}-macos-{expected_arch}'
    report = release / f'{stem}-smoke.json'
    # Run outside the checkout and without a build-machine PYTHONPATH.
    import os
    clean_env = dict(os.environ)
    clean_env.pop('PYTHONPATH', None)
    for key in ('DOTA2_TRANSLATOR_DATA_DIR', 'DOTA2_TRANSLATOR_STEAM_DIR', 'DOTA2_TRANSLATOR_DOTA_DIR'):
        clean_env.pop(key, None)
    with tempfile.TemporaryDirectory(prefix='移动后 with spaces ') as moved:
        moved_app = Path(moved) / 'Dota2ChatTranslator.app'
        shutil.copytree(app, moved_app, symlinks=True)
        subprocess.run([str(moved_app / 'Contents' / 'MacOS' / 'Dota2ChatTranslator'),
                        '--self-test', '--report', str(report)], cwd=moved, env=clean_env,
                       check=True, timeout=180)
    if not json.loads(report.read_text(encoding='utf-8'))['passed']:
        raise RuntimeError('Relocated frozen app self-test failed')
    with tempfile.TemporaryDirectory(prefix='dota-dmg-') as stage:
        stage = Path(stage)
        shutil.copytree(app, stage / app.name, symlinks=True)
        (stage / 'Applications').symlink_to('/Applications', target_is_directory=True)
        shutil.copy2(ROOT / 'packaging' / 'MAC_FIRST_USE.txt', stage / '首次使用说明.txt')
        subprocess.run(['hdiutil', 'create', '-volname', 'Dota 2 本地聊天翻译', '-srcfolder', str(stage),
                        '-format', 'UDZO', '-ov', str(release / f'{stem}.dmg')], check=True, timeout=600)
    dmg = release / f'{stem}.dmg'
    with dmg.open('rb') as stream:
        digest = hashlib.file_digest(stream, 'sha256').hexdigest()
    (release / f'{stem}.sha256').write_text(f'{digest}  {dmg.name}\n', encoding='utf-8')
    print(f'Verified native installer: {dmg.name}', flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--arch', choices=('arm64', 'x86_64'), required=True)
    args = parser.parse_args()
    build(args.arch)
