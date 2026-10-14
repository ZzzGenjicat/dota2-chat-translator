"""Resumable, headless CPU benchmark of the production offline chat pipeline.

PowerShell: $env:PYTHONPATH='vendor/offline;src;tests';
  .runtime/python/python.exe -u -X utf8 tools/evaluate_chat.py --run baseline --watch-until 600
No application, model installation, warmup, or production cache/settings writes.
Automatic flags are review aids, never semantic translation quality scores.
"""
from __future__ import annotations

import argparse
from collections import Counter
from contextlib import contextmanager
import hashlib
import json
import math
import os
from pathlib import Path
import re
import statistics
import sys
import threading
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'vendor' / 'offline'), str(ROOT / 'src')]
PIPELINE_FILES = ('src/dota2_map_assistant/llm_translation.py',
                  'src/dota2_map_assistant/chat_lexicon.py', 'data/dota_chat_lexicon.json')
REQUIRED_FIELDS = {'id', 'domain', 'source_language', 'target_language', 'length',
                   'source', 'reference', 'meaning', 'severity', 'must_preserve',
                   'forbidden_meaning', 'tags'}
STATUSES = ('ok', 'timeout', 'cutoff', 'hard_failure')
GIB = 2**30
PINNED_7B = {'repository': 'tencent/Hy-MT2-7B-GGUF',
             'revision': 'ab8472660ac61fac25f1af43fac2599d52a8a775',
             'filename': 'Hy-MT2-7B-Q4_K_M.gguf', 'size': 4624648896,
             'sha256': '9f96256500f3fc1ab4d64336b58f52a949a95ad7516b0c229476eef782f9f77b'}


def canonical_json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)


def case_digest(case):
    return hashlib.sha256(canonical_json(case).encode('utf-8')).hexdigest()


def validate_case(case):
    if not isinstance(case, dict) or not REQUIRED_FIELDS <= case.keys() or case.keys() - REQUIRED_FIELDS - {'history'}:
        raise ValueError('invalid case fields')
    for key in ('id', 'source', 'reference', 'meaning'):
        if not isinstance(case[key], str) or not case[key].strip():
            raise ValueError(f'invalid {key}: nonempty string required')
    if case['source'] != case['source'].strip():
        raise ValueError(f"{case['id']}: source has boundary whitespace")
    if case['domain'] not in ('dota', 'profanity'):
        raise ValueError('invalid domain')
    direction = (case['source_language'], case['target_language'])
    if direction not in (('ru', 'zh'), ('zh', 'ru')):
        raise ValueError('invalid translation direction')
    if type(case['severity']) is not int or not 0 <= case['severity'] <= 3:
        raise ValueError('invalid severity')
    bounds = {'ru': {'short': (1, 50), 'medium': (51, 150), 'long': (151, 300)},
              'zh': {'short': (1, 20), 'medium': (21, 60), 'long': (61, 140)}}
    low_high = bounds[case['source_language']].get(case['length'])
    if low_high is None or not low_high[0] <= len(case['source']) <= low_high[1]:
        raise ValueError(f"{case['id']}: invalid actual {case['length']} source length {len(case['source'])}")
    for key in ('must_preserve', 'forbidden_meaning', 'tags', 'history'):
        value = case.get(key, [])
        if not isinstance(value, list) or any(not isinstance(item, str) or not item.strip() for item in value):
            raise ValueError(f'invalid {key}: list of nonempty strings required')
    return case


def validate_corpus(rows):
    ids, identities = set(), set()
    for case in rows:
        validate_case(case)
        identity = (case['source_language'], case['target_language'], case['source'], tuple(case.get('history', [])))
        if case['id'] in ids or identity in identities:
            raise ValueError(f"duplicate id or source/context in direction: {case['id']}")
        ids.add(case['id'])
        identities.add(identity)
    return rows


