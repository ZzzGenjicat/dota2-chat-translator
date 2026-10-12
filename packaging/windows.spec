from pathlib import Path

root = Path(SPECPATH).parent
runtime = root / 'vendor' / 'llama'
if not (runtime / 'llama-server.exe').is_file():
    raise RuntimeError('Install the verified native CPU runtime before building')
a = Analysis(
    [str(root / 'run.py')], pathex=[str(root / 'src')],
    binaries=[(str(path), 'vendor/llama') for path in runtime.iterdir() if path.suffix.lower() in ('.exe', '.dll')],
    datas=[
                             (str(root / 'data' / 'dota_chat_lexicon.json'), 'data'),
                             (str(root / 'packaging' / 'MODEL_NOTICE.txt'), '.'),
                             (str(root / 'packaging' / 'WINDOWS_FIRST_USE.txt'), '.'),
                             (str(root / 'build' / 'third-party-licenses'), 'THIRD_PARTY_LICENSES')],
    hiddenimports=[],
    excludes=['torch', 'transformers', 'tensorflow', 'pytest', 'ctranslate2', 'sentencepiece', 'numpy'],
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
