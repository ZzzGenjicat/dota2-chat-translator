"""One-time verified model provisioning. Translation never calls remote APIs."""
from __future__ import annotations
import hashlib
import json
from pathlib import Path
import ssl
import threading
import urllib.request
import certifi

REPOSITORY = 'tencent/Hy-MT2-30B-A3B-GGUF'
REVISION = 'fd3dcbb6b31e9e03923ef4f9f42500f74c3851c3'
MODEL_FOLDER = 'hy-mt2-30b-a3b-q4'
FILES = {
    'Hy-MT2-30B-A3B-Q4_K_M.gguf':
        (18236702880, 'bb44b11bb0f7cd3d1321645b41e911cd3de2e473731227fc6bf37aa18b543f88'),
}
# Hosted native CI has too little memory for the production 30B model. This
# smaller model tests the same Hy-MT architecture, prompt and decoder path;
# it is a packaging fixture, never an automatic translation fallback.
NATIVE_FIXTURE = {
    'folder': 'hy-mt2-1.8b-q4', 'repository': 'tencent/Hy-MT2-1.8B-GGUF',
    'revision': 'a0c709d9fac510f2c807aa3af52872340dc37a4a',
    'files': {'Hy-MT2-1.8B-Q4_K_M.gguf':
              (1133080448, 'dc5f44fcf1fa496ee7ad725982c0c8c553a4de00259b53af84c4b89fb0c06699')},
}


def checksum(path: Path) -> str:
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def sample_fingerprint(path: Path) -> str:
    """Cheap modification check when Windows preserves a coarse write time.

    Full SHA256 is still verified at installation. Startup reads at most
    192 KiB instead of hashing the entire 17 GiB model every time.
    """
    size = path.stat().st_size
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        for offset in sorted({0, max(0, size // 2 - 32768), max(0, size - 65536)}):
            stream.seek(offset)
            digest.update(offset.to_bytes(8, 'little'))
            digest.update(stream.read(65536))
    return digest.hexdigest()


def is_installed(destination: Path, *, specification: dict | None = None) -> bool:
    repository, revision, files = REPOSITORY, REVISION, FILES
    quantization = 'Q4_K_M'
    if specification is not None:
        repository, revision, files = (specification[key] for key in ('repository', 'revision', 'files'))
        quantization = specification.get('quantization', quantization)
    try:
        manifest_path = destination / 'manifest.json'
        if manifest_path.stat().st_size > 256 * 1024:
            return False
        receipt = json.loads(manifest_path.read_text(encoding='utf-8'))
        if (receipt.get('revision') != revision or receipt.get('repository') != repository
                or receipt.get('quantization') != quantization):
            return False
        for name, (size, digest) in files.items():
            stat = (destination / name).stat()
            record = receipt['files'][name]
            if not (stat.st_size == record['size'] == size and record['sha256'] == digest
                    and stat.st_mtime_ns == record['mtime_ns']
                    and record.get('sample_sha256') == sample_fingerprint(destination / name)):
                return False
        return True
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        return False


def download_verified(url: str, target: Path, size: int, digest: str,
                      progress=lambda message: None, cancel: threading.Event | None = None) -> None:
    if target.is_file() and target.stat().st_size == size and checksum(target) == digest:
        return
    target.parent.mkdir(parents=True, exist_ok=True)
    partial = target.with_suffix(target.suffix + '.part')
    offset = partial.stat().st_size if partial.is_file() else 0
    if offset > size:
        offset = 0
    headers = {'User-Agent': 'Dota2ChatTranslator-model-installer'}
    if offset:
        headers['Range'] = f'bytes={offset}-'
    opener = download_opener()
    req = urllib.request.Request(url, headers=headers)
    if offset != size:
        with opener.open(req, timeout=30) as response:
            if not response.url.startswith('https://'):
                raise RuntimeError('模型下载必须使用 HTTPS')
            if offset and (response.status != 206 or not response.headers.get('Content-Range', '').startswith(f'bytes {offset}-')):
                offset = 0
            done = offset
            with partial.open('ab' if offset else 'wb') as output:
                while block := response.read(1024 * 1024):
                    if cancel is not None and cancel.is_set():
                        raise RuntimeError('模型下载已暂停，下次打开可继续')
                    done += len(block)
                    if done > size:
                        raise RuntimeError('下载文件大小异常')
                    output.write(block)
                    progress(f'首次准备本地模型：{done / 2**20:.0f}/{size / 2**20:.0f} MB')
    if partial.stat().st_size != size:
        raise RuntimeError('下载尚未完成，下次可继续')
    progress('正在校验本地模型…')
    if checksum(partial) != digest:
        partial.unlink()  # Only this installer's known corrupt partial file.
        raise RuntimeError('模型校验失败，请重试下载')
    partial.replace(target)


def download_opener():
    # Frozen Python on Mac/Windows can have an empty system CA store. Ship a
    # verified CA bundle; never disable HTTPS verification for model downloads.
    context = ssl.create_default_context(cafile=certifi.where())
    return urllib.request.build_opener(urllib.request.HTTPSHandler(context=context))


def install(destination: Path, verify_only=False, *, progress=lambda message: None,
            cancel: threading.Event | None = None, specification: dict | None = None) -> None:
    repository, revision, files = REPOSITORY, REVISION, FILES
    quantization = 'Q4_K_M'
    if specification is not None:
        repository, revision, files = (specification[key] for key in ('repository', 'revision', 'files'))
        quantization = specification.get('quantization', quantization)
    destination.mkdir(parents=True, exist_ok=True)
    for index, (name, (size, digest)) in enumerate(files.items(), 1):
        target = destination / name
        if verify_only:
            if not target.is_file() or target.stat().st_size != size or checksum(target) != digest:
                raise RuntimeError(f'{name}: missing or SHA256 mismatch')
        else:
            download_verified(f'https://huggingface.co/{repository}/resolve/{revision}/{name}',
                              target, size, digest,
                              progress=lambda message: progress(f'[{index}/{len(files)}] {message}'), cancel=cancel)
    manifest = {'repository': repository,
                'revision': revision, 'quantization': quantization, 'files': {
                    name: {'size': size, 'sha256': digest, 'mtime_ns': (destination / name).stat().st_mtime_ns,
                           'sample_sha256': sample_fingerprint(destination / name)}
                    for name, (size, digest) in files.items()}}
    temporary = destination / 'manifest.json.tmp'
    temporary.write_text(json.dumps(manifest, indent=2), encoding='utf-8')
    temporary.replace(destination / 'manifest.json')
    progress('本地模型准备完成，后续翻译全程离线')
