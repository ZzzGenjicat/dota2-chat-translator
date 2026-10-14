"""Pinned M2M100 418M CTranslate2 INT8 model provisioning for the shipped app."""
from __future__ import annotations

from pathlib import Path
import threading

from . import local_model_install

MODEL_FOLDER = 'm2m100-418m-ct2-int8'
REPOSITORY = 'JustFrederik/m2m_100_418m_ct2_int8'
REVISION = '1aeed44db4dd61a486bba44acf54c76507082a2c'
FILES = {
    'model.bin': (490667638, '6c8ef4e814a02d5947ab80710f93f3d4e776bf4b668ab3bb7996ff050b69d111'),
    'spm.128k.model': (2423393, 'd8f7c76ed2a5e0822be39f0a4f95a55eb19c78f4593ce609e2edbc2aea4d380a'),
    'config.json': (158, '531f7f8d8bf8b73a1580b73c04b58bf134ac490284f4f7af6c20ad238499ae37'),
    'shared_vocabulary.txt': (1159930, 'bd440aa21b8ca3453fc792a0018a1f3fe68b3464aadddd4d16a4b72f73c86d8c'),
}
SPECIFICATION = {
    'folder': MODEL_FOLDER,
    'repository': REPOSITORY,
    'revision': REVISION,
    'quantization': 'int8',
    'files': FILES,
}


def is_installed(destination: Path) -> bool:
    return local_model_install.is_installed(destination, specification=SPECIFICATION)


def verify(destination: Path) -> None:
    """Check packaged files without changing a signed or read-only bundle."""
    for name, (size, digest) in SPECIFICATION['files'].items():
        target = destination / name
        if (not target.is_file() or target.stat().st_size != size
                or local_model_install.checksum(target) != digest):
            raise RuntimeError(f'{name}: missing or SHA256 mismatch')


def install(destination: Path, verify_only: bool = False, *, progress=lambda message: None,
            cancel: threading.Event | None = None) -> None:
    local_model_install.install(destination, verify_only, progress=progress, cancel=cancel,
                                specification=SPECIFICATION)
