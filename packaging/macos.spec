# Build only on the native macOS architecture. No temporary model extraction.
from pathlib import Path
import platform
import sys
import tomllib
from PyInstaller.utils.hooks import collect_all

root = Path(SPECPATH).parent
sys.path.insert(0, str(root / 'src'))
from dota2_map_assistant.lightweight_model_install import FILES, MODEL_FOLDER
model = root / 'models' / MODEL_FOLDER
version = tomllib.loads((root / 'pyproject.toml').read_text(encoding='utf-8'))['project']['version']
if not all((model / name).is_file() for name in (*FILES, 'manifest.json')):
    raise RuntimeError('Install and verify the complete M2M100 INT8 model before building')
ct_data, ct_bins, ct_imports = collect_all('ctranslate2')
sp_data, sp_bins, sp_imports = collect_all('sentencepiece')
ct_bins = [(source, target) for source, target in ct_bins
           if not Path(source).name.lower().startswith(('cudnn', 'cublas', 'cudart'))]
a = Analysis(
    [str(root / 'run.py')], pathex=[str(root / 'src')],
    binaries=ct_bins + sp_bins,
    datas=ct_data + sp_data + [(str(model / name), 'models/' + MODEL_FOLDER) for name in (*FILES, 'manifest.json')] + [
                             (str(root / 'data' / 'dota_chat_lexicon.json'), 'data'),
                             (str(root / 'assets' / 'app-icon.png'), 'assets'),
                             (str(root / 'packaging' / 'MODEL_NOTICE.txt'), '.'),
                             (str(root / 'build' / 'third-party-licenses-m2m100'), 'THIRD_PARTY_LICENSES')],
    hiddenimports=ct_imports + sp_imports + ['numpy'],
    excludes=['torch', 'transformers', 'tensorflow', 'pytest', 'psutil', 'dota2_map_assistant.llm_translation'],
    noarchive=False,
)
a.binaries = [entry for entry in a.binaries
              if not Path(entry[0]).name.lower().startswith(('cudnn', 'cublas', 'cudart'))]
a.datas = [entry for entry in a.datas
           if not Path(entry[0]).name.lower().startswith(('cudnn', 'cublas', 'cudart'))]
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name='Dota2ChatTranslator',
          debug=False, strip=False, upx=False, console=False,
          target_arch=platform.machine(), codesign_identity=None)
bundle = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name='Dota2ChatTranslator')
app = BUNDLE(bundle, name='刀塔翻译.app', icon=str(root / 'assets' / 'app-icon.icns'),
             bundle_identifier='io.github.ZzzGenjicat.dota2-chat-translator',
             info_plist={'CFBundleName': '刀塔翻译',
                         'CFBundleDisplayName': '刀塔翻译',
                         'CFBundleShortVersionString': version, 'CFBundleVersion': version,
                         'LSMinimumSystemVersion': '14.0', 'NSHighResolutionCapable': True})
