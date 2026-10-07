"""Exercise the shipped app, model, clipboard and loopback GSI without remote access."""
from __future__ import annotations

import argparse
from contextlib import contextmanager
import http.client
import ipaddress
import json
import os
from pathlib import Path
import platform
import socket
import tempfile
import threading
import time
import traceback


def _loopback(host: str) -> bool:
    if host.lower() == 'localhost':
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


@contextmanager
def offline_network_guard():
    connect = socket.socket.connect
    connect_ex = socket.socket.connect_ex
    create_connection = socket.create_connection
    def check(address):
        if isinstance(address, tuple) and not _loopback(str(address[0])):
            raise RuntimeError('release self-test blocked remote network access')
    def guarded_connect(sock, address):
        check(address)
        return connect(sock, address)
    def guarded_connect_ex(sock, address):
        check(address)
        return connect_ex(sock, address)
    def guarded_create(address, *args, **kwargs):
        check(address)  # Reject remote hostnames before DNS resolution.
        return create_connection(address, *args, **kwargs)
    socket.socket.connect = guarded_connect
    socket.socket.connect_ex = guarded_connect_ex
    socket.create_connection = guarded_create
    try:
        yield
    finally:
        socket.socket.connect = connect
        socket.socket.connect_ex = connect_ex
        socket.create_connection = create_connection


def run_checks() -> dict:
    import tkinter as tk
    from .app_paths import resource_root
    from .offline_translation import M2M100Engine
    from .gsi_capture import GsiListener, check_gsi_config, install_gsi_config
    from .steam_discovery import find_dota_dir
    from .translator_app import TranslatorApp
    from .chat_lexicon import ChatLexicon
    from .outgoing_translation import OutgoingTranslator
    from .offline_translation import OfflineTranslationProvider
    from .sqlite_cache import SQLiteTranslationCache

    result = {'platform': platform.system(), 'architecture': platform.machine(),
              'remote_network_blocked': True, 'checks': {}}
    engine = M2M100Engine(resource_root() / 'models' / 'm2m100-418m-ct2-int8')
    root = None
    app = None
    listener = None
    original_env = {key: os.environ.get(key) for key in ('DOTA2_TRANSLATOR_DATA_DIR', 'DOTA2_TRANSLATOR_STEAM_DIR', 'DOTA2_TRANSLATOR_DOTA_DIR')}
    old_clipboard = None
    with tempfile.TemporaryDirectory(prefix='dota-offline-smoke-') as scratch:
        scratch = Path(scratch)
        try:
            os.environ['DOTA2_TRANSLATOR_DATA_DIR'] = str(scratch / 'user-data')
            steam = scratch / '我的 Steam Library'
            dota = steam / 'steamapps' / 'common' / 'dota 2 beta' / 'game' / 'dota'
            (dota / 'cfg').mkdir(parents=True)
            (dota / 'gameinfo.gi').write_text('fixture', encoding='utf-8')
            os.environ['DOTA2_TRANSLATOR_STEAM_DIR'] = str(steam)
            os.environ.pop('DOTA2_TRANSLATOR_DOTA_DIR', None)
            with offline_network_guard():
                started = time.monotonic()
                engine.warmup()
                ru_zh = engine.translate_segments(['пожалуйста, идём вместе по верхней линии'], 'ru', 'zh')[0]
                zh_ru = engine.translate_segments(['请跟我一起守住高地'], 'zh', 'ru')[0]
                if not ru_zh or not zh_ru or 'INT8' not in engine.status:
                    raise AssertionError('Real bidirectional INT8 inference failed')
                result['checks']['model'] = {'status': engine.status, 'ru_zh': ru_zh, 'zh_ru': zh_ru,
                                              'seconds': round(time.monotonic() - started, 3)}
                lexicon = ChatLexicon(resource_root() / 'data' / 'dota_chat_lexicon.json')
                provider = OfflineTranslationProvider(engine, SQLiteTranslationCache(scratch / 'cache.sqlite3'), 'ru', lexicon)
                insult = OutgoingTranslator(provider).translate('你是傻逼')
                if 'долбо' not in insult.lower():
                    raise AssertionError('Bundled profanity lexicon lost original strength')
                result['checks']['lexicon'] = True
                if find_dota_dir() != dota.resolve():
                    raise AssertionError('Steam discovery failed in Unicode library')
                install_gsi_config(dota)
                if check_gsi_config(dota).state != 'installed':
                    raise AssertionError('GSI configuration did not survive installation')
                received = []
                ready = threading.Event()
                def on_messages(messages):
                    received.extend(messages)
                    ready.set()
                listener = GsiListener(on_messages, port=0)
                listener.start()
                body = json.dumps({'map': {'matchid': 'smoke'}, 'events': [
                    {'event_type': 'chat_message', 'message': 'привет', 'player_id': 1, 'game_time': 1, 'channel_type': 'all'}]})
                connection = http.client.HTTPConnection('127.0.0.1', listener.server.server_address[1], timeout=5)
                try:
                    connection.request('POST', '/', body, {'Content-Type': 'application/json'})
                    response = connection.getresponse()
                    response.read()
                    if response.status != 200 or not ready.wait(3) or received[0].text != 'привет':
                        raise AssertionError('Loopback GSI receive failed')
                finally:
                    connection.close()
                result['checks']['gsi'] = True
                root = tk.Tk()
                try:
                    old_clipboard = root.clipboard_get()
                except tk.TclError:
                    pass
                app = TranslatorApp(root)
                root.geometry('1100x800+30+30')
                root.update()
                app.outgoing_result = zh_ru
                app._copy_outgoing()
                root.update()
                if root.clipboard_get() != zh_ru:
                    raise AssertionError('Russian clipboard copy failed')
                for control in (app.start_button, app.settings_button, app.copy_outgoing_button):
                    if (control.winfo_height() < control.winfo_reqheight()
                            or control.winfo_width() < control.winfo_reqwidth()):
                        raise AssertionError(
                            f'A primary control is clipped: {control["text"]!r}, '
                            f'height={control.winfo_height()}, required={control.winfo_reqheight()}, '
                            f'width={control.winfo_width()}, required={control.winfo_reqwidth()}, '
                            f'window={root.geometry()}, maximum={root.maxsize()}, '
                            f'screen={root.winfo_screenwidth()}x{root.winfo_screenheight()}, '
                            f'scaling={root.tk.call("tk", "scaling")}, font={app.ui_font}')
                result['checks']['ui'] = {'font': app.ui_font, 'clipboard': True, 'tk': root.tk.call('info', 'patchlevel')}
        finally:
            if listener is not None:
                listener.stop()
            if app is not None:
                app.controller.close()
            if root is not None:
                root.clipboard_clear()
                if old_clipboard is not None:
                    root.clipboard_append(old_clipboard)
                root.destroy()
            engine.close()
            for key, value in original_env.items():
                if value is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = value
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--self-test', action='store_true')
    parser.add_argument('--report', type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        report = run_checks()
        report['passed'] = True
    except Exception:
        report = {'passed': False, 'platform': platform.system(), 'architecture': platform.machine(),
                  'error': traceback.format_exc()}
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    return 0 if report['passed'] else 1
