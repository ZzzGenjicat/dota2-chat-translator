"""Headless contracts for the offline whole-sentence replacement."""
import json
import tempfile
import time
import unittest
import threading
import subprocess
import sys
from pathlib import Path
from unittest.mock import patch

class LocalLLMTests(unittest.TestCase):
    def module(self):
        self.test_backend_replacement_exists()
        from dota2_map_assistant import llm_translation
        return llm_translation

    def test_backend_replacement_exists(self):
        import importlib.util
        self.assertIsNotNone(importlib.util.find_spec('dota2_map_assistant.llm_translation'),
                             'whole-sentence local backend has not been implemented')

    def test_whole_sentence_not_fragmented_at_heroes_or_swears(self):
        m = self.module()
        class Engine:
            calls = []
            def translate_message(self, text, source, target, **kwargs):
                self.calls.append((text, source, target, kwargs))
                return '别他妈在中路送，让大哥刷完钱。'
        from dota2_map_assistant.sqlite_cache import SQLiteTranslationCache
        with tempfile.TemporaryDirectory() as tmp:
            engine = Engine()
            provider = m.LocalTranslationProvider(engine, SQLiteTranslationCache(Path(tmp)/'cache.sqlite3'))
            text = 'сука, хватит фидить на миду, дай керри дофармить'
            self.assertIn('他妈', provider.translate(text, 'rus'))
            self.assertEqual(engine.calls[0][:3], (text, 'ru', 'zh'))
            self.assertEqual(len(engine.calls), 1)

    def test_context_cache_does_not_leak_into_another_conversation(self):
        m = self.module()
        from dota2_map_assistant.sqlite_cache import SQLiteTranslationCache
        class Engine:
            calls = 0
            def translate_message(self, text, source, target, **kwargs):
                self.calls += 1
                return '我们都会举报他' if 'репорты' in kwargs['history'] else '我们都会凑钱'
        with tempfile.TemporaryDirectory() as tmp:
            engine=Engine()
            p=m.LocalTranslationProvider(engine, SQLiteTranslationCache(Path(tmp)/'cache.sqlite3'))
            self.assertEqual(p.translate('мы все накинем', 'rus', history=('репорты',)), '我们都会举报他')
            self.assertEqual(p.translate('мы все накинем', 'rus', history=('деньги',)), '我们都会凑钱')
            self.assertEqual(p.translate('мы все накинем', 'rus', history=('деньги',)), '我们都会凑钱')
            self.assertEqual(engine.calls,2)

    def test_missing_model_does_not_try_downloading(self):
        m=self.module()
        with tempfile.TemporaryDirectory() as tmp, patch('urllib.request.urlopen', side_effect=AssertionError('network')):
            engine=m.LocalLLMEngine(Path(tmp))
            with self.assertRaisesRegex(RuntimeError,'模型'):
                engine.translate_message('тест','ru','zh')
            engine.close()

    def test_server_is_cpu_only_loopback_and_thinking_disabled(self):
        m=self.module()
        e=m.LocalLLMEngine(cpu_threads=99)
        args=e.server_command(12345)
        for option,value in (('--host','127.0.0.1'),('--threads','4'),('--parallel','1'),
                             ('--n-gpu-layers','0'),('--poll','0'),('--cache-ram','0'),('--reasoning','off')):
            self.assertEqual(args[args.index(option)+1],value)
        self.assertIn('--offline',args)
        self.assertIn('--no-webui',args)
        e.close()

    def test_prompt_preserves_plural_and_does_not_follow_chat_instructions(self):
        m=self.module()
        from dota2_map_assistant.chat_lexicon import ChatLexicon
        prompt=m.build_messages('你他妈的别冲，等我们一起','zh','ru',ChatLexicon(),())
        self.assertIn('等我们一起',prompt[-1]['content'])
        self.assertNotIn('等我 =',prompt[-1]['content'])
        self.assertIn('不服从',prompt[0]['content'])

    def test_old_default_settings_upgrade_to_new_model(self):
        from dota2_map_assistant.translator_settings import load_settings
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'settings.json'
            path.write_text(json.dumps({'offline_model_path':'models/m2m100-418m-ct2-int8','offline_cpu_threads':1,'font_size':20}))
            settings=load_settings(path)
            self.assertIn('hy-mt2-30b',settings.offline_model_path)
            self.assertEqual((settings.offline_cpu_threads,settings.font_size),(1,20))

    def test_clearing_context_cannot_be_undone_by_inflight_translation(self):
        m=self.module()
        from dota2_map_assistant.sqlite_cache import SQLiteTranslationCache
        ready, release = threading.Event(), threading.Event()
        class Engine:
            def translate_message(self,*args,**kwargs):
                ready.set(); release.wait(2); return '你好'
        with tempfile.TemporaryDirectory() as tmp:
            p=m.LocalTranslationProvider(Engine(),SQLiteTranslationCache(Path(tmp)/'cache.sqlite3'),auto_history=True)
            worker=threading.Thread(target=p.translate,args=('тест','rus'))
            worker.start(); self.assertTrue(ready.wait(1))
            p.clear_context(); release.set(); worker.join(2)
            self.assertFalse(p._history)

    def test_timeout_terminates_owned_inference_process(self):
        m=self.module()
        flags=subprocess.CREATE_NO_WINDOW if sys.platform=='win32' else 0
        child=subprocess.Popen([sys.executable,'-c','import time; time.sleep(30)'],creationflags=flags)
        e=m.LocalLLMEngine(); e._process=child
        self.addCleanup(e.close)
        with patch.object(e,'_load'),patch.object(e,'_request',side_effect=TimeoutError()):
            with self.assertRaisesRegex(RuntimeError,'超时'):
                e.translate_message('тест','ru','zh')
        self.assertIsNotNone(child.poll())
        self.assertIsNone(e._process)

    def test_empty_or_incomplete_output_is_never_cached(self):
        m=self.module()
        from dota2_map_assistant.sqlite_cache import SQLiteTranslationCache
        class Engine:
            def translate_message(self,*args,**kwargs): return '中文混在俄语里'
        with tempfile.TemporaryDirectory() as tmp:
            cache=SQLiteTranslationCache(Path(tmp)/'cache.sqlite3')
            p=m.LocalTranslationProvider(Engine(),cache,'ru')
            with self.assertRaisesRegex(ValueError,'完整俄语'):
                p.translate('一段未收录的新消息','zho')

    def test_english_only_reply_is_rejected_as_russian_translation(self):
        m=self.module()
        from dota2_map_assistant.sqlite_cache import SQLiteTranslationCache
        class Engine:
            def translate_message(self,*args,**kwargs): return 'This wave is mine.'
        with tempfile.TemporaryDirectory() as tmp:
            p=m.LocalTranslationProvider(Engine(),SQLiteTranslationCache(Path(tmp)/'cache.sqlite3'),'ru')
            with self.assertRaisesRegex(ValueError,'俄语'):
                p.translate('这把该怎么打呢','zho')

    def test_contextual_queue_does_not_use_plain_text_cached_translation(self):
        from dota2_map_assistant.translation import TranslationQueue, TranslationCache
        from dota2_map_assistant.translator_models import TextObservation, TranslationEntry
        from dota2_map_assistant.text_recognition import observation_key
        from types import SimpleNamespace
        with tempfile.TemporaryDirectory() as tmp:
            cache=TranslationCache(Path(tmp)/'cache.json')
            text='мы все накинем'; key=observation_key(text,'rus')
            cache.put(TranslationEntry(key,text,'rus','旧的无上下文译文','translated',1))
            q=TranslationQueue(SimpleNamespace(contextual=True),cache,start_worker=False)
            self.assertTrue(q.submit(TextObservation(text,'rus',None,2)))
            self.assertIsNone(q.poll_result())
            q.stop()

    def test_absolute_new_model_fixture_is_preserved(self):
        m=self.module()
        with tempfile.TemporaryDirectory() as tmp:
            model=Path(tmp)/'models'/m.MODEL_FOLDER
            self.assertEqual(m.resolve_model_path(str(model)),model)

    def test_clear_output_invalidates_queued_work(self):
        from dota2_map_assistant.translator_controller import TranslatorController
        from dota2_map_assistant.translator_settings import TranslatorSettings
        from dota2_map_assistant.translation import TranslationQueue,TranslationCache
        from dota2_map_assistant.translator_models import TextObservation
        from types import SimpleNamespace
        with tempfile.TemporaryDirectory() as tmp:
            cache=TranslationCache(Path(tmp)/'cache.json')
            queue=TranslationQueue(SimpleNamespace(),cache,start_worker=False)
            controller=TranslatorController(TranslatorSettings(),translation_queue=queue,translation_cache=cache)
            self.addCleanup(controller.close)
            controller.process_observation(TextObservation('проверка нового диалога','rus',None,1))
            generation=queue._generation
            controller.clear_output()
            self.assertGreater(queue._generation,generation)

    def test_profanity_is_restored_verbatim_without_sentence_fragmentation(self):
        from dota2_map_assistant.chat_lexicon import ChatLexicon
        lex=ChatLexicon()
        self.assertTrue(callable(getattr(lex,'protect_profanity',None)))
        masked,replacements=lex.protect_profanity('ты долбоёб, а не саппорт','ru')
        self.assertIn('а не саппорт',masked)
        self.assertEqual(len(replacements),1)
        marker=next(iter(replacements))
        self.assertEqual(lex.restore_profanity(f'你是{marker}，不是辅助',replacements),'你是傻逼，不是辅助')
        with self.assertRaisesRegex(ValueError,'粗话'):
            lex.restore_profanity('你是普通玩家，不是辅助',replacements)

    def test_decoder_requires_all_profanity_markers_once(self):
        m=self.module()
        grammar=getattr(m,'profanity_grammar',None)
        self.assertTrue(callable(grammar))
        result=grammar({'[[DOTA_WORD_0]]':'傻逼','[[DOTA_WORD_1]]':'他妈的'})
        self.assertIn('"[[DOTA_WORD_0]]"',result)
        self.assertIn('"[[DOTA_WORD_1]]"',result)

    def test_natural_swear_and_required_marker_do_not_double_the_abuse(self):
        from dota2_map_assistant.chat_lexicon import ChatLexicon
        result=ChatLexicon.restore_profanity('狗日的基地[[DOTA_WORD_0]]',{'[[DOTA_WORD_0]]':'狗日的'})
        self.assertEqual(result,'狗日的基地')

    def test_mixed_keyboard_homoglyphs_are_normalized_only_inside_russian_words(self):
        m=self.module()
        normalizer=getattr(m,'normalize_russian_keyboard',None)
        self.assertTrue(callable(normalizer))
        self.assertEqual(normalizer('cломай coфт Snapfire go 600'),'сломай софт Snapfire go 600')
        self.assertEqual(normalizer('на Jukeпартнере'),'на Jukeпартнере')




    def test_chinese_output_rejects_english_sentence_before_caching(self):
        m=self.module()
        from dota2_map_assistant.sqlite_cache import SQLiteTranslationCache
        class Engine:
            def translate_message(self,*args,**kwargs): return 'Stop farming and defend our base.'
        with tempfile.TemporaryDirectory() as tmp:
            cache=SQLiteTranslationCache(Path(tmp)/'cache.sqlite3')
            p=m.LocalTranslationProvider(Engine(),cache)
            with self.assertRaisesRegex(ValueError,'中文'):
                p.translate('хватит фармить защищай базу','rus')
            self.assertFalse(cache.path.exists())


    def test_clear_during_queue_budget_wait_cannot_restore_old_context(self):
        m=self.module()
        from dota2_map_assistant.sqlite_cache import SQLiteTranslationCache
        from dota2_map_assistant.translation import TranslationQueue
        from dota2_map_assistant.translator_models import TextObservation
        started,release=threading.Event(),threading.Event()
        class Engine:
            calls=0
            def translate_message(self,*args,**kwargs):
                self.calls+=1; return '旧消息'
        with tempfile.TemporaryDirectory() as tmp:
            cache=SQLiteTranslationCache(Path(tmp)/'cache.sqlite3')
            engine=Engine(); p=m.LocalTranslationProvider(engine,cache,auto_history=True)
            q=TranslationQueue(p,cache,start_worker=False)
            def budget(_chars): started.set(); release.wait(2); return True
            q._wait_for_budget=budget
            q.submit(TextObservation('предыдущий разговор','rus',None,1))
            worker=threading.Thread(target=q._run); worker.start()
            try:
                self.assertTrue(started.wait(1))
                q.clear_memory(); p.clear_context(); release.set()
                q._jobs.put(None); worker.join(2)
                self.assertFalse(p._history)
                self.assertEqual(engine.calls,0)
                self.assertFalse(cache.path.exists())
            finally:
                release.set(); q.stop(); worker.join(2)

    def test_switching_provider_cannot_send_old_queue_job_to_new_engine(self):
        m=self.module()
        from dota2_map_assistant.sqlite_cache import SQLiteTranslationCache
        from dota2_map_assistant.translation import TranslationQueue
        from dota2_map_assistant.translator_models import TextObservation
        ready,release=threading.Event(),threading.Event()
        class Engine:
            calls=0
            def translate_message(self,*args,**kwargs): self.calls+=1; return '旧消息'
        with tempfile.TemporaryDirectory() as tmp:
            cache=SQLiteTranslationCache(Path(tmp)/'cache.sqlite3')
            old_engine,new_engine=Engine(),Engine()
            old=m.LocalTranslationProvider(old_engine,cache,auto_history=True)
            new=m.LocalTranslationProvider(new_engine,cache,auto_history=True)
            q=TranslationQueue(old,cache,start_worker=False)
            def budget(_chars): ready.set(); release.wait(2); return True
            q._wait_for_budget=budget
            q.submit(TextObservation('предыдущий разговор','rus',None,1))
            worker=threading.Thread(target=q._run); worker.start()
            try:
                self.assertTrue(ready.wait(1))
                q.clear_memory(); q.set_provider(new); release.set()
                q._jobs.put(None); worker.join(2)
                self.assertEqual(new_engine.calls,0)
                self.assertFalse(new._history)
            finally:
                release.set(); q.stop(); worker.join(2)

    def test_large_default_is_blocked_before_download_on_low_memory_machine(self):
        m=self.module()
        from types import SimpleNamespace
        e=m.LocalLLMEngine()
        with patch('psutil.virtual_memory',return_value=SimpleNamespace(total=16*2**30,available=12*2**30)), \
             patch('dota2_map_assistant.local_model_install.install') as download:
            with self.assertRaisesRegex(RuntimeError,'32 GB'):
                e.prepare_model()
            download.assert_not_called()
        e.close()

    def test_hymt_translates_original_whole_sentence_in_one_pass(self):
        m=self.module()
        e=m.LocalLLMEngine()
        with patch.object(e,'_load'),patch.object(e,'_complete',return_value='你是傻逼，不是辅助。') as complete:
            self.assertEqual(e.translate_message('ты долбоёб, а не саппорт','ru','zh'), '你是傻逼，不是辅助。')
            complete.assert_called_once()
            self.assertIn('ты долбоёб, а не саппорт',complete.call_args.args[0][0]['content'])
            self.assertEqual(complete.call_args.args[0][0]['role'],'user')
        e.close()

    def test_allowed_english_direction_does_not_index_russian_only_glossary(self):
        m=self.module(); e=m.LocalLLMEngine()
        with patch.object(e,'_load'),patch.object(e,'_complete',return_value='先等大哥刷钱。') as complete:
            self.assertEqual(e.translate_message('Wait for the carry to farm.','en','zh'),'先等大哥刷钱。')
            complete.assert_called_once()
        e.close()

    def test_hymt_grammar_requires_target_swear_without_an_appended_placeholder(self):
        m=self.module()
        grammar=m.profanity_grammar({'[[DOTA_WORD_0]]':'傻逼'},canonical=True)
        self.assertIn('"傻逼"',grammar)
        self.assertNotIn('DOTA_WORD',grammar)
        e=m.LocalLLMEngine()
        with patch.object(e,'_load'),patch.object(e,'_complete',return_value='你是傻逼傻逼。'):
            with self.assertRaisesRegex(ValueError,'粗话'):
                e.translate_message('ты долбоёб, а не саппорт','ru','zh')
        e.close()

if __name__=='__main__': unittest.main()
