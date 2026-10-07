from __future__ import annotations

import json
import html
import queue
import ssl
import threading
import time
from collections import deque
from pathlib import Path
from typing import Any, Callable, Protocol
from urllib import request
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode

from .text_recognition import normalize_text, observation_key
from .translator_models import PROFILE_LIMITS, TextObservation, TranslationEntry


class TranslationProvider(Protocol):
    def translate(self, text: str, source_language: str) -> str:
        ...


class TranslationCache:
    def __init__(self, path: Path) -> None:
        self.path = path
        self._lock = threading.Lock()

    def get(self, key: str) -> TranslationEntry | None:
        with self._lock:
            data = self._read()
        raw = data.get(key)
        if not isinstance(raw, dict) or raw.get("status") != "translated" or not raw.get("translated_text"):
            return None
        try:
            return TranslationEntry(
                key=str(raw["key"]),
                source_text=str(raw["source_text"]),
                language=str(raw["language"]),
                translated_text=str(raw.get("translated_text") or ""),
                status=str(raw.get("status") or "failed"),
                observed_at=float(raw.get("observed_at") or 0.0),
            )
        except (KeyError, TypeError, ValueError):
            return None

    def put(self, entry: TranslationEntry) -> None:
        if entry.status != "translated" or not entry.translated_text:
            return
        with self._lock:
            data = self._read()
            data[entry.key] = {
                "key": entry.key,
                "source_text": entry.source_text,
                "language": entry.language,
                "translated_text": entry.translated_text,
                "status": entry.status,
                "observed_at": entry.observed_at,
            }
            self.path.parent.mkdir(parents=True, exist_ok=True)
            temporary = self.path.with_suffix(self.path.suffix + ".tmp")
            temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
            temporary.replace(self.path)

    def clear(self) -> None:
        with self._lock:
            if self.path.exists():
                self.path.unlink()

    def _read(self) -> dict[str, Any]:
        if not self.path.exists():
            return {}
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}
        return data if isinstance(data, dict) else {}


class LocalPassthroughProvider:
    def translate(self, text: str, source_language: str) -> str:
        return text


class UnconfiguredProvider:
    def translate(self, text: str, source_language: str) -> str:
        raise RuntimeError("未配置翻译服务地址")


class GoogleTranslateProvider:
    """Small text-only adapter for the public Google translation endpoint."""

    def __init__(
        self,
        endpoint: str = "https://translate.googleapis.com/translate_a/single",
        opener: Callable[..., Any] | None = None,
        timeout_seconds: float = 8.0,
        target_language: str = "zh-CN",
    ) -> None:
        self.endpoint = endpoint
        self.opener = opener or request.urlopen
        self.timeout_seconds = timeout_seconds
        self.target_language = target_language
        self.ssl_context = _make_ssl_context()

    def translate(self, text: str, source_language: str) -> str:
        source = {
            "eng": "en",
            "rus": "ru",
            "spa": "es",
            "deu": "de",
            "fra": "fr",
            "zho": "zh-CN",
        }.get(source_language, "auto")
        query = urlencode(
            {
                "client": "gtx",
                "sl": source,
                "tl": self.target_language,
                "dt": "t",
                "q": text,
            }
        )
        response = self.opener(
            request.Request(f"{self.endpoint}?{query}", headers={"User-Agent": "Mozilla/5.0"}),
            timeout=self.timeout_seconds,
            context=self.ssl_context,
        )
        with response:
            data = json.loads(response.read().decode("utf-8"))
        try:
            segments = data[0]
            translated = "".join(segment[0] for segment in segments if segment and isinstance(segment[0], str))
        except (IndexError, KeyError, TypeError):
            translated = ""
        if not translated.strip():
            raise RuntimeError("翻译服务没有返回译文")
        return translated.strip()


