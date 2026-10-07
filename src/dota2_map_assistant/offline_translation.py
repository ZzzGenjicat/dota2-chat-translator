"""Local-only M2M100 CPU inference with one shared model for both directions."""
from __future__ import annotations

import hashlib
import json
import re
import sys
import threading
import time
from contextlib import contextmanager
from pathlib import Path, PureWindowsPath

from .chat_lexicon import ChatLexicon
from .platform_runtime import configure_low_impact_runtime, offline_install_hint
from .sqlite_cache import SQLiteTranslationCache
from .text_recognition import observation_key
from .translator_models import TranslationEntry
from .app_paths import resource_root, user_data_dir, recorded_resource_root

APP_ROOT = resource_root()
DEFAULT_MODEL_PATH = APP_ROOT / 'models' / 'm2m100-418m-ct2-int8'
MODEL_FILES = ('model.bin', 'config.json', 'shared_vocabulary.txt', 'spm.128k.model')
LANGUAGES = {'rus': 'ru', 'zho': 'zh', 'eng': 'en', 'ru': 'ru', 'zh': 'zh', 'en': 'en'}
PIPELINE_VERSION = 'm2m100-int8-chat-v2-beam1-max128'
_INFERENCE_LOCK = threading.Lock()


def resolve_model_path(path: str) -> Path:
    value = Path(path).expanduser() if path.strip() else DEFAULT_MODEL_PATH
    configured = PureWindowsPath(path) if PureWindowsPath(path).is_absolute() else value
    if configured.is_absolute() and not value.exists() and configured.name == DEFAULT_MODEL_PATH.name and DEFAULT_MODEL_PATH.is_dir():
        old_root = recorded_resource_root()
        if old_root is not None and configured == old_root / 'models' / DEFAULT_MODEL_PATH.name:
            return DEFAULT_MODEL_PATH
    return value if value.is_absolute() else APP_ROOT / value


def _cache_filename(model_path: Path, lexicon: ChatLexicon, *, portable: bool = True) -> str:
    # No large weight hashing at launch. Installer verifies hashes; changing files
    # or lexicon content produces a fresh namespace, including custom local models.
    model_id = str(model_path.resolve())
    if portable:
        try:
            model_id = 'bundled:' + model_path.resolve().relative_to(APP_ROOT.resolve()).as_posix()
        except ValueError:
            pass
    identity = [model_id, PIPELINE_VERSION, lexicon.fingerprint]
    for name in MODEL_FILES:
        path = model_path / name
        if path.exists():
            info = path.stat()
            identity.append(f'{name}:{info.st_size}:{info.st_mtime_ns}')
    digest = hashlib.sha256('\n'.join(identity).encode()).hexdigest()[:16]
    return f'translation_cache_offline_{digest}.sqlite3'


def cache_path(model_path: Path, lexicon: ChatLexicon) -> Path:
    return user_data_dir() / 'cache' / _cache_filename(model_path, lexicon)


def make_translation_cache(model_path: Path, lexicon: ChatLexicon) -> SQLiteTranslationCache:
    return SQLiteTranslationCache(cache_path(model_path, lexicon),
                                  legacy_path=APP_ROOT / 'data' / _cache_filename(model_path, lexicon, portable=False))