def load_corpus(directory, *, growing=False):
    rows = []
    for path in sorted(Path(directory).glob('*.jsonl')):
        data = path.read_bytes()
        lines = data.splitlines(keepends=True)
        for index, raw in enumerate(lines):
            if not raw.strip():
                continue
            try:
                rows.append(json.loads(raw))
            except (ValueError, UnicodeError) as exc:
                if growing and index == len(lines) - 1 and not raw.endswith(b'\n'):
                    break  # Author is still appending this last row.
                raise ValueError(f'{path.name}:{index + 1}: invalid JSON') from exc
    return validate_corpus(rows)


def pipeline_fingerprint(root, model_identity, threads):
    files = {}
    for name in PIPELINE_FILES:
        files[name] = hashlib.sha256((Path(root) / name).read_bytes()).hexdigest()
    # Harness revisions are provenance, separate from translation compatibility.
    for name in ('src/dota2_map_assistant/offline_translation.py',
                 'src/dota2_map_assistant/text_recognition.py', 'src/dota2_map_assistant/dota_terminology.py'):
        path = Path(root) / name
        if path.is_file():
            files[name] = hashlib.sha256(path.read_bytes()).hexdigest()
    return {'schema_version': 1, 'files_sha256': files, 'model': model_identity,
            'cpu_threads': threads, 'gpu_layers': 0, 'auto_history': False}


def translation_identity(fingerprint):
    value = json.loads(canonical_json(fingerprint))
    value.get('files_sha256', {}).pop('tools/evaluate_chat.py', None)
    return value


def model_identity(model, *, stat=None):
    from dota2_map_assistant.local_model_install import REPOSITORY, REVISION, FILES
    model = Path(model)
    stat = stat if stat is not None else model.stat()
    repository = revision = size = digest = None
    if model.name in FILES:
        repository, revision = REPOSITORY, REVISION
        size, digest = FILES[model.name]
    elif model.name == PINNED_7B['filename']:
        repository, revision = PINNED_7B['repository'], PINNED_7B['revision']
        size, digest = PINNED_7B['size'], PINNED_7B['sha256']
    return {'repository': repository, 'revision': revision, 'filename': model.name,
            'expected_size': size, 'expected_sha256': digest,
            'actual_path': str(model.resolve()), 'actual_size': stat.st_size,
            'actual_mtime_ns': stat.st_mtime_ns}


def memory_decision(total, available, model_bytes, *, loaded, production_30b=False):
    """Read-only policy: leave 6 GiB free plus 2 GiB loading overhead."""
    reload_budget = max(model_bytes + 8 * GIB, 22 * GIB if production_30b else 0)
    total_budget = max(reload_budget, 31 * GIB if production_30b else 0)
    required = 6 * GIB if loaded else reload_budget
    state = 'fatal' if total < total_budget else ('pause' if available < required else 'ready')
    return {'state': state, 'loaded': loaded, 'total_ram_bytes': total, 'available_ram_bytes': available,
            'required_available_bytes': required, 'required_total_bytes': total_budget}


def memory_snapshot():
    import psutil
    memory = psutil.virtual_memory()
    return {'total': memory.total, 'available': memory.available}


def release_owned_model(engine):
    # Called only between cases by the sole inference owner. Keep the engine
    # reusable; close() would permanently disable subsequent translations.
    process = getattr(engine, '_process', None)
    if process is None or process.poll() is not None:
        return
    if engine._lock.acquire(blocking=False):
        try:
            engine._unload()
        finally:
            engine._lock.release()


def wait_for_memory(engine, model_bytes, production_30b, state, persist, *, memory_reader=None, sleep=None):
    memory_reader = memory_reader or memory_snapshot
    sleep = sleep or time.sleep
    while True:
        memory = memory_reader()
        process = getattr(engine, '_process', None)
        loaded = process is not None and process.poll() is None
        decision = memory_decision(memory['total'], memory['available'], model_bytes,
                                   loaded=loaded, production_30b=production_30b)
        state['memory'] = decision
        if decision['state'] == 'fatal':
            raise RuntimeError(f"total RAM cannot support this model safely: {memory['total'] / GIB:.1f} GiB; "
                               f"required {decision['required_total_bytes'] / GIB:.1f} GiB")
        if decision['state'] == 'ready':
            return
        state['state'] = 'memory_paused'
        release_owned_model(engine)
        persist()
        sleep(5)