class MyMemoryTranslateProvider:
    """Text-only fallback with a small public daily quota."""

    def __init__(
        self,
        endpoint: str = "https://api.mymemory.translated.net/get",
        opener: Callable[..., Any] | None = None,
        timeout_seconds: float = 8.0,
        target_language: str = "zh-CN",
    ) -> None:
        self.endpoint = endpoint
        self.opener = opener or request.urlopen
        self.timeout_seconds = timeout_seconds
        self.target_language = target_language
        self.ssl_context = _make_ssl_context()

    def translate(self, text: str, source_language: str) -> str:
        if len(text.encode("utf-8")) > 500:
            raise ValueError("单条文字超过备用翻译接口的 500 字节限制")
        source = {"eng": "en", "rus": "ru", "spa": "es", "deu": "de", "fra": "fr", "zho": "zh-CN"}.get(source_language, "en")
        query = urlencode({"q": text, "langpair": f"{source}|{self.target_language}"})
        response = self.opener(
            request.Request(f"{self.endpoint}?{query}", headers={"User-Agent": "Mozilla/5.0"}),
            timeout=self.timeout_seconds,
            context=self.ssl_context,
        )
        with response:
            data = json.loads(response.read().decode("utf-8"))
        if data.get("quotaFinished") or data.get("responseStatus") != 200:
            raise RuntimeError(f"备用翻译接口限额：{data.get('responseDetails') or data.get('responseStatus')}")
        translated = data.get("responseData", {}).get("translatedText")
        if not isinstance(translated, str) or not translated.strip():
            raise RuntimeError("备用翻译接口没有返回译文")
        return html.unescape(translated).strip()


class FallbackTranslationProvider:
    def __init__(
        self,
        primary: TranslationProvider,
        fallback: TranslationProvider,
        cooldown_seconds: float = 3600.0,
        clock: Callable[[], float] = time.monotonic,
        target_language: str = "zh-CN",
    ) -> None:
        self.primary = primary
        self.fallback = fallback
        self.cooldown_seconds = cooldown_seconds
        self.clock = clock
        self.target_language = target_language
        self._fallback_until = 0.0
        self._blocked_until = 0.0

    def translate(self, text: str, source_language: str) -> str:
        if self.clock() < self._blocked_until:
            raise RuntimeError("免费翻译接口暂不可用，请稍后重试")
        if self.clock() < self._fallback_until:
            return self._translate_with_fallback(text, source_language)
        try:
            translated = self.primary.translate(text, source_language)
            _require_target_language(translated, source_language, self.target_language)
            return translated
        except HTTPError as exc:
            self._fallback_until = self.clock() + (self.cooldown_seconds if exc.code == 429 or exc.code >= 500 else 60.0)
            return self._translate_with_fallback(text, source_language)
        except (URLError, TimeoutError, RuntimeError):
            self._fallback_until = self.clock() + 60.0
            return self._translate_with_fallback(text, source_language)
        except ValueError:
            return self._translate_with_fallback(text, source_language)

    def _translate_with_fallback(self, text: str, source_language: str) -> str:
        try:
            translated = self.fallback.translate(text, source_language)
            _require_target_language(translated, source_language, self.target_language)
            return translated
        except ValueError:
            raise
        except Exception as exc:
            self._blocked_until = self.clock() + 60.0
            raise RuntimeError("免费翻译接口暂不可用，请稍后重试") from exc


def _require_target_language(translated: str, source_language: str, target_language: str) -> None:
    if source_language == "rus" and not any("\u4e00" <= char <= "\u9fff" for char in translated):
        raise ValueError("翻译服务未返回中文译文")
    if source_language == "zho" and target_language == "ru" and (
        not any("\u0400" <= char <= "\u052f" for char in translated)
        or any("\u4e00" <= char <= "\u9fff" for char in translated)
    ):
        raise ValueError("翻译服务未返回俄语译文")


class HttpTranslationProvider:
    def __init__(
        self,
        url: str,
        api_key: str = "",
        opener: Callable[..., Any] | None = None,
        timeout_seconds: float = 8.0,
    ) -> None:
        self.url = url.strip()
        self.api_key = api_key.strip()
        self.opener = opener or request.urlopen
        self.timeout_seconds = timeout_seconds
        self.ssl_context = _make_ssl_context()

    def translate(self, text: str, source_language: str) -> str:
        if not self.url:
            raise RuntimeError("未配置翻译服务地址")
        payload = json.dumps(
            {
                "text": text,
                "source_language": source_language,
                "target_language": "zh-CN",
            },
            ensure_ascii=False,
        ).encode("utf-8")
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        response = self.opener(
            request.Request(self.url, data=payload, headers=headers, method="POST"),
            timeout=self.timeout_seconds,
            context=self.ssl_context,
        )
        with response:
            data = json.loads(response.read().decode("utf-8"))
        if not isinstance(data, dict):
            raise RuntimeError("翻译服务返回格式不是 JSON 对象")
        translated = data.get("translated_text") or data.get("translation") or data.get("text")
        if not isinstance(translated, str) or not translated.strip():
            raise RuntimeError("翻译服务没有返回 translated_text")
        return translated.strip()


