from pathlib import Path
from PyInstaller.utils.hooks import collect_all

root = Path(SPECPATH).parent
model = root / 'models' / 'm2m100-418m-ct2-int8'
if not (model / 'model.bin').is_file():
    raise RuntimeError('Install and verify the model before building')
ct_data, ct_bins, ct_imports = collect_all('ctranslate2')
sp_data, sp_bins, sp_imports = collect_all('sentencepiece')
# CUDA/cuDNN are optional dynamic backends; this distribution is CPU only.
ct_bins = [(source, target) for source, target in ct_bins
           if not Path(source).name.lower().startswith(('cudnn', 'cublas', 'cudart'))]
a = Analysis(
    [str(root / 'run.py')], pathex=[str(root / 'src')],
    binaries=ct_bins + sp_bins,
    datas=ct_data + sp_data + [(str(model), 'models/m2m100-418m-ct2-int8'),
                             (str(root / 'data' / 'dota_chat_lexicon.json'), 'data'),
                             (str(root / 'packaging' / 'MODEL_NOTICE.txt'), '.'),
                             (str(root / 'packaging' / 'WINDOWS_FIRST_USE.txt'), '.'),
                             (str(root / 'build' / 'third-party-licenses'), 'THIRD_PARTY_LICENSES')],
    hiddenimports=ct_imports + sp_imports,
    excludes=['torch', 'transformers', 'tensorflow', 'pytest', 'psutil'],
    noarchive=False,
)
# Hooks can collect optional DLLs again while scanning dependencies.
a.binaries = [entry for entry in a.binaries
              if not Path(entry[0]).name.lower().startswith(('cudnn', 'cublas', 'cudart'))]
a.datas = [entry for entry in a.datas
           if not Path(entry[0]).name.lower().startswith(('cudnn', 'cublas', 'cudart'))]
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name='Dota2ChatTranslator',
          debug=False, strip=False, upx=False, console=False)
bundle = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name='Dota2ChatTranslator')