def transient_memory_guard_failure(row):
    return (row.get('status') == 'hard_failure'
            and row.get('error', {}).get('type') == 'RuntimeError'
            and '需要至少 32 GB 内存和 22 GB 当前可用内存' in row['error'].get('message', ''))


def atomic_json(path, value):
    path = Path(path)
    temporary = path.with_name(path.name + f'.{os.getpid()}.{threading.get_ident()}.tmp')
    with temporary.open('w', encoding='utf-8', newline='\n') as stream:
        stream.write(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + '\n')
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)


def read_results(path):
    """Return complete rows and byte offset before an interrupted last write."""
    path = Path(path)
    if not path.exists():
        return [], 0
    data, rows, valid_bytes = path.read_bytes(), [], 0
    lines = data.splitlines(keepends=True)
    for index, raw in enumerate(lines):
        try:
            row = json.loads(raw)
            if not isinstance(row, dict) or not isinstance(row.get('id'), str):
                raise ValueError('invalid result record')
        except (ValueError, UnicodeError) as exc:
            if index == len(lines) - 1 and not raw.endswith(b'\n'):
                return rows, valid_bytes
            raise ValueError(f'corrupt complete results line {index + 1}') from exc
        rows.append(row)
        valid_bytes += len(raw)
    return rows, valid_bytes


class RunStore:
    """Append-only durable attempts; manifest allows corpus growth, never edits."""
    def __init__(self, directory, fingerprint, cases):
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True)
        self.path = self.directory / 'results.jsonl'
        self.lock = threading.RLock()
        manifest_path = self.directory / 'manifest.json'
        if manifest_path.exists():
            self.manifest = json.loads(manifest_path.read_text(encoding='utf-8'))
            if translation_identity(self.manifest.get('fingerprint')) != translation_identity(fingerprint):
                raise ValueError('pipeline/model fingerprint changed; use a new --run name')
        else:
            if self.path.exists() and self.path.stat().st_size:
                raise ValueError('results without compatible fingerprint manifest')
            self.manifest = {'fingerprint': fingerprint, 'created_at': time.time(), 'cases': {}}
        provenance = self.manifest.setdefault('harness_sources', [])
        old_hash = self.manifest['fingerprint'].get('files_sha256', {}).get('tools/evaluate_chat.py')
        current_hash = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
        for digest in (old_hash, current_hash):
            if digest and not any(item['sha256'] == digest for item in provenance):
                provenance.append({'sha256': digest, 'recorded_at': time.time()})
        self.rows, valid_bytes = read_results(self.path)
        self.check_cases(cases)
        atomic_json(manifest_path, self.manifest)
        known = self.manifest['cases']
        for row in self.rows:
            if row['id'] not in known or row.get('case_digest') != known[row['id']]:
                raise ValueError(f"result source fingerprint changed: {row['id']}")
            if row.get('status') not in STATUSES:
                raise ValueError(f"invalid recorded result status: {row['id']}")
        if self.path.exists() and self.path.stat().st_size != valid_bytes:
            with self.path.open('r+b') as stream:
                stream.truncate(valid_bytes)
                stream.flush()
                os.fsync(stream.fileno())
        if valid_bytes:
            with self.path.open('r+b') as stream:
                stream.seek(-1, os.SEEK_END)
                if stream.read(1) != b'\n':
                    stream.write(b'\n')
                    stream.flush()
                    os.fsync(stream.fileno())

    @property
    def attempted_ids(self):
        with self.lock:
            return {row['id'] for row in self.rows}

    def check_cases(self, cases):
        current = {case['id']: case_digest(case) for case in cases}
        known = self.manifest['cases']
        for case_id, digest in known.items():
            if current.get(case_id) != digest:
                raise ValueError(f'corpus source/context changed or removed: {case_id}')
        if current != known:
            self.manifest['cases'] = current
            atomic_json(self.directory / 'manifest.json', self.manifest)
        elif not (self.directory / 'manifest.json').exists():
            atomic_json(self.directory / 'manifest.json', self.manifest)

    def append(self, row):
        with self.lock:
            with self.path.open('a', encoding='utf-8', newline='\n') as stream:
                stream.write(canonical_json(row) + '\n')
                stream.flush()
                os.fsync(stream.fileno())
            self.rows.append(row)

    def snapshot_rows(self):
        with self.lock:
            return list(self.rows)