class ChatCompletionTranslationProvider:
    """Translate one chat body with a user-configured chat-completions model."""

    SYSTEM_PROMPT = (
        "你是 Dota 2 对局聊天翻译员。把俄语玩家消息译成简体中文，只输出译文。"
        "按游戏语境理解英雄、位置、经济、口语和粗话；保留数字与玩家原意。"
        "不要补充原文没有的信息；看不懂的片段保留原文，不要编造。"
    )

    def __init__(
        self, url: str, model: str, api_key: str,
        opener: Callable[..., Any] | None = None, timeout_seconds: float = 8.0,
    ) -> None:
        self.url = url.strip()
        self.model = model.strip()
        self.api_key = api_key.strip()
        self.opener = opener or request.urlopen
        self.timeout_seconds = timeout_seconds
        self.ssl_context = _make_ssl_context()

    def translate(self, text: str, source_language: str) -> str:
        if not self.url or not self.model or not self.api_key:
            raise RuntimeError("模型翻译需要服务地址、模型 ID 和密钥")
        payload = json.dumps({
            "model": self.model,
            "messages": [
                {"role": "system", "content": self.SYSTEM_PROMPT},
                {"role": "user", "content": text},
            ],
        }, ensure_ascii=False).encode("utf-8")
        req = request.Request(
            self.url, data=payload,
            headers={"Content-Type": "application/json", "Authorization": f"Bearer {self.api_key}"},
            method="POST",
        )
        with self.opener(req, timeout=self.timeout_seconds, context=self.ssl_context) as response:
            data = json.loads(response.read().decode("utf-8"))
        try:
            translated = data["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            raise ValueError("模型服务未返回译文") from exc
        if not isinstance(translated, str) or not translated.strip():
            raise ValueError("模型服务未返回译文")
        return translated.strip()


class TranslationQueue:
    def __init__(
        self,
        provider: TranslationProvider,
        cache: TranslationCache,
        profile: str = "Balanced",
        paused: bool = False,
        start_worker: bool = True,
        clock: Callable[[], float] = time.monotonic,
        sleeper: Callable[[float], None] = time.sleep,
    ) -> None:
        self.provider = provider
        self.cache = cache
        self._clock = clock
        self._sleeper = sleeper
        self._profile_name = "Balanced"
        self.set_profile(profile)
        self._paused = paused
        self._jobs: queue.Queue[tuple[int, TextObservation] | None] = queue.Queue()
        self._results: queue.Queue[TranslationEntry] = queue.Queue()
        self._seen: set[str] = set()
        self._pending: set[str] = set()
        self._generation = 0
        self._lock = threading.Lock()
        self._stop_event = threading.Event()
        self._last_request_at: float | None = None
        self._sent_chars: deque[tuple[float, int]] = deque()
        self._worker: threading.Thread | None = None
        if start_worker:
            self._worker = threading.Thread(target=self._run, name="translation-worker", daemon=True)
            self._worker.start()

    @property
    def request_delay_seconds(self) -> float:
        if getattr(self.provider, 'is_offline', False):
            return 0.0
        return PROFILE_LIMITS[self._profile_name].min_interval_seconds

    @property
    def profile(self) -> str:
        return self._profile_name

    @property
    def paused(self) -> bool:
        return self._paused

    def set_provider(self, provider: TranslationProvider) -> None:
        self.provider = provider

    def submit(self, observation: TextObservation) -> bool:
        text = normalize_text(observation.text)
        if not text or self._paused:
            return False
        key = observation_key(text, observation.language)
        try:
            cached = self.cache.get(key)
        except OSError:
            cached = None
        if cached is not None:
            with self._lock:
                if key in self._seen:
                    return False
                self._seen.add(key)
            self._results.put(cached)
            return True
        with self._lock:
            if key in self._seen or key in self._pending:
                return False
            self._seen.add(key)
            self._pending.add(key)
            generation = self._generation
        self._jobs.put((generation, TextObservation(text, observation.language, observation.confidence, observation.observed_at)))
        return True

    def poll_result(self) -> TranslationEntry | None:
        try:
            return self._results.get_nowait()
        except queue.Empty:
            return None

    def set_profile(self, profile: str) -> None:
        if profile not in PROFILE_LIMITS:
            raise ValueError(f"未知网络档位：{profile}")
        self._profile_name = profile

    def set_paused(self, paused: bool) -> None:
        self._paused = bool(paused)

    def stop(self) -> None:
        self._stop_event.set()
        if self._worker is not None:
            self._jobs.put(None)
            self._worker.join(timeout=0.0 if getattr(self.provider, 'is_offline', False) else 2.0)
            self._worker = None

    def clear_memory(self) -> None:
        with self._lock:
            self._generation += 1
            self._seen.clear()
            self._pending.clear()
        while True:
            try:
                self._results.get_nowait()
            except queue.Empty:
                return

    def _run(self) -> None:
        while not self._stop_event.is_set():
            try:
                job = self._jobs.get(timeout=0.2)
            except queue.Empty:
                release = getattr(self.provider, 'release_if_idle', None)
                if release is not None:
                    release()
                continue
            if job is None:
                return
            generation, observation = job
            if generation != self._generation:
                continue
            key = observation_key(observation.text, observation.language)
            successful = False
            try:
                if self._paused:
                    entry = TranslationEntry(key, observation.text, observation.language, "", "paused", observation.observed_at)
                else:
                    allowed = self._wait_for_budget(len(observation.text))
                    if not allowed or self._paused or self._stop_event.is_set():
                        entry = TranslationEntry(key, observation.text, observation.language, "", "paused", observation.observed_at)
                    else:
                        try:
                            translated = self.provider.translate(observation.text, observation.language)
                            entry = TranslationEntry(key, observation.text, observation.language, translated, "translated", observation.observed_at)
                            successful = True
                        except Exception as exc:
                            entry = TranslationEntry(key, observation.text, observation.language, "", f"failed: {exc}", observation.observed_at)
                with self._lock:
                    if generation == self._generation:
                        try:
                            self.cache.put(entry)
                        except OSError:
                            pass  # The visible translation does not depend on a writable cache.
                        self._results.put(entry)
            finally:
                with self._lock:
                    if generation == self._generation:
                        self._pending.discard(key)
                        if not successful:
                            self._seen.discard(key)

    def _wait_for_budget(self, char_count: int) -> bool:
        if getattr(self.provider, 'is_offline', False):
            return not self._stop_event.is_set() and not self._paused
        profile = PROFILE_LIMITS[self._profile_name]
        while not self._stop_event.is_set() and not self._paused:
            now = self._clock()
            delay = 0.0 if self._last_request_at is None else profile.min_interval_seconds - (now - self._last_request_at)
            self._prune_chars(now)
            used_chars = sum(count for _timestamp, count in self._sent_chars)
            if used_chars + char_count <= profile.max_chars_per_minute:
                if delay > 0:
                    self._sleeper(delay)
                    continue
                send_at = self._clock()
                self._sent_chars.append((send_at, char_count))
                self._last_request_at = send_at
                return True
            if not self._sent_chars:
                return True
            wait_for_chars = max(0.01, 60.0 - (now - self._sent_chars[0][0]))
            self._sleeper(max(delay, wait_for_chars))
        return False

    def _prune_chars(self, now: float) -> None:
        while self._sent_chars and now - self._sent_chars[0][0] >= 60.0:
            self._sent_chars.popleft()


def _make_ssl_context() -> ssl.SSLContext:
    try:
        import certifi

        return ssl.create_default_context(cafile=certifi.where())
    except Exception:
        return ssl.create_default_context()
