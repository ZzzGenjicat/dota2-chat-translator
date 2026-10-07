"""Build and exercise a self-contained, per-user Windows offline installer."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import platform
import shutil
import subprocess
import sys
import tempfile
import time
import tomllib
import winreg

from install_offline_model import install
from package_licenses import collect_licenses

ROOT = Path(__file__).resolve().parents[1]
APP_ID = '{9D69E4A5-B037-475A-B271-D20229C6DFA8}'
UNINSTALL_KEYS = r'Software\Microsoft\Windows\CurrentVersion\Uninstall' + '\\'


def registry_values(app_id: str) -> dict | None:
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, UNINSTALL_KEYS + app_id + '_is1',
                            0, winreg.KEY_READ | winreg.KEY_WOW64_64KEY) as key:
            values = {}
            for index in range(winreg.QueryInfoKey(key)[1]):
                name, value, kind = winreg.EnumValue(key, index)
                values[name] = (value, kind)
            return values
    except FileNotFoundError:
        return None


def clean_environment() -> dict:
    env = dict(os.environ)
    env.pop('PYTHONPATH', None)
    env.pop('PYTHONHOME', None)
    for key in list(env):
        if key.startswith('DOTA2_TRANSLATOR_'):
            env.pop(key)
    windows = Path(os.environ['SystemRoot'])
    env['PATH'] = os.pathsep.join(str(windows / suffix) for suffix in ('System32', ''))
    return env


def frozen_check(app: Path, report: Path, env: dict) -> dict:
    process = subprocess.run([str(app), '--self-test', '--report', str(report)], cwd=app.parent,
                             env=env, timeout=180)
    result = json.loads(report.read_text(encoding='utf-8'))
    if process.returncode or not result.get('passed'):
        raise RuntimeError(f'Frozen offline check failed: {result}')
    return result


def uninstall_test_app(uninstaller: Path, env: dict) -> None:
    if uninstaller.exists():
        subprocess.run([str(uninstaller), '/VERYSILENT', '/SUPPRESSMSGBOXES', '/NORESTART'],
                       env=env, check=True, timeout=180)
        deadline = time.monotonic() + 30
        while uninstaller.exists() and time.monotonic() < deadline:
            time.sleep(0.2)
        if uninstaller.exists():
            raise RuntimeError('Isolated test uninstaller did not finish')


def exercise_installer(installer: Path, report: Path, env: dict) -> None:
    test_id = APP_ID + '.InstallerTest'
    if registry_values(test_id) is not None:
        raise RuntimeError('An existing isolated test installation must be removed first')
    production_registry = registry_values(APP_ID)
    checks = {}
    with tempfile.TemporaryDirectory(prefix='安装验证 with spaces ') as scratch_name:
        scratch = Path(scratch_name)
        destination = scratch / '程序'
        shortcuts = scratch / '快捷方式'
        preserved = scratch / '用户数据' / 'config.json'
        preserved.parent.mkdir()
        preserved.write_text('preserve existing user data', encoding='utf-8')
        app = destination / 'Dota2ChatTranslator.exe'
        uninstaller = destination / 'unins000.exe'
        shortcut_files = [shortcuts / name / f'{label}.lnk' for name, label in (
            ('start-menu', 'Dota 2 本地聊天翻译'), ('desktop', 'Dota 2 本地聊天翻译'),
            ('instructions', '首次使用说明'))]
        args = [str(installer), '/VERYSILENT', '/SUPPRESSMSGBOXES', '/NORESTART', '/SP-',
                '/INSTALLERTEST=1', f'/TESTSHORTCUTDIR={shortcuts}', f'/DIR={destination}']
        try:
            subprocess.run(args + [f'/LOG={scratch / "install.log"}'], env=env, check=True, timeout=300)
            if not app.is_file() or registry_values(test_id) is None:
                raise RuntimeError('Installer did not register the isolated app')
            if not all(path.is_file() for path in shortcut_files):
                raise RuntimeError('Installer did not create all shortcuts')
            if not (destination / '_internal' / 'WINDOWS_FIRST_USE.txt').is_file():
                raise RuntimeError('Installer omitted the first-use instructions')
            checks['installed'] = checks['shortcuts_created'] = checks['instructions_included'] = True
            frozen_check(app, scratch / 'installed-smoke.json', env)
            checks['installed_offline_inference'] = True
            subprocess.run(args + [f'/LOG={scratch / "reinstall.log"}'], env=env, check=True, timeout=300)
            frozen_check(app, scratch / 'reinstalled-smoke.json', env)
            checks['reinstalled_offline_inference'] = True
        except Exception:
            diagnostics = ROOT / 'build' / 'installer-diagnostics'
            diagnostics.mkdir(parents=True, exist_ok=True)
            for path in list(scratch.glob('*.log')) + list(scratch.glob('*-smoke.json')):
                shutil.copy2(path, diagnostics / path.name)
            raise
        finally:
            uninstall_test_app(uninstaller, env)
        if app.exists() or (destination / '_internal' / 'models').exists():
            raise RuntimeError('Uninstall left the app or model behind')
        if any(path.exists() for path in shortcut_files) or registry_values(test_id) is not None:
            raise RuntimeError('Uninstall left shortcuts or its registry entry behind')
        if registry_values(APP_ID) != production_registry:
            raise RuntimeError('Isolated test changed the real application registry entry')
        if preserved.read_text(encoding='utf-8') != 'preserve existing user data':
            raise RuntimeError('Uninstall modified data outside its installation')
        checks.update(uninstalled=True, shortcuts_removed=True, test_registry_removed=True,
                      production_registry_preserved=True, external_user_data_preserved=True)
    report.write_text(json.dumps({'passed': True, 'platform': 'Windows', 'architecture': 'AMD64',
                                  'checks': checks}, indent=2), encoding='utf-8')


def build() -> None:
    if sys.platform != 'win32' or platform.machine().lower() not in ('amd64', 'x86_64'):
        raise RuntimeError('Build with native 64-bit CPython on Windows')
    import ctranslate2
    if 'int8' not in ctranslate2.get_supported_compute_types('cpu'):
        raise RuntimeError('CTranslate2 does not support CPU INT8')
    install(ROOT / 'models' / 'm2m100-418m-ct2-int8', verify_only=True)
    collect_licenses(ROOT)
    subprocess.run([sys.executable, '-m', 'PyInstaller', '--noconfirm',
                    '--distpath', str(ROOT / 'dist'), '--workpath', str(ROOT / 'build' / 'pyinstaller'),
                    str(ROOT / 'packaging' / 'windows.spec')], cwd=ROOT, check=True)
    app_dir = ROOT / 'dist' / 'Dota2ChatTranslator'
    internal = app_dir / '_internal'
    for name in ('python312.dll', 'msvcp140.dll', 'vcruntime140.dll', 'vcruntime140_1.dll'):
        if not any(path.name.lower() == name for path in internal.rglob('*.dll')):
            raise RuntimeError(f'App-local runtime dependency is missing: {name}')
    if any(path.name.lower().startswith(('cudnn', 'cublas', 'cudart')) for path in internal.rglob('*.dll')):
        raise RuntimeError('A CPU-only installer unexpectedly contains GPU libraries')
    release = ROOT / 'release'
    release.mkdir(exist_ok=True)
    version = tomllib.loads((ROOT / 'pyproject.toml').read_text(encoding='utf-8'))['project']['version']
    stem = f'Dota2ChatTranslator-{version}-windows-x64'
    env = clean_environment()
    with tempfile.TemporaryDirectory(prefix='移动程序 with spaces ') as moved:
        moved_app = Path(moved) / app_dir.name
        shutil.copytree(app_dir, moved_app)
        frozen_check(moved_app / 'Dota2ChatTranslator.exe', release / f'{stem}-smoke.json', env)
    compiler = ROOT / '.tools' / 'inno-6.7.3' / 'ISCC.exe'
    subprocess.run([str(compiler), f'/DAppVersion={version}', f'/DAppSource={app_dir}',
                    f'/DReleaseDirectory={release}', str(ROOT / 'packaging' / 'windows.iss')], check=True)
    installer = release / f'{stem}-setup.exe'
    exercise_installer(installer, release / f'{stem}-installer.json', env)
    with installer.open('rb') as stream:
        digest = hashlib.file_digest(stream, 'sha256').hexdigest()
    (release / f'{stem}.sha256').write_text(f'{digest}  {installer.name}\n', encoding='utf-8')
    print(f'Verified native installer: {installer.name}', flush=True)


if __name__ == '__main__':
    build()
