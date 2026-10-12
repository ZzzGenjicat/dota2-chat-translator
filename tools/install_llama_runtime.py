"""Download fixed, checksum-verified native llama.cpp runtime binaries."""
from __future__ import annotations
import argparse
from pathlib import Path
import platform
import shutil
import sys
import tarfile
import tempfile
import zipfile

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'src'))
from dota2_map_assistant.local_model_install import download_verified, checksum
TAG='b11429'
ASSETS={
 ('win32','amd64'):('llama-b11429-bin-win-cpu-x64.zip',19398918,'1283323272b04cd07905816a597a0da810918102de958f4ff6f7bbaa70ed2efe'),
 ('darwin','arm64'):('llama-b11429-bin-macos-arm64.tar.gz',11971406,'740288ec6887be94280a5dfa25b5e23a78285cab104519e6c7e218904ee82459'),
 ('darwin','x86_64'):('llama-b11429-bin-macos-x64.tar.gz',11487431,'29ac3ea02be6bd143e824973f2cc5fa74bc4094393a9eaab0ff6814f19dd8522'),
}

def install(destination: Path, verify_only=False):
    arch=platform.machine().lower()
    if arch=='x86_64' and sys.platform=='win32': arch='amd64'
    try: name,size,digest=ASSETS[(sys.platform,arch)]
    except KeyError: raise RuntimeError('需要 Windows x64、Apple 芯片 Mac 或 Intel Mac 原生运行环境')
    archive=ROOT/'build/runtime-downloads'/name
    if verify_only:
        if not archive.is_file() or checksum(archive)!=digest:
            raise RuntimeError('Pinned native runtime archive is missing or invalid')
    else:
        download_verified(f'https://github.com/ggml-org/llama.cpp/releases/download/{TAG}/{name}',
                          archive,size,digest,progress=lambda message:print(message,flush=True))
    destination.mkdir(parents=True,exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='runtime-',dir=archive.parent) as folder:
        extracted=Path(folder)
        if name.endswith('.zip'):
            with zipfile.ZipFile(archive) as bundle:
                for member in bundle.infolist():
                    if not (extracted/member.filename).resolve().is_relative_to(extracted.resolve()):
                        raise RuntimeError('Unsafe runtime archive path')
                bundle.extractall(extracted)
        else:
            with tarfile.open(archive) as bundle:
                bundle.extractall(extracted,filter='data')
        server=next(extracted.rglob('llama-server.exe' if sys.platform=='win32' else 'llama-server'))
        for path in server.parent.iterdir():
            if path.name==server.name or path.suffix.lower() in ('.dll','.dylib') or path.name.startswith('LICENSE'):
                shutil.copy2(path,destination/path.name)
        (destination/server.name).chmod(0o755)
    print(f'Verified CPU runtime: {TAG} {sys.platform}/{arch}',flush=True)

if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--verify-only',action='store_true')
    args=p.parse_args()
    install(ROOT/'vendor/llama',args.verify_only)
