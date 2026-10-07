from __future__ import annotations

import queue
from dataclasses import dataclass, replace
from pathlib import Path

from .dota_terminology import local_term_translation, localize_translation
from .chat_lexicon import ChatLexicon
from .offline_translation import M2M100Engine, OfflineTranslationProvider, cache_path, resolve_model_path, make_translation_cache
from .app_paths import portable_resource_path, resource_root
from .gsi_capture import GsiListener
from .text_recognition import is_chat_wheel_message, normalize_text, observation_key, similar_observations
from .translation import TranslationCache, TranslationQueue
from .translator_models import TextObservation, TranslationEntry
from .translator_settings import TranslatorSettings

APP_ROOT = resource_root()
# Previous cache contains many OCR fragments and literal mistranslations.
TRANSLATION_CACHE_PATH = APP_ROOT / "data" / "translation_cache_v2.json"


def _cache_path_for_settings(settings: TranslatorSettings) -> Path:
    return cache_path(resolve_model_path(settings.offline_model_path), ChatLexicon())

@dataclass(frozen=True)
class DisplayRow:
    key: str
    source_text: str
    language: str
    translated_text: str
    status: str
    observed_at: float


class TranslatorController:
    def __init__(
        self,
        settings: TranslatorSettings,
        translation_queue: TranslationQueue | None = None,
        translation_cache: TranslationCache | None = None,
        chatgpt_auth: object | None = None,
    ) -> None:
        self.settings = replace(settings, translation_backend='offline',
                                offline_model_path=portable_resource_path(resolve_model_path(settings.offline_model_path)))
        lexicon = ChatLexicon()
        self.engine = M2M100Engine(resolve_model_path(settings.offline_model_path), settings.offline_cpu_threads)
        self.translation_cache = translation_cache or make_translation_cache(self.engine.model_path, lexicon)
        incoming = OfflineTranslationProvider(self.engine, self.translation_cache, 'zh', lexicon)
        self.outgoing_provider = OfflineTranslationProvider(self.engine, self.translation_cache, 'ru', lexicon)
        self.translation_queue = translation_queue or TranslationQueue(
            incoming,
            self.translation_cache,
            settings.network_profile,
            settings.network_paused,
        )
        self.rows: list[DisplayRow] = []
        self._rows_by_key: dict[str, DisplayRow] = {}
        self._seen_message_texts: set[str] = set()
        self._observation_events: queue.Queue[TextObservation] = queue.Queue()
        self._gsi_listener: GsiListener | None = None
        self.status = "未开始"
        self.capture_status = "未开始"

    def start(self) -> None:
        if self._gsi_listener is not None:
            return
        listener = GsiListener(self._receive_gsi_messages)
        try:
            listener.start()
        except OSError as exc:
            self.capture_status = f"游戏直读无法启动：{exc}"
            return
        self._gsi_listener = listener
        self.status = "运行中"
        self.capture_status = "游戏直读已启动，等待 Dota 2 推送聊天"

    def stop(self) -> None:
        if self._gsi_listener is not None:
            self._gsi_listener.stop()
            self._gsi_listener = None
        self.status = "已暂停"
        self.capture_status = "已暂停抓取"

    def _receive_gsi_messages(self, messages: list[TextObservation]) -> None:
        for observation in messages:
            self._observation_events.put(observation)
        self.capture_status = f"游戏直读：收到 {len(messages)} 条俄语聊天"

    def close(self) -> None:
        self.stop()
        self.translation_queue.stop()
        self.engine.close()

    def clear_output(self) -> None:
        self._discard_pending_observations()
        self.rows.clear()
        self._rows_by_key.clear()
        self._seen_message_texts.clear()

    def _discard_pending_observations(self) -> None:
        while True:
            try:
                self._observation_events.get_nowait()
            except queue.Empty:
                break

    def clear_cache(self) -> None:
        self.translation_queue.clear_memory()
        self.translation_cache.clear()
        for index, row in enumerate(self.rows):
            if row.status != "queued":
                continue
            observation = TextObservation(row.source_text, row.language, None, row.observed_at)
            accepted = self.translation_queue.submit(observation)
            updated = replace(row, status="queued" if accepted else "paused" if self.translation_queue.paused else "untranslated")
            self.rows[index] = updated
            self._rows_by_key[row.key] = updated

    def process_observation(self, observation: TextObservation) -> None:
        text = normalize_text(observation.text)
        if not text or is_chat_wheel_message(text):
            return
        key = observation_key(text, observation.language)
        existing = self._rows_by_key.get(key)
        if existing is not None:
            retry_due = (existing.status == "untranslated" or existing.status.startswith("failed")) and observation.observed_at - existing.observed_at >= 30
            if (existing.status == "paused" or retry_due) and not self.translation_queue.paused:
                accepted = self.translation_queue.submit(observation)
                if accepted:
                    updated = replace(existing, status="queued", observed_at=observation.observed_at)
                    self._rows_by_key[key] = updated
                    for index, row in enumerate(self.rows):
                        if row.key == key:
                            self.rows[index] = updated
                            break
            return
        if text.casefold() in self._seen_message_texts:
            return
        if any(
            abs(observation.observed_at - row.observed_at) < 90
            and similar_observations(observation, TextObservation(row.source_text, row.language, None, row.observed_at))
            for row in self.rows[-60:]
        ):
            return
        local = local_term_translation(text)
        try:
            cached = self.translation_cache.get(key) if local is None else None
        except OSError:
            cached = None
        if local is not None:
            row = DisplayRow(key, text, observation.language, local, "translated", observation.observed_at)
        elif cached is not None:
            row = replace(_row_from_entry(cached), observed_at=observation.observed_at)
        else:
            accepted = self.translation_queue.submit(observation)
            status = "paused" if self.translation_queue.paused else ("queued" if accepted else "untranslated")
            row = DisplayRow(key, text, observation.language, "", status, observation.observed_at)
        self._rows_by_key[key] = row
        self._seen_message_texts.add(text.casefold())
        self.rows.append(row)
        self._trim_rows()

    def poll(self) -> None:
        while True:
            try:
                observation = self._observation_events.get_nowait()
            except queue.Empty:
                break
            if observation.language != "rus":
                continue
            self.process_observation(observation)
        while True:
            entry = self.translation_queue.poll_result()
            if entry is None:
                break
            existing = self._rows_by_key.get(entry.key)
            if existing is None:
                continue
            updated = _row_from_entry(entry)
            self._rows_by_key[entry.key] = updated
            for index, row in enumerate(self.rows):
                if row.key == entry.key:
                    self.rows[index] = updated
                    break

    def set_profile(self, profile: str) -> None:
        self.translation_queue.set_profile(profile)
        self.settings = replace(self.settings, network_profile=profile)

    def set_network_paused(self, paused: bool) -> None:
        self.translation_queue.set_paused(paused)
        self.settings = replace(self.settings, network_paused=paused)
        if not paused:
            for index, row in enumerate(self.rows):
                if row.status != "paused":
                    continue
                observation = TextObservation(row.source_text, row.language, None, row.observed_at)
                if self.translation_queue.submit(observation):
                    updated = replace(row, status="queued")
                    self.rows[index] = updated
                    self._rows_by_key[row.key] = updated

    def set_offline_provider(self, model_path: str, cpu_threads: int = 2) -> None:
        new_settings = replace(self.settings, translation_backend='offline',
                               offline_model_path=portable_resource_path(resolve_model_path(model_path.strip())),
                               offline_cpu_threads=max(1, min(4, int(cpu_threads))), network_paused=False)
        lexicon = ChatLexicon()
        engine = M2M100Engine(resolve_model_path(new_settings.offline_model_path), new_settings.offline_cpu_threads)
        old_engine = self.engine
        self.translation_queue.clear_memory()
        old_engine.close()
        self.translation_cache = make_translation_cache(engine.model_path, lexicon)
        self.translation_queue.cache = self.translation_cache
        self.translation_queue.set_paused(False)
        self.translation_queue.set_provider(OfflineTranslationProvider(engine, self.translation_cache, 'zh', lexicon))
        self.outgoing_provider = OfflineTranslationProvider(engine, self.translation_cache, 'ru', lexicon)
        self.engine = engine
        self.settings = new_settings
        # A retired active engine releases its weights before the global
        # inference lock lets the next engine load.
        for index, row in enumerate(self.rows):
            if row.status in ('queued', 'paused', 'untranslated', 'unchanged') or row.status.startswith('failed'):
                accepted = self.translation_queue.submit(TextObservation(row.source_text, row.language, None, row.observed_at))
                updated = replace(row, translated_text='', status='queued' if accepted else 'untranslated')
                self.rows[index] = updated
                self._rows_by_key[row.key] = updated

    def _trim_rows(self) -> None:
        maximum = max(20, int(self.settings.max_rows))
        if len(self.rows) <= maximum:
            return
        del self.rows[:-maximum]
        self._rows_by_key = {row.key: row for row in self.rows}

    @staticmethod
    def _make_provider(settings: TranslatorSettings, chatgpt_auth: object | None = None):
        lexicon = ChatLexicon()
        engine = M2M100Engine(resolve_model_path(settings.offline_model_path), settings.offline_cpu_threads)
        cache = make_translation_cache(engine.model_path, lexicon)
        return OfflineTranslationProvider(engine, cache, 'zh', lexicon)


def _row_from_entry(entry: TranslationEntry) -> DisplayRow:
    translated = localize_translation(entry.source_text, entry.translated_text)
    if entry.status == "translated" and normalize_text(translated).casefold() == normalize_text(entry.source_text).casefold():
        return DisplayRow(entry.key, entry.source_text, entry.language, "", "unchanged", entry.observed_at)
    return DisplayRow(
        entry.key, entry.source_text, entry.language,
        translated, entry.status, entry.observed_at,
    )