class M2M100Engine:
    def __init__(self, model_path: Path = DEFAULT_MODEL_PATH, cpu_threads: int = 2,
                 idle_seconds: int = 180) -> None:
        self.model_path = model_path
        self.cpu_threads = max(1, min(4, int(cpu_threads)))
        self.idle_seconds = idle_seconds
        self._lock = threading.RLock()
        self._translator = None
        self._tokenizer = None
        self._last_used = time.monotonic()
        self._status = '模型尚未加载'
        self._closed = False

    @property
    def status(self) -> str:
        return self._status

    def _load(self) -> None:
        if self._closed:
            raise RuntimeError('本地模型已关闭')
        if self._translator is not None:
            return
        missing = [name for name in MODEL_FILES if not (self.model_path / name).is_file()]
        if missing:
            raise RuntimeError('本地模型缺失，' + offline_install_hint() + '：' + ', '.join(missing))
        configure_low_impact_runtime()
        local_dependencies = APP_ROOT / 'vendor' / 'offline'
        if local_dependencies.is_dir() and str(local_dependencies) not in sys.path:
            sys.path.insert(0, str(local_dependencies))
        try:
            import ctranslate2
            import sentencepiece
        except (ImportError, OSError) as exc:
            raise RuntimeError('离线推理依赖不可用，' + offline_install_hint()) from exc
        if 'int8' not in ctranslate2.get_supported_compute_types('cpu'):
            raise RuntimeError('当前 CPU 不支持 CTranslate2 INT8，未切换其他后端')
        config = json.loads((self.model_path / 'config.json').read_text(encoding='utf-8'))
        if config.get('decoder_start_token') != '</s>':
            raise RuntimeError('模型配置不是受支持的 M2M100 模型')
        # Windows SentencePiece cannot reliably open non-ASCII file paths.
        tokenizer = self._tokenizer or sentencepiece.SentencePieceProcessor(model_proto=(self.model_path / 'spm.128k.model').read_bytes())
        translator = ctranslate2.Translator(str(self.model_path), device='cpu', compute_type='int8',
                                            inter_threads=1, intra_threads=self.cpu_threads,
                                            max_queued_batches=1)
        if translator.compute_type not in ('int8', 'int8_float32'):
            raise RuntimeError('模型未使用 INT8 推理')
        self._tokenizer = tokenizer
        self._translator = translator
        self._status = f'INT8 已就绪 · {self.cpu_threads} 线程'

    def warmup(self) -> None:
        try:
            self.translate_segments(['Привет'], 'ru', 'zh')
        except Exception as exc:
            self._status = str(exc)
            raise

    @contextmanager
    def _access(self):
        # Includes tokenization, loading, errors and decoding: retirement must
        # release weights before any other engine can enter the global gate.
        with _INFERENCE_LOCK, self._lock:
            try:
                if self._closed:
                    raise RuntimeError('本地模型已关闭')
                yield
            finally:
                self._last_used = time.monotonic()
                if self._closed:
                    self._translator = None
                    self._tokenizer = None

    def translate_segments(self, texts: list[str], source: str, target: str) -> list[str]:
        with self._access():
            self._load()
            sources = []
            total_tokens = 0
            for text in texts:
                pieces = self._tokenizer.encode(text, out_type=str)
                total_tokens += len(pieces)
                if total_tokens > 192:
                    raise ValueError('消息过长，请拆成较短的聊天句子（最多 192 token）')
                sources.append([f'__{source}__', *pieces, '</s>'])
            try:
                results = self._translator.translate_batch(sources, target_prefix=[[f'__{target}__']] * len(sources),
                                                            beam_size=1, return_scores=False,
                                                            max_batch_size=1, max_input_length=0,
                                                            max_decoding_length=128)
                translated = []
                for result in results:
                    tokens = result.hypotheses[0]
                    if len(tokens) >= 128:
                        raise ValueError('译文过长，请拆成短句，避免输出被截断')
                    tokens = [token for token in tokens if not (token.startswith('__') and token.endswith('__'))
                              and token not in ('<s>', '</s>', '<pad>')]
                    output = self._tokenizer.decode(tokens).strip()
                    if not output:
                        raise RuntimeError('本地模型未返回译文')
                    translated.append(output)
                return translated
            finally:
                self._last_used = time.monotonic()
                if self._closed:
                    self._translator = None
                    self._tokenizer = None

    def validate_text(self, text: str) -> None:
        with self._access():
            if self._closed:
                raise RuntimeError('本地模型已关闭')
            if self._tokenizer is None:
                local_dependencies = APP_ROOT / 'vendor' / 'offline'
                if local_dependencies.is_dir() and str(local_dependencies) not in sys.path:
                    sys.path.insert(0, str(local_dependencies))
                try:
                    import sentencepiece
                    self._tokenizer = sentencepiece.SentencePieceProcessor(model_proto=(self.model_path / 'spm.128k.model').read_bytes())
                except (ImportError, OSError) as exc:
                    raise RuntimeError('本地模型分词文件不可用，' + offline_install_hint()) from exc
            if len(self._tokenizer.encode(text, out_type=str)) > 192:
                raise ValueError('消息过长，请拆成较短的聊天句子（最多 192 token）')
            self._last_used = time.monotonic()

    def release_if_idle(self) -> None:
        if time.monotonic() - self._last_used < self.idle_seconds:
            return
        if not _INFERENCE_LOCK.acquire(blocking=False):
            return
        try:
            if not self._lock.acquire(blocking=False):
                return
            try:
                if time.monotonic() - self._last_used >= self.idle_seconds:
                    self._translator = None
                    self._tokenizer = None
                    self._status = '空闲时已释放模型；下一条自动加载'
            finally:
                if self._closed:
                    self._translator = None
                    self._tokenizer = None
                self._lock.release()
        finally:
            _INFERENCE_LOCK.release()

    def close(self) -> None:
        self._closed = True
        # A running inference releases its own weights in finally. Closing the
        # window and applying settings must never wait on a long translation.
        if self._lock.acquire(blocking=False):
            try:
                self._translator = None
                self._tokenizer = None
            finally:
                self._lock.release()