def mechanical_flags(case, output):
    flags = []
    if not output.strip():
        flags.append('empty_output')
    if re.search(r'\[\[DOTA_|〖待翻译文本〗|\[\[.*?\]\]', output):
        flags.append('placeholder_leak')
    script = r'[\u4e00-\u9fff]' if case['target_language'] == 'zh' else r'[\u0400-\u052f]'
    if not re.search(script, output) and not re.fullmatch(r'[\d\s,.!?+%:\-]+', output) and output.strip(' .!?').casefold() not in {'bkb', 'gg', 'ez', 'afk', 'tp'}:
        flags.append('target_script_missing')
    if case['target_language'] == 'ru' and re.search(r'[\u4e00-\u9fff]', output):
        flags.append('unexpected_chinese_in_russian')
    # Arabic digits only: Chinese spelled-out numbers require human review.
    source_numbers = Counter(re.findall(r'\d+(?:[.,]\d+)?', case['source']))
    output_numbers = Counter(re.findall(r'\d+(?:[.,]\d+)?', output))
    if source_numbers != output_numbers:
        flags.append('numbers_changed')
    return flags


class EngineProbe:
    """Observe the owned engine; store no requests, headers, or raw responses."""
    def __init__(self, engine):
        self.engine = engine
        self.original_request = engine._request
        self.original_translate = engine.translate_message
        self.lock = threading.RLock()
        self.reset()
        engine._request = self.request
        engine.translate_message = self.translate

    def reset(self):
        with self.lock:
            self.metrics = {'engine_call_count': 0, 'completion_call_count': 0,
                            'input_tokens': 0, 'output_tokens': 0, 'finish_reasons': [],
                            'completion_seconds': 0.0, 'native_timings': []}

    def snapshot(self):
        with self.lock:
            return json.loads(json.dumps(self.metrics))

    def translate(self, *args, **kwargs):
        with self.lock:
            self.metrics['engine_call_count'] += 1
        return self.original_translate(*args, **kwargs)

    def request(self, endpoint, payload=None, **kwargs):
        started = time.perf_counter()
        completion = endpoint == '/v1/chat/completions'
        if completion:
            with self.lock:
                self.metrics['completion_call_count'] += 1
        try:
            result = self.original_request(endpoint, payload, **kwargs)
            if completion:
                with self.lock:
                    usage = result.get('usage', {})
                    for source, target in (('prompt_tokens', 'input_tokens'), ('completion_tokens', 'output_tokens')):
                        value = usage.get(source)
                        if type(value) is int and value >= 0:
                            self.metrics[target] += value
                    for choice in result.get('choices', []):
                        reason = choice.get('finish_reason')
                        if reason in ('stop', 'length', 'content_filter', 'tool_calls', None):
                            self.metrics['finish_reasons'].append(reason)
                    timings = {key: value for key, value in result.get('timings', {}).items()
                               if key in ('prompt_n', 'prompt_ms', 'prompt_per_second', 'predicted_n',
                                          'predicted_ms', 'predicted_per_second', 'prompt_per_token_ms', 'predicted_per_token_ms')
                               and type(value) in (int, float) and math.isfinite(value)}
                    if timings:
                        self.metrics['native_timings'].append(timings)
            return result
        finally:
            if completion:
                with self.lock:
                    self.metrics['completion_seconds'] += time.perf_counter() - started


class TrackingCache:
    def __init__(self, cache):
        self.cache = cache
        self.hits = 0

    @property
    def generation(self):
        return self.cache.generation

    def get(self, key):
        value = self.cache.get(key)
        if value is not None:
            self.hits += 1
        return value

    def put(self, *args, **kwargs):
        return self.cache.put(*args, **kwargs)


