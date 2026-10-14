"""Headless benchmark contracts; no real model or application is started."""
import importlib
import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from contextlib import redirect_stdout
import io
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tools'))


def case(case_id='ru-1', **changes):
    value = dict(id=case_id, domain='dota', source_language='ru', target_language='zh',
                 length='short', source='не идите в лес', reference='别进野区',
                 meaning='Do not enter the jungle.', severity=0,
                 must_preserve=['negation'], forbidden_meaning=['enter jungle'], tags=['negation'])
    value.update(changes)
    return value


class ChatEvaluationTests(unittest.TestCase):
    def module(self):
        self.assertIsNotNone(importlib.util.find_spec('evaluate_chat'), 'headless benchmark is missing')
        return importlib.import_module('evaluate_chat')

    def runner_fixture(self, directory):
        corpus = directory / 'corpus'
        corpus.mkdir()
        model = directory / 'fixture.gguf'
        model.write_bytes(b'fixture')
        class Engine:
            model_file = model
            server_file = model
            cpu_threads = 2
            _process = None
            closed = False
            def _request(self, *args, **kwargs):
                raise AssertionError('native requests forbidden in runner regressions')
            def translate_message(self, *args, **kwargs):
                raise AssertionError('model inference forbidden in runner regressions')
            def close(self):
                self.closed = True
        args = SimpleNamespace(corpus=corpus, watch_until=1, ids=None, validate_only=False,
                               output=directory, run='run', threads=2, limit=None, retry_errors=False, model=model)
        return args, Engine()

    def test_watch_target_retries_all_selected_failures_once_before_exit(self):
        m = self.module()
        from dota2_map_assistant import llm_translation
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            args, engine = self.runner_fixture(directory)
            rows = [case(), case('ru-2', source='жди меня'), case('ru-3', source='нужен вард'),
                    case('ru-4', source='нет маны')]
            (args.corpus / 'cases.jsonl').write_text(''.join(json.dumps(row) + '\n' for row in rows), encoding='utf-8')
            ids = directory / 'ids.txt'
            ids.write_text('ru-1\nru-2\nru-3\n', encoding='utf-8')
            args.ids, args.retry_errors = ids, True
            fingerprint = {'pipeline': 'fixture'}
            store = m.RunStore(directory / 'run', fingerprint, rows)
            for row in (rows[0], rows[1], rows[3]):
                store.append(dict(id=row['id'], case_digest=m.case_digest(row), status='timeout'))
            class Translator:
                def __init__(self, *args, **kwargs):
                    pass
                def translate(self, *args, **kwargs):
                    raise TimeoutError('still timed out after explicit retry')
            with patch.object(llm_translation, 'LocalLLMEngine', return_value=engine), \
                 patch.object(llm_translation, 'LocalTranslationProvider', Translator), \
                 patch.object(m, 'pipeline_fingerprint', return_value=fingerprint), \
                 patch.object(m, 'memory_snapshot', return_value={'total': 32 * 2**30, 'available': 24 * 2**30}), \
                 patch.object(m.time, 'sleep', side_effect=AssertionError('completed watch must not wait')), redirect_stdout(io.StringIO()):
                self.assertEqual(m.run_benchmark(args), 0)
            results, _ = m.read_results(directory / 'run' / 'results.jsonl')
            self.assertEqual([row['id'] for row in results], ['ru-1', 'ru-2', 'ru-4', 'ru-1', 'ru-2'])
            self.assertEqual([row['status'] for row in results[-2:]], ['timeout', 'timeout'])
            self.assertTrue(engine.closed)

    def test_contending_runner_cannot_overwrite_active_progress(self):
        m = self.module()
        from dota2_map_assistant import llm_translation
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            args, engine = self.runner_fixture(directory)
            (args.corpus / 'cases.jsonl').write_text(json.dumps(case()) + '\n', encoding='utf-8')
            run = directory / 'run'
            with m.run_lock(run):
                progress = run / 'progress.json'
                original = b'{"state":"translating","current_case":"owned-active-case"}\n'
                progress.write_bytes(original)
                with patch.object(llm_translation, 'LocalLLMEngine', return_value=engine):
                    with self.assertRaises((ValueError, OSError)):
                        m.run_benchmark(args)
                self.assertEqual(progress.read_bytes(), original, 'a runner that never held the lock must not publish state')
            self.assertTrue(engine.closed)

    def test_missing_runtime_cannot_overwrite_active_progress(self):
        m = self.module()
        from dota2_map_assistant import llm_translation
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            args, engine = self.runner_fixture(directory)
            engine.server_file = directory / 'missing-runtime'
            (args.corpus / 'cases.jsonl').write_text(json.dumps(case()) + '\n', encoding='utf-8')
            run = directory / 'run'
            with m.run_lock(run):
                progress = run / 'progress.json'
                original = b'{"state":"memory_paused","current_case":"owned-active-case"}\n'
                progress.write_bytes(original)
                with patch.object(llm_translation, 'LocalLLMEngine', return_value=engine):
                    with self.assertRaisesRegex(ValueError, 'missing'):
                        m.run_benchmark(args)
                self.assertEqual(progress.read_bytes(), original)
            self.assertTrue(engine.closed)

    def test_unowned_startup_failure_publishes_fatal_only_while_holding_run_lock(self):
        m = self.module()
        from dota2_map_assistant import llm_translation
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            args, engine = self.runner_fixture(directory)
            engine.server_file = directory / 'missing-runtime'
            (args.corpus / 'cases.jsonl').write_text(json.dumps(case()) + '\n', encoding='utf-8')
            run = directory / 'run'
            original_write = m.atomic_json
            def checked_write(path, value):
                with self.assertRaises((ValueError, OSError)):
                    with m.run_lock(run):
                        pass
                original_write(path, value)
            with patch.object(llm_translation, 'LocalLLMEngine', return_value=engine), \
                 patch.object(m, 'atomic_json', side_effect=checked_write):
                with self.assertRaisesRegex(ValueError, 'missing'):
                    m.run_benchmark(args)
            self.assertEqual(json.loads((run / 'progress.json').read_text(encoding='utf-8'))['state'], 'fatal')
            self.assertTrue(engine.closed)

    def test_rejects_invalid_fields_and_direction(self):
        m = self.module()
        invalid = [dict(severity=True), dict(severity=4), dict(history='context'),
                   dict(source_language='en'), dict(target_language='ru'),
                   dict(must_preserve='negation'), dict(length='tiny'), dict(source=' '),
                   dict(domain='other'), dict(extra='unknown')]
        for changes in invalid:
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                m.validate_case(case(**changes))

    def test_length_bounds_use_actual_source_characters(self):
        m = self.module()
        for language, target, bounds in [('ru', 'zh', (50, 150, 300)), ('zh', 'ru', (20, 60, 140))]:
            letter = 'я' if language == 'ru' else '别'
            for label, low, high in [('short', 1, bounds[0]), ('medium', bounds[0] + 1, bounds[1]), ('long', bounds[1] + 1, bounds[2])]:
                for size in (low, high):
                    m.validate_case(case(source_language=language, target_language=target, length=label, source=letter * size))
                with self.assertRaises(ValueError):
                    m.validate_case(case(source_language=language, target_language=target, length=label, source=letter * (high + 1)))

    def test_corpus_rejects_duplicate_ids_and_same_direction_sources(self):
        m = self.module()
        for rows in ([case(), case()], [case(), case('ru-2')]):
            with self.assertRaisesRegex(ValueError, 'duplicate'):
                m.validate_corpus(rows)
        m.validate_corpus([case(history=['нужны варды']), case('ru-2', history=['нет вардов'])])

    def test_reads_real_authored_corpus_without_inference(self):
        m = self.module()
        rows = m.load_corpus(ROOT / 'eval' / 'chat600')
        self.assertGreater(len(rows), 0)
        self.assertEqual(len(rows), len({row['id'] for row in rows}))

    def test_resume_ignores_and_repairs_incomplete_tail(self):
        m = self.module()
        with tempfile.TemporaryDirectory() as tmp:
            store = m.RunStore(Path(tmp), {'pipeline': 'one'}, [case()])
            row = dict(id='ru-1', case_digest=m.case_digest(case()), status='ok', elapsed_seconds=1)
            store.append(row)
            with (Path(tmp) / 'results.jsonl').open('ab') as stream:
                stream.write(b'{"id":"partial')
            resumed = m.RunStore(Path(tmp), {'pipeline': 'one'}, [case()])
            self.assertEqual(resumed.attempted_ids, {'ru-1'})
            resumed.append(dict(row, id='ru-2'))
            self.assertEqual(len(m.read_results(Path(tmp) / 'results.jsonl')[0]), 2)

    def test_resume_rejects_pipeline_change_or_changed_attempted_case(self):
        m = self.module()
        with tempfile.TemporaryDirectory() as tmp:
            store = m.RunStore(Path(tmp), {'pipeline': 'one'}, [case()])
            store.append(dict(id='ru-1', case_digest=m.case_digest(case()), status='timeout'))
            with self.assertRaisesRegex(ValueError, 'fingerprint'):
                m.RunStore(Path(tmp), {'pipeline': 'two'}, [case()])
            with self.assertRaisesRegex(ValueError, 'changed'):
                m.RunStore(Path(tmp), {'pipeline': 'one'}, [case(source='идите в лес')])

    def test_resume_accepts_growth_and_errors_count_as_attempted(self):
        m = self.module()
        with tempfile.TemporaryDirectory() as tmp:
            store = m.RunStore(Path(tmp), {'pipeline': 'one'}, [case()])
            store.append(dict(id='ru-1', case_digest=m.case_digest(case()), status='hard_failure'))
            resumed = m.RunStore(Path(tmp), {'pipeline': 'one'}, [case(), case('ru-2', source='жди меня')])
            self.assertEqual(resumed.attempted_ids, {'ru-1'})

    def test_rejects_corrupt_complete_result_line(self):
        m = self.module()
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'results.jsonl'
            path.write_text('{broken}\n', encoding='utf-8')
            with self.assertRaises(ValueError):
                m.read_results(path)

    def test_complete_tail_without_newline_can_resume_append(self):
        m = self.module()
        with tempfile.TemporaryDirectory() as tmp:
            rows = [case(), case('ru-2', source='жди меня')]
            store = m.RunStore(Path(tmp), {'pipeline': 'one'}, rows)
            first = dict(id='ru-1', case_digest=m.case_digest(rows[0]), status='ok')
            store.path.write_text(json.dumps(first), encoding='utf-8')
            resumed = m.RunStore(Path(tmp), {'pipeline': 'one'}, rows)
            resumed.append(dict(id='ru-2', case_digest=m.case_digest(rows[1]), status='ok'))
            self.assertEqual(len(m.read_results(store.path)[0]), 2)

    def test_insufficient_total_ram_is_fatal_and_closes_owned_engine(self):
        m = self.module()
        from dota2_map_assistant import llm_translation
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            corpus = directory / 'corpus'
            corpus.mkdir()
            (corpus / 'cases.jsonl').write_text(json.dumps(case()) + '\n', encoding='utf-8')
            model = directory / 'model.gguf'
            model.write_bytes(b'fixture')
            class Engine:
                model_file = model
                server_file = model
                cpu_threads = 2
                closed = False
                def _request(self, *args, **kwargs):
                    raise AssertionError('no model request allowed')
                def translate_message(self, *args, **kwargs):
                    raise AssertionError('no inference allowed')
                def close(self):
                    self.closed = True
            engine = Engine()
            args = SimpleNamespace(corpus=corpus, watch_until=None, ids=None, validate_only=False,
                                   output=directory, run='run', threads=2, limit=None, retry_errors=False)
            with patch.object(llm_translation, 'LocalLLMEngine', return_value=engine), \
                 patch.object(m, 'memory_snapshot', return_value={'total': 4 * 2**30, 'available': 3 * 2**30}, create=True):
                with self.assertRaisesRegex(RuntimeError, 'total RAM'):
                    m.run_benchmark(args)
            self.assertTrue(engine.closed)
            progress = json.loads((directory / 'run' / 'progress.json').read_text(encoding='utf-8'))
            self.assertEqual(progress['state'], 'fatal')
            self.assertEqual(progress['attempted'], 0)

    def test_memory_policy_reserves_ram_before_and_after_loading(self):
        m = self.module()
        size = 18236702880
        self.assertEqual(m.memory_decision(32 * 2**30, 7 * 2**30, size, loaded=True, production_30b=True)['state'], 'ready')
        self.assertEqual(m.memory_decision(32 * 2**30, 5 * 2**30, size, loaded=True, production_30b=True)['state'], 'pause')
        paused = m.memory_decision(32 * 2**30, 23 * 2**30, size, loaded=False, production_30b=True)
        self.assertEqual(paused['state'], 'pause')
        self.assertEqual(paused['required_available_bytes'], size + 8 * 2**30)
        self.assertEqual(m.memory_decision(32 * 2**30, 26 * 2**30, size, loaded=False, production_30b=True)['state'], 'ready')
        self.assertEqual(m.memory_decision(16 * 2**30, 15 * 2**30, size, loaded=False, production_30b=True)['state'], 'fatal')
        self.assertEqual(m.memory_decision(32 * 2**30, 20 * 2**30, 4624648896, loaded=False)['state'], 'ready')

    def test_pause_releases_owned_model_and_waits_until_reload_budget_recovers(self):
        m = self.module()
        import threading
        class Process:
            def poll(self):
                return None
        class Engine:
            _process = Process()
            _lock = threading.RLock()
            def _unload(self):
                self._process = None
        engine = Engine()
        available = [5 * 2**30]
        events = []
        def memory():
            return {'total': 32 * 2**30, 'available': available[0]}
        def sleep(seconds):
            self.assertEqual(seconds, 5)
            self.assertIsNone(engine._process, 'pressure must release the owned model immediately')
            available[0] = (23 if len(events) == 1 else 26) * 2**30
        state = {}
        m.wait_for_memory(engine, 18236702880, True, state, lambda: events.append(dict(state)), memory_reader=memory, sleep=sleep)
        self.assertEqual(len(events), 2, '23 GiB after unload must still wait for the full reload reserve')
        self.assertEqual(state['memory']['state'], 'ready')

    def test_runner_does_not_submit_or_record_case_during_temporary_ram_shortage(self):
        m = self.module()
        from dota2_map_assistant import llm_translation
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            corpus = directory / 'corpus'
            corpus.mkdir()
            (corpus / 'cases.jsonl').write_text(json.dumps(case()) + '\n', encoding='utf-8')
            model = directory / 'custom.gguf'
            model.write_bytes(b'fixture')
            class Engine:
                model_file = model
                server_file = model
                cpu_threads = 2
                _process = None
                closed = False
                calls = 0
                def _request(self, *args, **kwargs):
                    raise AssertionError('no native request allowed')
                def translate_message(self, *args, **kwargs):
                    self.calls += 1
                    if self.calls == 1:
                        raise RuntimeError('Hy-MT2 30B 需要至少 32 GB 内存和 22 GB 当前可用内存；当前可用 20 GB')
                    return '别进野区'
                def close(self):
                    self.closed = True
            engine = Engine()
            available = [7 * 2**30]
            pauses = []
            def memory():
                return {'total': 32 * 2**30, 'available': available[0]}
            def sleep(seconds):
                self.assertEqual(seconds, 5)
                self.assertIn(engine.calls, (0, 1))
                self.assertFalse((directory / 'run' / 'results.jsonl').exists())
                pauses.append(True)
                available[0] = 9 * 2**30
            relative_model = Path('models/fixture-model.gguf')
            args = SimpleNamespace(corpus=corpus, watch_until=None, ids=None, validate_only=False,
                                   output=directory, run='run', threads=2, limit=None, retry_errors=False, model=relative_model)
            original_is_file = Path.is_file
            def is_file(path):
                return True if path == relative_model else original_is_file(path)
            def make_engine(*, model_path, cpu_threads):
                self.assertEqual(model_path, relative_model.resolve(), 'native subprocess cwd must not reinterpret a relative model path')
                return engine
            with patch.object(llm_translation, 'LocalLLMEngine', side_effect=make_engine), patch.object(Path, 'is_file', is_file), \
                 patch.object(m, 'memory_snapshot', side_effect=memory, create=True), patch.object(m.time, 'sleep', side_effect=sleep):
                self.assertEqual(m.run_benchmark(args), 0)
            self.assertTrue(pauses)
            self.assertTrue(engine.closed)
            rows, _ = m.read_results(directory / 'run' / 'results.jsonl')
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]['status'], 'ok')
            self.assertEqual(engine.calls, 2)
            self.assertEqual(len(pauses), 2, 'a load-time RAM race must wait and retry without recording a failure')

    def test_model_identity_pins_7b_without_mislabeling_generic_local_files(self):
        m = self.module()
        stat = SimpleNamespace(st_size=4624648896, st_mtime_ns=123)
        pinned = m.model_identity(Path('Hy-MT2-7B-Q4_K_M.gguf'), stat=stat)
        self.assertEqual(pinned['repository'], 'tencent/Hy-MT2-7B-GGUF')
        self.assertEqual(pinned['revision'], 'ab8472660ac61fac25f1af43fac2599d52a8a775')
        self.assertEqual(pinned['expected_sha256'], '9f96256500f3fc1ab4d64336b58f52a949a95ad7516b0c229476eef782f9f77b')
        generic = m.model_identity(Path('my-custom.gguf'), stat=stat)
        self.assertIsNone(generic['repository'])
        self.assertIsNone(generic['revision'])
        self.assertEqual(generic['actual_size'], 4624648896)

    def test_resume_keeps_translation_fingerprint_when_only_harness_changes(self):
        m = self.module()
        old = {'files_sha256': {'src/translation.py': 'same', 'tools/evaluate_chat.py': 'old'}, 'cpu_threads': 2}
        new = {'files_sha256': {'src/translation.py': 'same'}, 'cpu_threads': 2}
        with tempfile.TemporaryDirectory() as tmp:
            original = m.RunStore(Path(tmp), old, [case()])
            original.append(dict(id='ru-1', case_digest=m.case_digest(case()), status='ok'))
            resumed = m.RunStore(Path(tmp), new, [case()])
            self.assertEqual(resumed.attempted_ids, {'ru-1'})
            self.assertEqual(resumed.manifest['fingerprint'], old, 'do not rewrite historical pipeline identity')
            self.assertTrue(resumed.manifest['harness_sources'])
            with self.assertRaisesRegex(ValueError, 'fingerprint'):
                m.RunStore(Path(tmp), dict(new, cpu_threads=1), [case()])

    def test_summary_separates_timeout_cutoff_and_hard_failure(self):
        m = self.module()
        rows = [dict(id=str(i), status=status, elapsed_seconds=float(i + 1),
                     automatic_flags=['numbers_changed'] if i == 0 else [],
                     engine_call_count=1, path='model', rss_peak_bytes=1024, cpu_seconds=.2)
                for i, status in enumerate(['ok', 'ok', 'timeout', 'cutoff', 'hard_failure'])]
        result = m.summarize(rows)
        self.assertEqual(result['attempted'], 5)
        self.assertEqual(result['counts'], {'ok': 2, 'timeout': 1, 'cutoff': 1, 'hard_failure': 1})
        self.assertEqual(result['latency_seconds']['median'], 3.0)
        self.assertEqual(result['latency_seconds']['p95'], 5.0)
        self.assertEqual(result['automatic_flag_counts'], {'numbers_changed': 1})
        self.assertNotIn('quality_score', result)

    def test_flags_are_only_mechanical_and_do_not_require_reference_phrase(self):
        m = self.module()
        row = case(source='жди 15 секунд', reference='等待十五秒')
        self.assertEqual(m.mechanical_flags(row, '等15秒'), [])
        self.assertIn('numbers_changed', m.mechanical_flags(row, '等20秒'))
        self.assertIn('placeholder_leak', m.mechanical_flags(row, '等[[DOTA_1]]'))
        self.assertIn('target_script_missing', m.mechanical_flags(row, 'wait here'))

    def test_fingerprint_uses_file_content_and_cpu_threads(self):
        m = self.module()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for name in m.PIPELINE_FILES:
                path = root / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text('before', encoding='utf-8')
            identity = {'repository': 'pinned', 'revision': 'abc', 'filename': 'model.gguf'}
            first = m.pipeline_fingerprint(root, identity, 2)
            (root / m.PIPELINE_FILES[0]).write_text('after', encoding='utf-8')
            self.assertNotEqual(first, m.pipeline_fingerprint(root, identity, 2))
            self.assertNotEqual(first, m.pipeline_fingerprint(root, identity, 1))

    def test_evaluate_uses_explicit_context_and_source_alias(self):
        m = self.module()
        class Translator:
            def translate(self, text, language, *, history):
                if (text, language, history) != ('не идите в лес', 'rus', ('нужны варды',)):
                    raise AssertionError('context or source alias changed')
                return '别进野区'
        row = m.evaluate_case(case(history=['нужны варды']), Translator())
        self.assertEqual(row['status'], 'ok')
        self.assertEqual(row['output'], '别进野区')

    def test_exception_chain_distinguishes_timeout_from_hard_failure(self):
        m = self.module()
        class Translator:
            def translate(self, *args, **kwargs):
                try:
                    raise TimeoutError('native timeout')
                except TimeoutError as exc:
                    raise RuntimeError('wrapped failure') from exc
        row = m.evaluate_case(case(), Translator())
        self.assertEqual(row['status'], 'timeout')

    def test_production_wrapper_with_connection_refusal_is_a_hard_failure(self):
        m = self.module()
        from urllib.error import URLError
        class Translator:
            def translate(self, *args, **kwargs):
                try:
                    raise URLError(ConnectionRefusedError('connection refused'))
                except URLError as exc:
                    raise RuntimeError('本地翻译中断或超时，请重试') from exc
        self.assertEqual(m.evaluate_case(case(), Translator())['status'], 'hard_failure')

    def test_production_wrapper_preserves_typed_timeout_causes(self):
        m = self.module()
        from urllib.error import URLError
        for cause in (TimeoutError('native timeout'), URLError(TimeoutError('socket timeout'))):
            class Translator:
                def translate(self, *args, **kwargs):
                    try:
                        raise cause
                    except Exception as exc:
                        raise RuntimeError('本地翻译中断或超时，请重试') from exc
            with self.subTest(cause=type(cause).__name__):
                self.assertEqual(m.evaluate_case(case(), Translator())['status'], 'timeout')

    def test_model_loading_timeout_without_cause_remains_timeout(self):
        m = self.module()
        self.assertEqual(m.error_status(RuntimeError('本地模型加载超时'), {}), 'timeout')

    def test_native_metrics_capture_only_completion_metadata(self):
        m = self.module()
        class Engine:
            def _request(self, endpoint, payload=None, *, timeout=45):
                return {'choices': [{'finish_reason': 'length', 'message': {'content': 'secret output'}}],
                        'usage': {'prompt_tokens': 12, 'completion_tokens': 4},
                        'timings': {'predicted_ms': 250, 'predicted_per_second': 16}, 'api_key': 'hidden'}
            def translate_message(self, *args, **kwargs):
                self._request('/v1/chat/completions')
                return '测试'
        engine = Engine()
        probe = m.EngineProbe(engine)
        engine.translate_message('test', 'ru', 'zh')
        result = probe.snapshot()
        self.assertEqual(result['engine_call_count'], 1)
        self.assertEqual(result['input_tokens'], 12)
        self.assertEqual(result['output_tokens'], 4)
        self.assertEqual(result['finish_reasons'], ['length'])
        self.assertNotIn('secret output', json.dumps(result))
        self.assertNotIn('hidden', json.dumps(result))

    def test_cutoff_is_never_written_to_production_provider_cache(self):
        m = self.module()
        from dota2_map_assistant.llm_translation import LocalLLMEngine, LocalTranslationProvider
        from dota2_map_assistant.sqlite_cache import SQLiteTranslationCache
        with tempfile.TemporaryDirectory() as tmp:
            engine = LocalLLMEngine(Path(tmp) / 'model.gguf')
            cache = SQLiteTranslationCache(Path(tmp) / 'isolated.sqlite3')
            provider = LocalTranslationProvider(engine, cache, auto_history=False)
            probe = m.EngineProbe(engine)
            def request(endpoint, payload=None, **kwargs):
                if endpoint == '/tokenize':
                    return {'tokens': [1, 2]}
                return {'choices': [{'finish_reason': 'length', 'message': {'content': '未完成'}}],
                        'usage': {'prompt_tokens': 20, 'completion_tokens': 128}}
            original_wrapper = engine._request
            with patch.object(engine, '_load'), patch.object(probe, 'original_request', request):
                row = m.evaluate_case(case(), provider, probe)
            engine.close()
            self.assertEqual(row['status'], 'cutoff')
            self.assertFalse(cache.path.exists(), 'cutoff translations must not enter cache')


if __name__ == '__main__':
    unittest.main()
