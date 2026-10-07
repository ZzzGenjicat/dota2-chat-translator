import base64
import hashlib
import io
import json
import os
import queue
import random
import threading
import time
import sys
import tempfile
import unittest
from pathlib import Path
from urllib.parse import parse_qs, urlparse
from urllib import request
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from dota2_map_assistant.chatgpt_plan import (
    ChatGPTAuth,
    ChatGPTPlanProvider,
    CredentialStore,
    OAuthAttempt,
    parse_response_stream,
    validate_id_token,
    validate_callback,
    _json_request,
)


class ChatGPTPlanTests(unittest.TestCase):
    def setUp(self):
        # Local callback fixtures use a numeric loopback address. Hostname
        # resolution is unrelated to OAuth and can stall on hosted Mac runners.
        resolver = patch("http.server.socket.getfqdn", side_effect=lambda host: host)
        resolver.start()
        self.addCleanup(resolver.stop)

    def test_oauth_https_request_has_trusted_root_certificates(self):
        class Response:
            def __enter__(self): return self
            def __exit__(self, *_args): pass
            def read(self, *_args): return b'{"ok": true}'

        def opener(req, **kwargs):
            self.assertEqual(req.full_url, "https://auth.openai.com/api/accounts/oauth/token")
            self.assertGreater(len(kwargs["context"].get_ca_certs()), 0)
            return Response()

        self.assertEqual(_json_request("https://auth.openai.com/api/accounts/oauth/token",
                                       data={"grant_type": "authorization_code"}, form=True,
                                       opener=opener), {"ok": True})

    def test_id_token_validates_rsa_signature_and_rejects_tampering(self):
        rng = random.Random(127)

        def probable_prime(bits):
            while True:
                value = rng.getrandbits(bits) | (1 << (bits - 1)) | 1
                if any(value % divisor == 0 for divisor in (3, 5, 7, 11, 13, 17, 19, 23, 29, 31)):
                    continue
                rest = value - 1
                power = 0
                while rest % 2 == 0:
                    rest //= 2
                    power += 1
                for _ in range(16):
                    base = rng.randrange(2, value - 1)
                    witness = pow(base, rest, value)
                    if witness == 1 or witness == value - 1:
                        continue
                    for _ in range(power - 1):
                        witness = pow(witness, 2, value)
                        if witness == value - 1:
                            break
                    else:
                        break
                else:
                    return value

        p, q = probable_prime(1024), probable_prime(1024)
        modulus = p * q
        while modulus.bit_length() < 2048:
            q = probable_prime(1024)
            modulus = p * q
        exponent = 65537
        private = pow(exponent, -1, (p - 1) * (q - 1))
        encode = lambda data: base64.urlsafe_b64encode(data).rstrip(b"=").decode()
        header = encode(json.dumps({"alg": "RS256", "kid": "test"}).encode())
        claims = encode(json.dumps({"iss": "https://auth.openai.com", "aud": "oaiapp_123", "sub": "player",
                                     "nonce": "random-nonce", "exp": time.time() + 3600}).encode())
        content = (header + "." + claims).encode()
        digest_info = bytes.fromhex("3031300d060960864801650304020105000420") + hashlib.sha256(content).digest()
        size = (modulus.bit_length() + 7) // 8
        padded = b"\x00\x01" + b"\xff" * (size - len(digest_info) - 3) + b"\x00" + digest_info
        signature = pow(int.from_bytes(padded, "big"), private, modulus).to_bytes(size, "big")
        token = header + "." + claims + "." + encode(signature)

        def fetch(url):
            if url.endswith("openid-configuration"):
                return {"issuer": "https://auth.openai.com", "jwks_uri": "https://auth.openai.com/keys"}
            return {"keys": [{"kid": "test", "kty": "RSA", "alg": "RS256",
                              "n": encode(modulus.to_bytes(size, "big")), "e": encode(b"\x01\x00\x01")}]}

        self.assertEqual(validate_id_token(token, "oaiapp_123", "random-nonce", fetch)["sub"], "player")
        with self.assertRaisesRegex(ValueError, "签名"):
            validate_id_token(token[:-2] + "ab", "oaiapp_123", "random-nonce", fetch)
        with self.assertRaisesRegex(ValueError, "nonce"):
            validate_id_token(token, "oaiapp_123", "different", fetch)

    @unittest.skipUnless(os.name == "nt", "Windows DPAPI")
    def test_credential_file_is_encrypted_and_round_trips(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = CredentialStore(Path(tmp) / "auth.dat")
            data = {"host_id": "urn:uuid:test", "accounts": {"one": {"refresh_token": "secret-token"}}, "active": "one"}
            store.save(data)
            self.assertNotIn(b"secret-token", store.path.read_bytes())
            self.assertEqual(store.load(), data)

    @unittest.skipUnless(os.name == "nt", "Windows DPAPI")
    def test_unreadable_credentials_do_not_prevent_app_start_and_are_preserved(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "auth.dat"
            path.write_bytes(b"encrypted-for-a-different-windows-session")
            store = CredentialStore(path)
            auth = ChatGPTAuth(store=store)
            self.assertIsNone(auth.active)
            self.assertTrue(store.recovery_needed)
            self.assertEqual(path.read_bytes(), b"encrypted-for-a-different-windows-session")
            store.save(auth.data)
            self.assertFalse(store.recovery_needed)
            self.assertEqual(len(list(path.parent.glob("auth.dat.unreadable-*"))), 1)
            self.assertEqual(store.load()["host_id"], auth.data["host_id"])

    @unittest.skipUnless(os.name == "nt", "Windows DPAPI")
    def test_unreadable_credentials_keep_the_same_host_id_after_restart(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "auth.dat"
            path.write_bytes(b"encrypted-for-a-different-windows-session")
            first = CredentialStore(path)
            first_host = first.load()["host_id"]
            second = CredentialStore(path)
            second_host = second.load()["host_id"]
            self.assertTrue(first.recovery_needed)
            self.assertTrue(second.recovery_needed)
            self.assertEqual(first_host, second_host)
            self.assertEqual(path.read_bytes(), b"encrypted-for-a-different-windows-session")

    @unittest.skipUnless(os.name == "nt", "Windows DPAPI")
    def test_unreadable_credentials_reuse_the_saved_client_registration(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "auth.dat"
            store = CredentialStore(path)
            store.save({"host_id": "", "active": "oaiapp_existing", "accounts": {
                "oaiapp_existing": {"client_id": "oaiapp_existing", "subject": "player",
                                    "access_token": "secret", "refresh_token": "secret-refresh"}
            }})
            client_path = path.with_name(path.name + ".client")
            self.assertEqual(client_path.read_text(encoding="ascii"), "oaiapp_existing")
            self.assertNotIn("secret", client_path.read_text(encoding="ascii"))

            path.write_bytes(b"encrypted-for-a-different-windows-session")
            recovered = ChatGPTAuth(store=CredentialStore(path))
            self.assertIsNone(recovered.active)
            self.assertEqual(recovered.registration["client_id"], "oaiapp_existing")
            self.assertTrue(recovered.store.recovery_needed)

    def test_reauthorization_with_recovered_client_does_not_register_new_app(self):
        class MemoryStore:
            def __init__(self):
                self.data = {"host_id": "urn:uuid:test", "active": "oaiapp_existing", "accounts": {
                    "oaiapp_existing": {"client_id": "oaiapp_existing"}
                }}
            def load(self):
                return self.data
            def save(self, data): self.data = data

        class Response:
            def __enter__(self): return self
            def __exit__(self, *_args): pass
            def read(self, *_args):
                return json.dumps({"scope": "openid chatgpt.tokens.use.direct", "id_token": "jwt",
                                   "access_token": "access", "refresh_token": "refresh", "expires_in": 3600}).encode()

        opened = []
        callbacks = []
        auth = ChatGPTAuth(store=MemoryStore(), opener=lambda _req, **_kwargs: Response())
        def browser(url):
            opened.append(url)
            params = parse_qs(urlparse(url).query)
            callback = params["redirect_uri"][0] + "?code=one-time&state=" + params["state"][0]
            thread = threading.Thread(target=lambda: request.urlopen(callback, timeout=3).read())
            thread.start()
            callbacks.append(thread)

        with patch("dota2_map_assistant.chatgpt_plan.validate_id_token",
                   return_value={"sub": "player", "email": "p@example.com"}):
            auth.begin_login(browser=browser)
            auth._login_thread.join(timeout=5)
            for thread in callbacks:
                thread.join(timeout=3)
        url = opened[0]
        params = parse_qs(urlparse(url).query)
        self.assertEqual(params["client_id"], ["oaiapp_existing"])
        self.assertNotIn("agent_name_hint", params)
        self.assertEqual(auth.registration["client_id"], "oaiapp_existing")
        self.assertIsNotNone(auth.active)

    def test_expired_access_token_refreshes_and_saves_rotated_token(self):
        class MemoryStore:
            def __init__(self):
                self.data = {"host_id": "urn:uuid:test", "active": "oaiapp_one", "accounts": {
                    "oaiapp_one": {"client_id": "oaiapp_one", "access_token": "old",
                                   "refresh_token": "old-refresh", "expires_at": 1}
                }}
            def load(self): return self.data
            def save(self, data): self.data = data.copy()

        class Response:
            def __enter__(self): return self
            def __exit__(self, *_args): pass
            def read(self, *_args):
                return b'{"access_token":"new","refresh_token":"new-refresh","expires_in":3600}'

        sent = []
        def opener(req, **_kwargs):
            sent.append(req)
            return Response()

        store = MemoryStore()
        auth = ChatGPTAuth(store=store, opener=opener)
        self.assertEqual(auth.access_token(), "new")
        self.assertIn(b"grant_type=refresh_token", sent[0].data)
        self.assertIn(b"refresh_token=old-refresh", sent[0].data)
        self.assertEqual(store.data["accounts"]["oaiapp_one"]["refresh_token"], "new-refresh")

    def test_signed_out_registration_is_reusable_but_not_active(self):
        class MemoryStore:
            def load(self):
                return {"host_id": "urn:uuid:test", "active": "oaiapp_one", "accounts": {
                    "oaiapp_one": {"client_id": "oaiapp_one", "email": "player@example.com"}
                }}
            def save(self, _data): pass

        auth = ChatGPTAuth(store=MemoryStore())
        self.assertIsNone(auth.active)
        self.assertEqual(auth.registration["client_id"], "oaiapp_one")

    def test_loopback_callback_exchanges_code_after_browser_redirect(self):
        class MemoryStore:
            def __init__(self): self.data = {"host_id": "urn:uuid:test", "accounts": {}, "active": ""}
            def load(self): return self.data
            def save(self, data): self.data = data

        class Response:
            def __enter__(self): return self
            def __exit__(self, *_args): pass
            def read(self, *_args):
                return json.dumps({"scope": "openid chatgpt.tokens.use.direct", "id_token": "jwt",
                                   "access_token": "access", "refresh_token": "refresh", "expires_in": 3600}).encode()

        sent = []
        def opener(req, **_kwargs):
            sent.append(req)
            return Response()

        browser_threads = []
        callback_pages = []
        def browser(url):
            params = parse_qs(urlparse(url).query)
            callback = params["redirect_uri"][0] + "?" + (
                "code=one-time&state=" + params["state"][0] + "&client_id=oaiapp_test"
            )
            thread = threading.Thread(target=lambda: callback_pages.append(request.urlopen(callback, timeout=4).read().decode("utf-8")))
            thread.start()
            browser_threads.append(thread)

        store = MemoryStore()
        auth = ChatGPTAuth(store=store, opener=opener)
        with patch("dota2_map_assistant.chatgpt_plan.validate_id_token", return_value={"sub": "player", "email": "p@example.com"}):
            auth.begin_login(browser=browser)
            auth._login_thread.join(timeout=5)
            for thread in browser_threads:
                thread.join(timeout=5)
        self.assertFalse(auth._login_thread.is_alive())
        self.assertEqual(store.data["active"], "oaiapp_test")
        self.assertEqual(auth.email, "p@example.com")
        self.assertIn(b"code_verifier=", sent[0].data)
        self.assertIn(b"client_id=oaiapp_test", sent[0].data)
        self.assertEqual(len(callback_pages), 1)
        self.assertIn("请返回", callback_pages[0])
        self.assertNotIn("授权已完成", callback_pages[0])

    def test_authorization_uses_loopback_pkce_and_plan_scope(self):
        attempt = OAuthAttempt.new("urn:uuid:example", "http://127.0.0.1:12345/auth/callback")
        parsed = urlparse(attempt.authorize_url())
        query = parse_qs(parsed.query)
        self.assertEqual(parsed.netloc, "auth.openai.com")
        self.assertEqual(query["client_id"], ["dynamic_agent_client"])
        self.assertEqual(query["redirect_uri"], [attempt.redirect_uri])
        self.assertIn("chatgpt.tokens.use.direct", query["scope"][0])
        self.assertEqual(query["resource"], ["https://api.openai.com/v1"])
        self.assertEqual(query["code_challenge_method"], ["S256"])
        expected = base64.urlsafe_b64encode(hashlib.sha256(attempt.verifier.encode()).digest()).rstrip(b"=").decode()
        self.assertEqual(query["code_challenge"], [expected])

    def test_login_can_restart_after_browser_authorization_page_fails(self):
        class MemoryStore:
            def load(self):
                return {"host_id": "urn:uuid:test", "accounts": {}, "active": ""}
            def save(self, _data):
                pass

        opened = queue.Queue()
        auth = ChatGPTAuth(store=MemoryStore())
        first_url = second_url = None
        try:
            auth.begin_login(browser=opened.put)
            first_url = opened.get(timeout=3)
            # A browser-side 503 never calls the local callback. Clicking login
            # again must open a fresh attempt without a ten-minute wait.
            auth.begin_login(browser=opened.put)
            second_url = opened.get(timeout=3)
            first = parse_qs(urlparse(first_url).query)
            second = parse_qs(urlparse(second_url).query)
            self.assertNotEqual(first["state"], second["state"])
            self.assertEqual(first["ext_agent_host_id"], second["ext_agent_host_id"])
        finally:
            for url in (first_url, second_url):
                if not url:
                    continue
                params = parse_qs(urlparse(url).query)
                callback = params["redirect_uri"][0] + "?error=access_denied&state=" + params["state"][0]
                try:
                    request.urlopen(callback, timeout=2).read()
                except OSError:
                    pass
            if auth._login_thread:
                auth._login_thread.join(timeout=3)

    def test_callback_rejects_wrong_state_and_client_id(self):
        attempt = OAuthAttempt.new("urn:uuid:example", "http://127.0.0.1:12345/auth/callback", client_id="oaiapp_good")
        with self.assertRaisesRegex(ValueError, "state"):
            validate_callback(attempt, {"state": "wrong", "code": "abc"})
        with self.assertRaisesRegex(ValueError, "client"):
            validate_callback(attempt, {"state": attempt.state, "code": "abc", "client_id": "oaiapp_other"})

    def test_callback_requires_issued_client_for_first_registration(self):
        attempt = OAuthAttempt.new("urn:uuid:example", "http://127.0.0.1:12345/auth/callback")
        with self.assertRaisesRegex(ValueError, "client"):
            validate_callback(attempt, {"state": attempt.state, "code": "abc"})
        self.assertEqual(validate_callback(attempt, {"state": attempt.state, "code": "abc", "client_id": "oaiapp_123"}), ("abc", "oaiapp_123"))

    def test_stream_requires_completed_and_returns_deltas(self):
        payload = (
            'event: response.output_text.delta\n'
            'data: {"type":"response.output_text.delta","delta":"左边"}\n\n'
            'data: {"type":"response.output_text.delta","delta":"好打"}\n\n'
            'data: {"type":"response.completed","response":{}}\n\n'
        )
        self.assertEqual(parse_response_stream(io.BytesIO(payload.encode())), "左边好打")
        with self.assertRaisesRegex(RuntimeError, "completed"):
            parse_response_stream(io.BytesIO(payload.split('data: {"type":"response.completed"')[0].encode()))

    def test_stream_rejects_failed_event_even_after_text(self):
        payload = (
            'data: {"type":"response.output_text.delta","delta":"partial"}\n\n'
            'data: {"type":"response.failed","response":{"error":{"code":"subscription_sharing_usage_limit_exceeded"}}}\n\n'
        )
        with self.assertRaisesRegex(RuntimeError, "subscription_sharing_usage_limit_exceeded"):
            parse_response_stream(io.BytesIO(payload.encode()))

    def test_provider_uses_supported_responses_shape_and_only_chat_body(self):
        requests = []

        class Response:
            def __enter__(self): return self
            def __exit__(self, *_args): pass
            def __iter__(self):
                return iter(['data: {"type":"response.output_text.delta","delta":"左边有好打的目标"}\n'.encode(),
                             b'data: {"type":"response.completed","response":{}}\n'])

        def opener(req, **kwargs):
            requests.append((req, kwargs))
            return Response()

        provider = ChatGPTPlanProvider(lambda: "access_token", "gpt-model", opener=opener)
        self.assertEqual(provider.translate("слева мясо", "rus"), "左边有好打的目标")
        req, _kwargs = requests[0]
        self.assertEqual(req.full_url, "https://api.openai.com/v1/responses")
        self.assertEqual(req.get_header("Authorization"), "Bearer access_token")
        body = json.loads(req.data)
        self.assertEqual(body["input"], [{"role": "user", "content": "слева мясо"}])
        self.assertFalse(body["store"])
        self.assertTrue(body["stream"])
        self.assertIn("Dota 2", body["instructions"])

    def test_outgoing_translation_uses_literal_russian_rules_without_incoming_prompt(self):
        requests = []

        class Response:
            def __enter__(self): return self
            def __exit__(self, *_args): pass
            def __iter__(self):
                return iter([b'data: {"type":"response.output_text.delta","delta":"\xd0\x98\xd0\xb4\xd0\xb8 \xd0\xbd\xd0\xb0 \xd0\xbc\xd0\xb8\xd0\xb4"}\n',
                             b'data: {"type":"response.completed","response":{}}\n'])

        def opener(req, **_kwargs):
            requests.append(req)
            return Response()

        provider = ChatGPTPlanProvider(lambda: "access_token", "gpt-model", "INCOMING ONLY", opener=opener)
        self.assertEqual(provider.translate_outgoing("去中路"), "Иди на мид")
        payload = json.loads(requests[0].data)
        self.assertEqual(payload["input"], [{"role": "user", "content": "去中路"}])
        self.assertIn("俄语", payload["instructions"])
        self.assertIn("粗话", payload["instructions"])
        self.assertNotIn("INCOMING ONLY", payload["instructions"])

    def test_provider_reports_chatgpt_error_code_from_failed_http_response(self):
        from urllib.error import HTTPError

        def opener(req, **_kwargs):
            body = io.BytesIO(b'{"error":{"code":"subscription_sharing_user_not_eligible",'
                              b'"message":"Plan access unavailable"}}')
            raise HTTPError(req.full_url, 403, "Forbidden", {}, body)

        provider = ChatGPTPlanProvider(lambda: "access_token", "gpt-model", opener=opener)
        with self.assertRaisesRegex(RuntimeError, "subscription_sharing_user_not_eligible"):
            provider.translate("иди мид", "rus")


if __name__ == "__main__":
    unittest.main()
