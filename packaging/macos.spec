# Build only on the native macOS architecture. No temporary model extraction.
from pathlib import Path
import platform
import tomllib
from PyInstaller.utils.hooks import collect_all

root = Path(SPECPATH).parent
model = root / 'models' / 'm2m100-418m-ct2-int8'
version = tomllib.loads((root / 'pyproject.toml').read_text(encoding='utf-8'))['project']['version']
if not (model / 'model.bin').is_file():
    raise RuntimeError('Install and verify the model before building')
ct_data, ct_bins, ct_imports = collect_all('ctranslate2')
sp_data, sp_bins, sp_imports = collect_all('sentencepiece')
a = Analysis(
    [str(root / 'run.py')], pathex=[str(root / 'src')],
    binaries=ct_bins + sp_bins,
    datas=ct_data + sp_data + [(str(model), 'models/m2m100-418m-ct2-int8'),
                             (str(root / 'data' / 'dota_chat_lexicon.json'), 'data'),
                             (str(root / 'packaging' / 'MODEL_NOTICE.txt'), '.'),
                             (str(root / 'build' / 'third-party-licenses'), 'THIRD_PARTY_LICENSES')],
    hiddenimports=ct_imports + sp_imports,
    excludes=['torch', 'transformers', 'tensorflow', 'pytest', 'psutil'],
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
