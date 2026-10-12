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
    for name in ('Pillow', 'certifi', 'psutil', 'pyinstaller'):
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
        'llama.cpp-MIT.txt': 'https://raw.githubusercontent.com/ggml-org/llama.cpp/b11429/LICENSE',
        'Hy-MT2-Apache-2.0.txt': 'https://huggingface.co/tencent/Hy-MT2-30B-A3B-GGUF/resolve/fd3dcbb6b31e9e03923ef4f9f42500f74c3851c3/LICENSE.txt',
        'Python-PSF.txt': f'https://raw.githubusercontent.com/python/cpython/v{platform.python_version()}/LICENSE',
        'Tcl.txt': 'https://raw.githubusercontent.com/tcltk/tcl/core-8-6-16/license.terms',
        'Tk.txt': 'https://raw.githubusercontent.com/tcltk/tk/core-8-6-16/license.terms',
    }
    for name, url in license_urls.items():
        with urlopen(url, timeout=60) as response:
            data = response.read(1024 * 1024)
        (destination / name).write_bytes(data)
    for path in (root / 'vendor' / 'llama').glob('LICENSE*'):
        runtime_licenses = destination / 'llama-runtime'
        runtime_licenses.mkdir(exist_ok=True)
        shutil.copy2(path, runtime_licenses / path.name)
    windows_python_license = Path(sys.base_prefix) / 'LICENSE.txt'
    if sys.platform == 'win32' and windows_python_license.is_file():
        shutil.copy2(windows_python_license, destination / 'Python-Windows-bundled-licenses.txt')
    (destination / 'versions.json').write_text(json.dumps(versions, indent=2), encoding='utf-8')