class OfflineTranslationProvider:
    is_offline = True

    def __init__(self, engine: M2M100Engine, cache: SQLiteTranslationCache,
                 target_language: str = 'zh', lexicon: ChatLexicon | None = None) -> None:
        self.engine = engine
        self.cache = cache
        self.target_language = target_language
        self.lexicon = lexicon or ChatLexicon()

    def translate(self, text: str, source_language: str) -> str:
        source = LANGUAGES.get(source_language)
        if source not in ('ru', 'zh', 'en') or (source, self.target_language) not in (('ru', 'zh'), ('zh', 'ru'), ('en', 'zh')):
            raise ValueError('本地翻译支持俄语→中文、中文→俄语和英语→中文')
        text = text.strip()
        if not text:
            raise ValueError('请输入要翻译的消息')
        if len(text) > 1000:
            raise ValueError('消息过长，请拆成短句')
        # Include target language explicitly; never reuse a reverse-direction entry.
        key = f'{source}>{self.target_language}:' + observation_key(text, source_language)
        generation = getattr(self.cache, 'generation', None)
        try:
            cached = self.cache.get(key)
        except OSError:
            cached = None
        if cached:
            return cached.translated_text
        exact = self.lexicon.exact(text, source) if source in ('ru', 'zh') else None
        if exact is not None:
            output = exact
        else:
            validate = getattr(self.engine, 'validate_text', None)
            if validate is not None:
                validate(text)
            chunks = self.lexicon.split(text, source) if source in ('ru', 'zh') else [(text, False)]
            pending = [chunk.strip() for chunk, protected in chunks if not protected and re.search(r'\w', chunk)]
            if len(pending) > 8:
                raise ValueError('消息过长或片段过多，请拆成较短的聊天句子')
            results = iter(self.engine.translate_segments(pending, source, self.target_language) if pending else [])
            parts = []
            for chunk, protected in chunks:
                if protected or not re.search(r'\w', chunk):
                    parts.append(chunk)
                else:
                    parts.append(next(results))
            output = (' ' if self.target_language == 'ru' else '').join(parts).strip()
            output = re.sub(r'\s+([,.;:!?，。！？])', r'\1', output)
            output = re.sub(r'\s{2,}', ' ', output)
        if self.target_language == 'ru':
            output = output.translate(str.maketrans('，。！？；：', ',.!?;:'))
        else:
            output = re.sub(r'(?<=[\u4e00-\u9fff])\s+(?=[\u4e00-\u9fff])', '', output)
        if not output:
            raise RuntimeError('本地模型未返回译文')
        if self.target_language == 'ru' and re.search(r'[\u4e00-\u9fff]', output):
            raise ValueError('本地模型未返回完整俄语译文')
        if self.target_language == 'ru' and exact is None and not re.search(r'[\u0400-\u052f]', output):
            if not re.fullmatch(r'[\d\s,.!?+%\-]+', output):
                raise ValueError('本地模型未返回俄语译文')
        try:
            entry = TranslationEntry(key, text, source_language, output, 'translated', time.time())
            if generation is None:
                self.cache.put(entry)
            else:
                self.cache.put(entry, expected_generation=generation)
        except OSError:
            pass  # Cache failure must not prevent an otherwise valid translation.
        return output

    def release_if_idle(self) -> None:
        self.engine.release_if_idle()
