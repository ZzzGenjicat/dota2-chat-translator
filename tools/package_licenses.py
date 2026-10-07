"""Collect the licenses that travel with each native application bundle."""
from importlib import metadata
import json
from pathlib import Path
import platform
import shutil
import sys
from urllib.request import urlopen


def collect_licenses(root: Path) -> None:
    destination = root / 'build' / 'third-party-licenses'
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
    windows_python_license = Path(sys.base_prefix) / 'LICENSE.txt'
    if sys.platform == 'win32' and windows_python_license.is_file():
        shutil.copy2(windows_python_license, destination / 'Python-Windows-bundled-licenses.txt')
    (destination / 'versions.json').write_text(json.dumps(versions, indent=2), encoding='utf-8')
