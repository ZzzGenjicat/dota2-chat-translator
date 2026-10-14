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


class _CacheOnlyFallback:
    def _fail(self, *args, **kwargs):
        raise AssertionError('Persistent cache miss: recomputation is disabled')

    exact = split = hints = validate_text = translate_segments = translate_message = _fail


def check_chat_quality(engine, lexicon, cache) -> dict:
    from .offline_translation import OfflineTranslationProvider
    from .sqlite_cache import SQLiteTranslationCache
    from .text_recognition import observation_key

    provider = OfflineTranslationProvider(engine, cache, 'ru', lexicon)
    incoming = OfflineTranslationProvider(engine, cache, 'zh', lexicon)
    quality_cases = [
        ('rus', 'У ТИМБЕРА СОФТ', '伐木机开挂了'),
        ('rus', 'чЕРТ', '该死'),
        ('rus', 'софт', '外挂'),
        ('rus', 'выблядки с читами', '开挂的杂种'),
        ('rus', 'соси', '含屌去'),
        ('rus', 'на аксе', '玩斧王的'),
        ('rus', 'у лины софт', '莉娜开挂了'),
        ('rus', 'у акса нет читов', '斧王没开挂'),
        ('rus', 'он не читер', '他没开挂'),
        ('rus', 'мы все накинем', '我们都会加上（具体所指不明）'),
        ('rus', 'потипай', 'потипай（含义不明）'),
        ('zho', '请跟我一起守住高地', 'дефайте хг со мной'),
        ('zho', '等我把装备出完再打', 'дайте мне дофармить, потом дерёмся'),
        ('zho', '对面只有一个人能买活', 'у них байбек только у одного'),
        ('zho', '我刚才按错技能了', 'я мискликнул скилл'),
    ]
    quality_results = []
    for language, text, expected in quality_cases:
        translator = incoming if language == 'rus' else provider
        actual = translator.translate(text, language)
        if actual != expected:
            raise AssertionError(f'Bundled chat translation regression: {text!r} -> {actual!r}, expected {expected!r}')
        quality_results.append({'source': text, 'translation': actual})
    # A fresh instance cannot satisfy reads from the original memory dictionary.
    reopened = SQLiteTranslationCache(cache.path)
    provider_cache = SQLiteTranslationCache(cache.path)
    forbidden = _CacheOnlyFallback()
    cached_providers = {
        'rus': OfflineTranslationProvider(forbidden, provider_cache, 'zh', forbidden),
        'zho': OfflineTranslationProvider(forbidden, provider_cache, 'ru', forbidden),
    }
    direction_hits = {'ru>zh': 0, 'zh>ru': 0}
    for language, text, expected in quality_cases:
        source, target = ('ru', 'zh') if language == 'rus' else ('zh', 'ru')
        direction = f'{source}>{target}'
        key = direction + ':' + observation_key(text, language)
        try:
            stored = reopened.get(key)
        except OSError as exc:
            raise AssertionError(f'Persistent cache read failed: {key}') from exc
        if (stored is None or stored.key != key or stored.source_text != text
                or stored.language != language or stored.translated_text != expected):
            raise AssertionError(f'Persistent cache entry missing or incorrect: {key}')
        # This provider cache is also fresh: stored-entry assertions cannot
        # populate its memory. Both lexicon and model fallback fail immediately.
        if cached_providers[language].translate(text, language) != expected:
            raise AssertionError(f'Persistent cache translation regression: {key}')
        direction_hits[direction] += 1
    return {'chat_quality': quality_results, 'persistent_cache': {
        'reopened': True, 'recomputation_disabled': True,
        'hits': sum(direction_hits.values()), 'direction_hits': direction_hits,
    }}


