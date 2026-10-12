# Build only on the native macOS architecture. No temporary model extraction.
from pathlib import Path
import platform
import tomllib

root = Path(SPECPATH).parent
runtime = root / 'vendor' / 'llama'
version = tomllib.loads((root / 'pyproject.toml').read_text(encoding='utf-8'))['project']['version']
if not (runtime / 'llama-server').is_file():
    raise RuntimeError('Install the verified native CPU runtime before building')
a = Analysis(
    [str(root / 'run.py')], pathex=[str(root / 'src')],
    binaries=[(str(path), 'vendor/llama') for path in runtime.iterdir()
              if path.name == 'llama-server' or path.suffix == '.dylib'],
    datas=[
                             (str(root / 'data' / 'dota_chat_lexicon.json'), 'data'),
                             (str(root / 'packaging' / 'MODEL_NOTICE.txt'), '.'),
                             (str(root / 'build' / 'third-party-licenses'), 'THIRD_PARTY_LICENSES')],
    hiddenimports=[],
    excludes=['torch', 'transformers', 'tensorflow', 'pytest', 'ctranslate2', 'sentencepiece', 'numpy'],
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name='Dota2ChatTranslator',
          debug=False, strip=False, upx=False, console=False,
          target_arch=platform.machine(), codesign_identity=None)
bundle = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name='Dota2ChatTranslator')
app = BUNDLE(bundle, name='Dota2ChatTranslator.app',
             bundle_identifier='io.github.ZzzGenjicat.dota2-chat-translator',
             info_plist={'CFBundleName': 'Dota 2 聊天翻译',
                         'CFBundleDisplayName': 'Dota 2 聊天翻译',
                         'CFBundleShortVersionString': version, 'CFBundleVersion': version,
                         'LSMinimumSystemVersion': '14.0', 'NSHighResolutionCapable': True})
