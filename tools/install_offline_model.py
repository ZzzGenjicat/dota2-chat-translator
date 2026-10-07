"""Explicit one-time model download. The translator never imports this tool."""
from __future__ import annotations

import argparse
import hashlib
import json
import ssl
import sys
from pathlib import Path
from urllib.request import urlopen

ROOT = Path(__file__).resolve().parents[1]
REPOSITORY = 'JustFrederik/m2m_100_418m_ct2_int8'
REVISION = '1aeed44db4dd61a486bba44acf54c76507082a2c'
FILES = {
    'model.bin': '6c8ef4e814a02d5947ab80710f93f3d4e776bf4b668ab3bb7996ff050b69d111',
    'spm.128k.model': 'd8f7c76ed2a5e0822be39f0a4f95a55eb19c78f4593ce609e2edbc2aea4d380a',
    'config.json': '531f7f8d8bf8b73a1580b73c04b58bf134ac490284f4f7af6c20ad238499ae37',
    'shared_vocabulary.txt': 'bd440aa21b8ca3453fc792a0018a1f3fe68b3464aadddd4d16a4b72f73c86d8c',
}


def checksum(path: Path) -> str:
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def install(destination: Path, verify_only: bool = False) -> None:
    destination.mkdir(parents=True, exist_ok=True)
    context = ssl.create_default_context()
    for name, expected in FILES.items():
        target = destination / name
        if target.is_file() and checksum(target) == expected:
            print(f'{name}: SHA256 OK', flush=True)
            continue
        if verify_only:
            raise RuntimeError(f'{name}: missing or SHA256 mismatch')
        temporary = target.with_suffix(target.suffix + '.part')
        print(f'Downloading {name}...', flush=True)
        # HTTPS, pinned revision, no remote code execution. Keep original files
        # until the complete replacement passes integrity verification.
        with urlopen(f'https://huggingface.co/{REPOSITORY}/resolve/{REVISION}/{name}',
                     timeout=60, context=context) as response, temporary.open('wb') as output:
            while block := response.read(1024 * 1024):
                output.write(block)
        if checksum(temporary) != expected:
            raise RuntimeError(f'{name}: SHA256 mismatch; incomplete file retained as .part')
        temporary.replace(target)
    manifest = {'base_model': 'facebook/m2m100_418M', 'repository': REPOSITORY,
                'revision': REVISION, 'quantization': 'int8', 'sha256': FILES}
    (destination / 'manifest.json').write_text(json.dumps(manifest, indent=2), encoding='utf-8')
    print('Local offline model verified.', flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--model-dir', type=Path, default=ROOT / 'models' / 'm2m100-418m-ct2-int8')
    parser.add_argument('--verify-only', action='store_true')
    options = parser.parse_args()
    try:
        install(options.model_dir.resolve(), options.verify_only)
    except Exception as exc:
        print(str(exc), file=sys.stderr)
        sys.exit(1)