def error_status(error, metrics):
    if 'length' in metrics.get('finish_reasons', []) or '译文未完成' in str(error):
        return 'cutoff'
    seen = set()
    chain = []
    while error is not None and id(error) not in seen:
        seen.add(id(error))
        chain.append(error)
        nested = error.__cause__ or error.__context__
        if nested is None and isinstance(getattr(error, 'reason', None), BaseException):
            nested = error.reason  # URLError keeps socket failures in reason.
        error = nested
    if any(isinstance(cause, TimeoutError) for cause in chain):
        return 'timeout'
    # Outer interruption wrappers mention timeout for every network failure.
    # Only the innermost reason can provide textual timeout evidence.
    message = str(chain[-1]) if chain else ''
    if message != '本地翻译中断或超时，请重试' and ('超时' in message or 'timed out' in message.lower()):
        return 'timeout'
    return 'hard_failure'


def evaluate_case(case, translator, probe=None, cache=None, monitor=None):
    if probe:
        probe.reset()
    if monitor:
        monitor.begin_case()
    hits_before = cache.hits if cache else 0
    started = time.perf_counter()
    row = dict(case)
    row.update(case_digest=case_digest(case), attempted_at=time.time(), output='', status='ok')
    try:
        row['output'] = translator.translate(case['source'], {'ru': 'rus', 'zh': 'zho'}[case['source_language']],
                                             history=tuple(case.get('history', [])))
        if not isinstance(row['output'], str) or not row['output'].strip():
            raise ValueError('empty translator output')
    except Exception as exc:
        row['status'] = error_status(exc, probe.snapshot() if probe else {})
        message = str(exc)
        secret = getattr(getattr(probe, 'engine', None), '_secret', '')
        if secret:
            message = message.replace(secret, '[redacted]')
        message = re.sub(r'(?i)(bearer\s+|api[-_ ]?key[=: ]+)[^\s,;]+', r'\1[redacted]', message)
        row['error'] = {'type': type(exc).__name__, 'message': message[:1000]}
    row['elapsed_seconds'] = time.perf_counter() - started
    row.update(probe.snapshot() if probe else {'engine_call_count': 0})
    row['cache_hits'] = (cache.hits - hits_before) if cache else 0
    row['path'] = 'cache' if row['cache_hits'] else ('model' if row['engine_call_count'] else ('exact' if row['status'] == 'ok' else 'error'))
    row['automatic_flags'] = mechanical_flags(case, row['output']) if row['status'] == 'ok' else []
    row['semantic_review'] = 'pending'
    if monitor:
        row.update(monitor.snapshot())
    return row


def distribution(values):
    values = sorted(values)
    return {'count': len(values), 'median': statistics.median(values) if values else None,
            'p95': values[math.ceil(len(values) * .95) - 1] if values else None,
            'min': values[0] if values else None, 'max': values[-1] if values else None}


def summarize(rows):
    # Explicit retry appends attempts; aggregate case outcomes from the latest attempt.
    latest = {row['id']: row for row in rows}
    cases = list(latest.values())
    counts = Counter(row.get('status') for row in cases)
    result = {'attempted': len(cases), 'attempt_records': len(rows),
              'counts': {status: counts[status] for status in STATUSES},
              'latency_seconds': distribution([row['elapsed_seconds'] for row in cases if 'elapsed_seconds' in row]),
              'model_latency_seconds': distribution([row['elapsed_seconds'] for row in cases if row.get('path') == 'model' and 'elapsed_seconds' in row]),
              'successful_model_latency_seconds': distribution([row['elapsed_seconds'] for row in cases if row.get('path') == 'model' and row.get('status') == 'ok' and 'elapsed_seconds' in row]),
              'automatic_flag_counts': dict(Counter(flag for row in cases for flag in row.get('automatic_flags', []))),
              'paths': dict(Counter(row.get('path', 'unknown') for row in cases)),
              'engine_call_count': sum(row.get('engine_call_count', 0) for row in rows),
              'completion_call_count': sum(row.get('completion_call_count', 0) for row in rows),
              'input_tokens': sum(row.get('input_tokens', 0) for row in rows),
              'output_tokens': sum(row.get('output_tokens', 0) for row in rows),
              'peak_owned_rss_bytes': max((row.get('rss_peak_bytes', 0) for row in rows), default=0),
              'owned_cpu_seconds': sum(row.get('cpu_seconds', 0) for row in rows),
              'semantic_review': 'pending; automatic flags do not measure meaning or severity',
              'number_check_limit': 'Arabic digits only; Chinese/Russian spelled-out numbers require review'}
    for label in ('domain', 'direction', 'length'):
        groups = {}
        for row in cases:
            key = f"{row.get('source_language')}>{row.get('target_language')}" if label == 'direction' else row.get(label, 'unknown')
            groups.setdefault(key, []).append(row)
        result[f'by_{label}'] = {key: {'attempted': len(group), 'statuses': dict(Counter(row['status'] for row in group)),
                                              'latency_seconds': distribution([row['elapsed_seconds'] for row in group if 'elapsed_seconds' in row])}
                                      for key, group in groups.items()}
    return result


