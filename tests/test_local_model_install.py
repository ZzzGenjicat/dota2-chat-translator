import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from dota2_map_assistant import local_model_install as m

class ModelInstallTests(unittest.TestCase):
    def fixture(self):
        data=b'verified model fixture'
        return data,{'test.gguf':(len(data),hashlib.sha256(data).hexdigest())}

    def test_same_size_without_verification_receipt_is_not_ready(self):
        data,files=self.fixture()
        with tempfile.TemporaryDirectory() as tmp, patch.object(m,'FILES',files):
            p=Path(tmp); (p/'test.gguf').write_bytes(b'x'*len(data))
            self.assertFalse(m.is_installed(p))

    def test_verified_receipt_avoids_rehash_but_detects_changed_file(self):
        data,files=self.fixture()
        with tempfile.TemporaryDirectory() as tmp, patch.object(m,'FILES',files):
            p=Path(tmp); model=p/'test.gguf'; model.write_bytes(data)
            with patch('urllib.request.OpenerDirector.open',side_effect=AssertionError('network')):
                m.install(p,verify_only=True)
            with patch.object(m,'checksum',side_effect=AssertionError('must not rehash on each startup')):
                self.assertTrue(m.is_installed(p))
                model.write_bytes(b'x'*len(data))
                self.assertFalse(m.is_installed(p))

    def test_complete_valid_partial_is_promoted_without_network(self):
        data,files=self.fixture(); size,digest=files['test.gguf']
        with tempfile.TemporaryDirectory() as tmp:
            target=Path(tmp)/'test.gguf'; target.with_suffix('.gguf.part').write_bytes(data)
            with patch('urllib.request.OpenerDirector.open',side_effect=AssertionError('network')):
                m.download_verified('https://example.invalid/test.gguf',target,size,digest)
            self.assertEqual(target.read_bytes(),data)

    def test_invalid_partial_never_replaces_existing_model(self):
        data,files=self.fixture(); size,digest=files['test.gguf']
        with tempfile.TemporaryDirectory() as tmp:
            target=Path(tmp)/'test.gguf'; target.write_bytes(b'old')
            target.with_suffix('.gguf.part').write_bytes(b'x'*size)
            with self.assertRaisesRegex(RuntimeError,'校验'):
                m.download_verified('https://example.invalid/test.gguf',target,size,digest)
            self.assertEqual(target.read_bytes(),b'old')

    def test_bundled_cache_namespace_survives_first_model_install(self):
        from dota2_map_assistant.llm_translation import cache_path,MODEL_FOLDER,MODEL_FILENAME
        from dota2_map_assistant.chat_lexicon import ChatLexicon
        with tempfile.TemporaryDirectory() as tmp:
            folder=Path(tmp)/'models'/MODEL_FOLDER
            lex=ChatLexicon(); before=cache_path(folder,lex)
            folder.mkdir(parents=True); (folder/MODEL_FILENAME).write_bytes(b'fixture')
            self.assertEqual(before,cache_path(folder,lex))

    def test_model_download_uses_bundled_ca_roots_and_verifies_https(self):
        import ssl
        import urllib.request
        opener=m.download_opener()
        handler=next(x for x in opener.handlers if isinstance(x,urllib.request.HTTPSHandler))
        context=handler._context
        self.assertEqual(context.verify_mode,ssl.CERT_REQUIRED)
        self.assertTrue(context.check_hostname)
        self.assertGreater(context.cert_store_stats()['x509_ca'],0)

    def test_same_size_rewrite_with_coarse_unchanged_timestamp_is_detected(self):
        import os
        data,files=self.fixture()
        with tempfile.TemporaryDirectory() as tmp,patch.object(m,'FILES',files):
            p=Path(tmp); model=p/'test.gguf'; model.write_bytes(data)
            m.install(p,verify_only=True)
            stat=model.stat(); model.write_bytes(b'x'*len(data))
            os.utime(model,ns=(stat.st_atime_ns,stat.st_mtime_ns))
            self.assertFalse(m.is_installed(p))

if __name__=='__main__': unittest.main()
