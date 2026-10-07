"""Build, relocate-test and package a native offline macOS app."""
from __future__ import annotations

import argparse
import hashlib
from importlib import metadata
import json
from pathlib import Path
import platform
import shutil
import subprocess
import sys
import tempfile
import tomllib
from urllib.request import urlopen

from install_offline_model import install

ROOT = Path(__file__).resolve().parents[1]


def collect_licenses() -> None:
    destination = ROOT / 'build' / 'third-party-licenses'
    destination.mkdir(parents=True, exist_ok=True)
    versions = {}
    for name in ('ctranslate2', 'sentencepiece', 'numpy', 'Pillow', 'certifi', 'pyinstaller'):
        distribution = metadata.distribution(name)
        versions[name] = distribution.version
        for entry in distribution.files or ():
            if entry.name.lower().startswith(('license', 'copying', 'notice')):
                source = Path(distribution.locate_file(entry))
                if source.is_file():
                    target = destination / name / Path(*[part for part in entry.parts if part not in ('.', '..')])
                    target.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(source, target)
    # Some binary wheels omit upstream license texts. Include their pinned originals.
    license_urls = {
        'CTranslate2-MIT.txt': 'https://raw.githubusercontent.com/OpenNMT/CTranslate2/v4.8.2/LICENSE',
        'SentencePiece-Apache-2.0.txt': 'https://raw.githubusercontent.com/google/sentencepiece/v0.2.1/LICENSE',
        'M2M100-Meta-MIT.txt': 'https://raw.githubusercontent.com/facebookresearch/fairseq/v0.12.2/LICENSE',
        'Python-PSF.txt': f'https://raw.githubusercontent.com/python/cpython/v{platform.python_version()}/LICENSE',
        'Tcl.txt': 'https://raw.githubusercontent.com/tcltk/tcl/core-8-6-16/license.terms',
        'Tk.txt': 'https://raw.githubusercontent.com/tcltk/tk/core-8-6-16/license.terms',
    }
    for name, url in license_urls.items():
        with urlopen(url, timeout=60) as response:
            data = response.read(1024 * 1024)
        (destination / name).write_bytes(data)
    (destination / 'versions.json').write_text(json.dumps(versions, indent=2), encoding='utf-8')


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
    collect_licenses()
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