class ResourceMonitor:
    """Sample only the engine's owned PID; available RAM is read-only."""
    def __init__(self, engine):
        import psutil
        self.psutil = psutil
        self.engine = engine
        self.lock = threading.RLock()
        self.stop_event = threading.Event()
        self.previous_cpu = {}
        self.current = {}
        self.begin_case()
        self.thread = threading.Thread(target=self._loop, daemon=True)
        self.thread.start()

    def begin_case(self):
        with self.lock:
            self.current = {'owned_pid': None, 'rss_bytes': 0, 'rss_peak_bytes': 0, 'cpu_seconds': 0.0,
                            'available_ram_bytes': int(self.psutil.virtual_memory().available)}
        self.sample()

    def sample(self):
        process = getattr(self.engine, '_process', None)
        with self.lock:
            self.current['available_ram_bytes'] = int(self.psutil.virtual_memory().available)
            if process is None or process.poll() is not None:
                self.current['owned_pid'], self.current['rss_bytes'] = None, 0
                return
            try:
                native = self.psutil.Process(process.pid)
                rss = native.memory_info().rss
                cpu = native.cpu_times()
                total = cpu.user + cpu.system
                previous = self.previous_cpu.get(process.pid, 0.0)
                self.previous_cpu[process.pid] = total
                self.current['owned_pid'] = process.pid
                self.current['rss_bytes'] = rss
                self.current['rss_peak_bytes'] = max(self.current['rss_peak_bytes'], rss)
                self.current['cpu_seconds'] += max(0, total - previous)
            except (self.psutil.Error, OSError):
                pass

    def snapshot(self):
        self.sample()
        with self.lock:
            return dict(self.current)

    def _loop(self):
        while not self.stop_event.wait(.5):
            self.sample()

    def close(self):
        self.stop_event.set()
        self.thread.join(2)


@contextmanager
def run_lock(directory):
    """OS lock releases even after a terminated benchmark process."""
    directory.mkdir(parents=True, exist_ok=True)
    with (directory / 'run.lock').open('a+b') as stream:
        stream.seek(0, 2)
        if stream.tell() == 0:
            stream.write(b'0')
            stream.flush()
        stream.seek(0)
        if os.name == 'nt':
            import msvcrt
            try:
                msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
            except OSError as exc:
                raise ValueError('another process is using this --run name') from exc
        else:
            import fcntl
            fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        try:
            yield
        finally:
            stream.seek(0)
            if os.name == 'nt':
                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(stream, fcntl.LOCK_UN)


def load_ids(path):
    if path is None:
        return None
    raw = path.read_text(encoding='utf-8-sig').strip()
    ids = json.loads(raw) if raw.startswith('[') else [line.strip() for line in raw.splitlines() if line.strip()]
    if not isinstance(ids, list) or any(not isinstance(value, str) or not value for value in ids) or len(set(ids)) != len(ids):
        raise ValueError('--ids requires unique string IDs, one per line or a JSON array')
    return set(ids)