def run_checks() -> dict:
    import tkinter as tk
    from .app_paths import resource_root
    from .offline_translation import M2M100Engine, default_model_path
    from .gsi_capture import GsiListener, check_gsi_config, install_gsi_config
    from .steam_discovery import find_dota_dir
    from .translator_app import TranslatorApp
    from .chat_lexicon import ChatLexicon
    from .outgoing_translation import OutgoingTranslator
    from .offline_translation import OfflineTranslationProvider
    from .sqlite_cache import SQLiteTranslationCache

    result = {'platform': platform.system(), 'architecture': platform.machine(),
              'remote_network_blocked': True, 'checks': {}}
    smoke_model = os.environ.get('DOTA2_TRANSLATOR_SMOKE_MODEL')
    engine = M2M100Engine(Path(smoke_model) if smoke_model else default_model_path())
    root = None
    app = None
    listener = None
    original_env = {key: os.environ.get(key) for key in ('DOTA2_TRANSLATOR_DATA_DIR', 'DOTA2_TRANSLATOR_STEAM_DIR', 'DOTA2_TRANSLATOR_DOTA_DIR')}
    old_clipboard = None
    with tempfile.TemporaryDirectory(prefix='dota-offline-smoke-') as scratch:
        scratch = Path(scratch)
        try:
            os.environ['DOTA2_TRANSLATOR_DATA_DIR'] = str(scratch / 'user-data')
            # Keep the isolated app on the same packaged model as the probe.
            from .translator_settings import TranslatorSettings, save_settings
            from .app_paths import user_data_dir
            save_settings(user_data_dir() / 'config' / 'translator_settings.json',
                          TranslatorSettings(offline_model_path=str(engine.model_path)))
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
                compute_type = engine._translator.compute_type
                if (not any('\u4e00' <= ch <= '\u9fff' for ch in ru_zh)
                        or not any('\u0400' <= ch <= '\u04ff' for ch in zh_ru)
                        or compute_type not in ('int8', 'int8_float32') or 'INT8' not in engine.status):
                    raise AssertionError('Real bidirectional M2M100 CPU INT8 inference failed')
                result['checks']['model'] = {'status': engine.status, 'ru_zh': ru_zh, 'zh_ru': zh_ru,
                                              'backend': 'CTranslate2', 'device': 'cpu', 'compute_type': compute_type,
                                              'model_folder': engine.model_path.name,
                                              'seconds': round(time.monotonic() - started, 3)}
                lexicon = ChatLexicon(resource_root() / 'data' / 'dota_chat_lexicon.json')
                provider = OfflineTranslationProvider(engine, SQLiteTranslationCache(scratch / 'cache.sqlite3'), 'ru', lexicon)
                insult = OutgoingTranslator(provider).translate('你是傻逼')
                if 'долбо' not in insult.lower():
                    raise AssertionError('Bundled profanity lexicon lost original strength')
                result['checks']['lexicon'] = True
                # Exercise model inference with compositional glossary handling
                # in each native installation, beyond the exact shipped phrases.
                incoming = OfflineTranslationProvider(engine, provider.cache, 'zh', lexicon)
                complex_chat = incoming.translate('ты долбоёб, а не саппорт, где варды?', 'rus')
                if ('傻逼' not in complex_chat or '[[DOTA_' in complex_chat
                        or not any('\u4e00' <= ch <= '\u9fff' for ch in complex_chat)):
                    raise AssertionError('Native compositional/profanity inference failed')
                result['checks']['complex_model'] = complex_chat
                result['checks'].update(check_chat_quality(engine, lexicon, provider.cache))
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
                # Retire the standalone probe before warming the app's own
                # shared engine; native CI must never keep two models loaded.
                engine.close()
                root = tk.Tk()
                try:
                    old_clipboard = root.clipboard_get()
                except tk.TclError:
                    pass
                app = TranslatorApp(root)
                if app.controller.engine.model_path != engine.model_path:
                    raise AssertionError('App changed the explicit offline model fixture path')
                app.controller.engine.warmup()
                result['checks']['app_model'] = {'status': app.controller.engine.status,
                                                 'shared_directions': app.controller.outgoing_provider.engine is app.controller.translation_queue.provider.engine}
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