def run_benchmark(args):
    from dota2_map_assistant.llm_translation import LocalLLMEngine, LocalTranslationProvider
    from dota2_map_assistant.chat_lexicon import ChatLexicon
    from dota2_map_assistant.sqlite_cache import SQLiteTranslationCache
    from dota2_map_assistant.release_smoke import offline_network_guard
    cases = load_corpus(args.corpus, growing=bool(args.watch_until))
    selected_ids = load_ids(args.ids)
    if not args.watch_until and selected_ids and selected_ids - {case['id'] for case in cases}:
        raise ValueError('--ids contains IDs absent from corpus')
    if args.validate_only:
        print(canonical_json({'validated_cases': len(cases), 'by_domain': dict(Counter(row['domain'] for row in cases))}), flush=True)
        return 0
    directory = args.output / args.run
    model_override = getattr(args, 'model', None)
    if model_override is not None and (model_override.suffix.lower() != '.gguf' or not model_override.is_file()):
        raise ValueError('--model requires an existing local GGUF file; benchmark never downloads')
    if model_override is not None:
        model_override = model_override.resolve()
    engine = LocalLLMEngine(model_path=model_override, cpu_threads=args.threads)
    monitor = None
    heartbeat_stop = threading.Event()
    heartbeat = None
    store = None
    run_lock_attempted = False
    state = {'state': 'starting', 'current_case': None, 'network_guard_installed': False}
    try:
        model = engine.model_file
        if not model.is_file() or not engine.server_file.is_file():
            raise ValueError('model/runtime missing: install separately; benchmark never downloads')
        stat = model.stat()
        identity = model_identity(model, stat=stat)
        fingerprint = pipeline_fingerprint(ROOT, identity, engine.cpu_threads)
        run_lock_attempted = True
        with run_lock(directory):
            store = RunStore(directory, fingerprint, cases)
            probe = EngineProbe(engine)
            cache = TrackingCache(SQLiteTranslationCache(directory / 'translation_cache.sqlite3'))
            lexicon = ChatLexicon(ROOT / 'data' / 'dota_chat_lexicon.json')
            translators = {target: LocalTranslationProvider(engine, cache, target, lexicon, auto_history=False) for target in ('zh', 'ru')}
            monitor = ResourceMonitor(engine)
            state.update(state='ready', run=args.run, cpu_threads=engine.cpu_threads,
                         model_filename=model.name, corpus_available=len(cases), watch_until=args.watch_until,
                         selected_ids=sorted(selected_ids) if selected_ids else None)

            def persist(*, emit=False):
                summary = summarize(store.snapshot_rows())
                progress = dict(state, updated_at=time.time(), attempted=summary['attempted'],
                                counts=summary['counts'], resources=monitor.snapshot(), current_native=probe.snapshot())
                atomic_json(directory / 'summary.json', dict(summary, run=args.run, state=state['state'],
                                                            network_guard_installed=state['network_guard_installed']))
                atomic_json(directory / 'progress.json', progress)
                if emit:
                    print(canonical_json(progress), flush=True)

            def heartbeat_loop():
                while not heartbeat_stop.wait(30):
                    persist(emit=True)

            with offline_network_guard():
                state['network_guard_installed'] = True
                persist(emit=True)
                heartbeat = threading.Thread(target=heartbeat_loop, daemon=True)
                heartbeat.start()
                this_session = 0
                retried = set()
                try:
                    while True:
                        store.check_cases(cases)
                        state['corpus_available'] = len(cases)
                        latest = {row['id']: row for row in store.snapshot_rows()}
                        attempted_selected = len(store.attempted_ids & selected_ids) if selected_ids else len(store.attempted_ids)
                        pending = [case for case in cases if (selected_ids is None or case['id'] in selected_ids)
                                   and (case['id'] not in latest or (args.retry_errors and latest[case['id']]['status'] != 'ok' and case['id'] not in retried))]
                        if args.watch_until and attempted_selected >= args.watch_until:
                            # Honor explicit retries even after the unique-case
                            # target is reached, without starting extra new IDs.
                            pending = [case for case in pending if case['id'] in latest]
                            if not pending:
                                break
                        if args.limit is not None and this_session >= args.limit:
                            break
                        if not pending:
                            if not args.watch_until:
                                break
                            state.update(state='waiting_for_corpus', current_case=None)
                            persist()
                            time.sleep(5)
                            cases = load_corpus(args.corpus, growing=True)
                            continue
                        case = pending[0]
                        state.update(state='memory_check', current_case=case['id'])
                        wait_for_memory(engine, stat.st_size,
                                        model.name == 'Hy-MT2-30B-A3B-Q4_K_M.gguf', state, persist)
                        state['state'] = 'translating'
                        persist()
                        row = evaluate_case(case, translators[case['target_language']], probe, cache, monitor)
                        if transient_memory_guard_failure(row):
                            # RAM can drop after the pre-case check. A rejected
                            # load is not a translation attempt or cached failure.
                            state['state'] = 'memory_paused'
                            release_owned_model(engine)
                            persist(emit=True)
                            time.sleep(5)
                            continue
                        store.append(row)
                        retried.add(case['id'])
                        this_session += 1
                        state.update(state='running', current_case=None, last_case=case['id'], last_status=row['status'])
                        persist(emit=this_session % 5 == 0)
                        if args.watch_until:
                            cases = load_corpus(args.corpus, growing=True)
                    state.update(state='complete', current_case=None)
                except KeyboardInterrupt:
                    state.update(state='interrupted', current_case=None)
                except Exception as exc:
                    state.update(state='fatal', fatal_error={'type': type(exc).__name__, 'message': str(exc)[:1000]})
                    raise
                finally:
                    heartbeat_stop.set()
                    if heartbeat:
                        heartbeat.join(3)
                    persist(emit=True)
        return 130 if state['state'] == 'interrupted' else 0
    except Exception as exc:
        # Publish preflight failures only under ownership. A rejected run lock
        # must leave the active owner's state untouched, including after it exits.
        if store is None and not run_lock_attempted:
            try:
                with run_lock(directory):
                    atomic_json(directory / 'progress.json', dict(state, state='fatal', updated_at=time.time(),
                                 fatal_error={'type': type(exc).__name__, 'message': str(exc)[:1000]}))
            except (ValueError, OSError):
                pass  # The original startup exception remains visible to caller.
        raise
    finally:
        heartbeat_stop.set()
        engine.close()
        if monitor:
            monitor.close()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run', required=True, help='Unique baseline or improved pipeline name; repeats resume')
    parser.add_argument('--corpus', type=Path, default=ROOT / 'eval' / 'chat600')
    parser.add_argument('--output', type=Path, default=ROOT / '.test_runtime' / 'chat600')
    parser.add_argument('--watch-until', type=int, help='Wait for appended corpus rows until this many unique cases are attempted')
    parser.add_argument('--ids', type=Path, help='Select IDs from newline text or JSON list')
    parser.add_argument('--limit', type=int, help='Maximum additional attempts in this invocation')
    parser.add_argument('--threads', type=int, choices=range(1, 5), default=2)
    parser.add_argument('--model', type=Path, help='Benchmark an existing local GGUF; default remains production 30B Q4')
    parser.add_argument('--retry-errors', action='store_true', help='Explicitly append new attempts for previously failed cases')
    parser.add_argument('--validate-only', action='store_true', help='Validate corpus without loading or starting model')
    args = parser.parse_args(argv)
    if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,79}', args.run) or args.run in ('.', '..'):
        parser.error('--run must be a simple safe directory name')
    if args.watch_until is not None and args.watch_until < 1 or args.limit is not None and args.limit < 0:
        parser.error('--watch-until must be positive and --limit nonnegative')
    try:
        return run_benchmark(args)
    except (ValueError, RuntimeError, OSError) as exc:
        print(canonical_json({'state': 'fatal', 'error_type': type(exc).__name__, 'error': str(exc)[:1000]}), flush=True)
        return 1


if __name__ == '__main__':
    sys.exit(main())
