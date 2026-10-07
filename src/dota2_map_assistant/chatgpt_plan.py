"""Official Sign in with ChatGPT flow for a local Windows translator.

OAuth credentials stay in a DPAPI-protected, gitignored local file.
Only OpenAI's public OAuth and Responses endpoints are used.
"""

from __future__ import annotations

import base64
import ctypes
import hashlib
import hmac
import json
import os
import queue
import secrets
import ssl
import subprocess
import threading
import time
import uuid
from ctypes import wintypes
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Any, Callable
from urllib import request
from urllib.error import HTTPError
from urllib.parse import parse_qs, urlencode, urlparse


AUTH_BASE = "https://auth.openai.com"
AUTHORIZE_URL = AUTH_BASE + "/api/accounts/authorize"
TOKEN_URL = AUTH_BASE + "/api/accounts/oauth/token"
DISCOVERY_URL = AUTH_BASE + "/.well-known/openid-configuration"
RESOURCE = "https://api.openai.com/v1"
SCOPE = "openid profile email offline_access resource.invoke chatgpt.tokens.use.direct"
RESPONSES_URL = RESOURCE + "/responses"
MODELS_URL = RESOURCE + "/models"
DEFAULT_INSTRUCTIONS = (
    "你是 Dota 2 对局聊天翻译员。只把玩家的俄语聊天正文译成自然、准确的简体中文，只输出译文。"
    "结合游戏语境理解英雄、位置、经济、口语及粗话，保留专有名词和数字。"
    "不解释、不添加原文没有的信息；含糊或无法辨认之处保留原文。"
)
OUTGOING_INSTRUCTIONS = (
    "你是 Dota 2 对局发言翻译员。只把用户输入的中文译成自然、准确的俄语，只输出可直接发送的俄语译文。"
    "尽量直译，保留原话的意思、语气、粗话、辱骂和攻击性表达，不弱化、不美化、不额外添加内容。"
    "游戏术语按玩家常用说法翻译；专有名词和数字保持准确。"
)


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _unb64url(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


def _verified_context() -> ssl.SSLContext:
    context = ssl.create_default_context()
    if not context.get_ca_certs():
        # Inkscape's bundled MinGW Python has no Windows root store and its
        # default OpenSSL CA path is empty. Its certifi bundle is available.
        try:
            import certifi
        except ImportError as exc:
            raise RuntimeError("当前 Python 缺少 HTTPS 根证书，请安装 certifi") from exc
        context.load_verify_locations(cafile=certifi.where())
    if not context.get_ca_certs():
        raise RuntimeError("当前 Python 没有可用的 HTTPS 根证书")
    return context


@dataclass(frozen=True)
class OAuthAttempt:
    host_id: str
    redirect_uri: str
    state: str
    nonce: str
    verifier: str
    client_id: str = "dynamic_agent_client"
    id_token_hint: str = ""
    login_hint: str = ""

    @classmethod
    def new(cls, host_id: str, redirect_uri: str, client_id: str = "dynamic_agent_client",
            id_token_hint: str = "", login_hint: str = "") -> "OAuthAttempt":
        return cls(host_id, redirect_uri, secrets.token_urlsafe(32), secrets.token_urlsafe(32),
                   secrets.token_urlsafe(64), client_id, id_token_hint, login_hint)

    def authorize_url(self) -> str:
        parameters = {
            "client_id": self.client_id,
            "ext_agent_host_id": self.host_id,
            "response_type": "code",
            "redirect_uri": self.redirect_uri,
            "scope": SCOPE,
            "resource": RESOURCE,
            "state": self.state,
            "nonce": self.nonce,
            "code_challenge_method": "S256",
            "code_challenge": _b64url(hashlib.sha256(self.verifier.encode("ascii")).digest()),
        }
        if self.client_id == "dynamic_agent_client":
            parameters["agent_name_hint"] = "Dota 2 Chat Translator"
        else:
            if self.id_token_hint:
                parameters["id_token_hint"] = self.id_token_hint
            if self.login_hint:
                parameters["login_hint"] = self.login_hint
        return AUTHORIZE_URL + "?" + urlencode(parameters)


def validate_callback(attempt: OAuthAttempt, values: dict[str, str]) -> tuple[str, str]:
    if not hmac.compare_digest(values.get("state", ""), attempt.state):
        raise ValueError("OAuth state 不匹配")
    if values.get("error"):
        raise ValueError("登录授权被取消或拒绝：" + values["error"])
    code = values.get("code", "")
    if not code:
        raise ValueError("登录回调缺少授权码")
    callback_client = values.get("client_id", "")
    if attempt.client_id == "dynamic_agent_client":
        if not callback_client.startswith("oaiapp_"):
            raise ValueError("登录回调缺少有效的 client ID")
        return code, callback_client
    if callback_client and callback_client != attempt.client_id:
        raise ValueError("登录回调的 client ID 不匹配")
    return code, attempt.client_id


def _json_request(url: str, *, data: dict | None = None, token: str = "",
                  form: bool = False, opener: Callable[..., Any] = request.urlopen) -> dict:
    body = None
    headers = {"Accept": "application/json"}
    if data is not None:
        body = urlencode(data).encode("utf-8") if form else json.dumps(data, ensure_ascii=False).encode("utf-8")
        headers["Content-Type"] = "application/x-www-form-urlencoded" if form else "application/json"
    if token:
        headers["Authorization"] = "Bearer " + token
    req = request.Request(url, data=body, headers=headers, method="POST" if data is not None else "GET")
    with opener(req, timeout=15, context=_verified_context()) as response:
        result = json.load(response)
    if not isinstance(result, dict):
        raise ValueError("服务返回格式错误")
    return result


def validate_id_token(token: str, client_id: str, nonce: str,
                      fetch_json: Callable[[str], dict] = _json_request) -> dict:
    """Verify RS256 signature and the OIDC claims before accepting credentials."""
    try:
        header_text, claims_text, signature_text = token.split(".")
        header = json.loads(_unb64url(header_text))
        claims = json.loads(_unb64url(claims_text))
        signature = _unb64url(signature_text)
        if header.get("alg") != "RS256" or not header.get("kid"):
            raise ValueError("不支持的 ID token 签名")
        discovery = fetch_json(DISCOVERY_URL)
        if discovery.get("issuer") != AUTH_BASE:
            raise ValueError("OIDC 发行方无效")
        jwks_uri = discovery.get("jwks_uri", "")
        if not isinstance(jwks_uri, str) or not jwks_uri.startswith(AUTH_BASE + "/"):
            raise ValueError("OIDC 公钥地址无效")
        keys = fetch_json(jwks_uri).get("keys", [])
        key = next((item for item in keys if item.get("kid") == header["kid"] and item.get("kty") == "RSA"), None)
        if not key or key.get("alg", "RS256") != "RS256":
            raise ValueError("找不到 ID token 签名公钥")
        modulus = int.from_bytes(_unb64url(key["n"]), "big")
        exponent = int.from_bytes(_unb64url(key["e"]), "big")
        if modulus.bit_length() < 2048 or exponent < 3 or exponent % 2 == 0:
            raise ValueError("ID token 公钥无效")
        size = (modulus.bit_length() + 7) // 8
        if len(signature) != size:
            raise ValueError("ID token 签名无效")
        encoded = pow(int.from_bytes(signature, "big"), exponent, modulus).to_bytes(size, "big")
        digest = hashlib.sha256((header_text + "." + claims_text).encode("ascii")).digest()
        digest_info = bytes.fromhex("3031300d060960864801650304020105000420") + digest
        expected = b"\x00\x01" + b"\xff" * (size - len(digest_info) - 3) + b"\x00" + digest_info
        if not hmac.compare_digest(encoded, expected):
            raise ValueError("ID token 签名无效")
        audience = claims.get("aud")
        if claims.get("iss") != AUTH_BASE or (audience != client_id and (not isinstance(audience, list) or client_id not in audience)):
            raise ValueError("ID token 发行方或受众无效")
        if claims.get("nonce") != nonce or float(claims.get("exp", 0)) <= time.time():
            raise ValueError("ID token 已过期或 nonce 无效")
        if not isinstance(claims.get("sub"), str) or not claims["sub"]:
            raise ValueError("ID token 缺少用户身份")
        return claims
    except (KeyError, TypeError, IndexError, UnicodeError, json.JSONDecodeError, OverflowError) as exc:
        raise ValueError("ID token 无效") from exc


class _Blob(ctypes.Structure):
    _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_byte))]


def _dpapi(data: bytes, *, protect: bool) -> bytes:
    if os.name != "nt":
        raise RuntimeError("ChatGPT 登录凭据需要 Windows DPAPI")
    source_buffer = ctypes.create_string_buffer(data)
    source = _Blob(len(data), ctypes.cast(source_buffer, ctypes.POINTER(ctypes.c_byte)))
    result = _Blob()
    crypt32 = ctypes.WinDLL("crypt32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    name = "CryptProtectData" if protect else "CryptUnprotectData"
    operation = getattr(crypt32, name)
    operation.argtypes = [ctypes.POINTER(_Blob), ctypes.c_void_p, ctypes.c_void_p,
                          ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(_Blob)]
    operation.restype = wintypes.BOOL
    if not operation(ctypes.byref(source), None, None, None, None, 0, ctypes.byref(result)):
        raise OSError(ctypes.get_last_error(), "Windows 无法保护登录凭据")
    try:
        return ctypes.string_at(result.pbData, result.cbData)
    finally:
        kernel32.LocalFree(ctypes.cast(result.pbData, ctypes.c_void_p))


class CredentialStore:
    def __init__(self, path: Path | None = None) -> None:
        self.path = path or Path(__file__).resolve().parents[2] / "config" / "chatgpt_auth.dat"
        self.recovery_needed = False

    def _stable_host_id(self, preferred: str = "") -> str:
        # The host ID is public metadata, so keep it apart from DPAPI tokens.
        # A Windows logon change must not generate a new host on every launch.
        host_path = self.path.with_name(self.path.name + ".host")
        try:
            saved = host_path.read_text(encoding="ascii").strip()
            if saved.startswith("urn:uuid:") and uuid.UUID(saved[9:]).version == 4:
                return saved
        except (FileNotFoundError, ValueError, UnicodeError):
            pass
        try:
            valid_preferred = preferred.startswith("urn:uuid:") and uuid.UUID(preferred[9:]).version == 4
        except (ValueError, AttributeError):
            valid_preferred = False
        host_id = preferred if valid_preferred else "urn:uuid:" + str(uuid.uuid4())
        host_path.parent.mkdir(parents=True, exist_ok=True)
        host_path.write_text(host_id, encoding="ascii")
        return host_id

    def _client_path(self) -> Path:
        return self.path.with_name(self.path.name + ".client")

    def _saved_client_id(self) -> str:
        try:
            client_id = self._client_path().read_text(encoding="ascii").strip()
        except (OSError, UnicodeError):
            return ""
        if client_id.startswith("oaiapp_") and len(client_id) <= 255 and all(
            char.isascii() and (char.isalnum() or char in "_-" ) for char in client_id
        ):
            return client_id
        return ""

    def _remember_client_id(self, data: dict) -> None:
        active = data.get("active", "")
        record = data.get("accounts", {}).get(active, {})
        client_id = record.get("client_id", "") if isinstance(record, dict) else ""
        if not isinstance(client_id, str) or not client_id.startswith("oaiapp_"):
            return
        try:
            path = self._client_path()
            temporary = path.with_name(path.name + ".tmp")
            temporary.write_text(client_id, encoding="ascii")
            temporary.replace(path)
        except (OSError, UnicodeError):
            pass  # The encrypted credentials remain the authoritative record.

    def load(self) -> dict:
        if not self.path.exists():
            return {"host_id": self._stable_host_id(), "accounts": {}, "active": ""}
        try:
            data = json.loads(_dpapi(self.path.read_bytes(), protect=False))
        except (OSError, ValueError, UnicodeError, json.JSONDecodeError):
            # DPAPI can reject a file written from a different Windows logon
            # context. Keep that file recoverable and let the app start offline.
            self.recovery_needed = True
            client_id = self._saved_client_id()
            return {"host_id": self._stable_host_id(),
                    "accounts": {client_id: {"client_id": client_id}} if client_id else {},
                    "active": client_id}
        self.recovery_needed = False
        data["host_id"] = self._stable_host_id(str(data.get("host_id", "")))
        self._remember_client_id(data)
        return data

    def save(self, data: dict) -> None:
        data["host_id"] = self._stable_host_id(str(data.get("host_id", "")))
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(".tmp")
        temporary.write_bytes(_dpapi(json.dumps(data, ensure_ascii=False).encode("utf-8"), protect=True))
        if self.recovery_needed and self.path.exists():
            backup = self.path.with_name(self.path.name + ".unreadable-" + uuid.uuid4().hex)
            self.path.replace(backup)
        temporary.replace(self.path)
        self.recovery_needed = False
        self._remember_client_id(data)


def find_chrome() -> str:
    if os.name == "nt":
        try:
            import winreg
            for hive in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
                try:
                    with winreg.OpenKey(hive, r"Software\Microsoft\Windows\CurrentVersion\App Paths\chrome.exe") as key:
                        found, _ = winreg.QueryValueEx(key, "")
                        if Path(found).is_file():
                            return found
                except OSError:
                    pass
        except ImportError:
            pass
    for base in (os.environ.get("PROGRAMFILES", ""), os.environ.get("PROGRAMFILES(X86)", ""),
                 os.environ.get("LOCALAPPDATA", "")):
        if base:
            candidate = Path(base) / "Google" / "Chrome" / "Application" / "chrome.exe"
            if candidate.is_file():
                return str(candidate)
    raise FileNotFoundError("未找到 Chrome；请先安装 Chrome 再登录")


def open_in_chrome(url: str) -> None:
    subprocess.Popen([find_chrome(), url], close_fds=True)


class ChatGPTAuth:
    def __init__(self, store: CredentialStore | None = None, opener: Callable[..., Any] = request.urlopen) -> None:
        self.store = store or CredentialStore()
        self.data = self.store.load()
        if isinstance(self.store, CredentialStore) and not self.store.path.exists():
            self.store.save(self.data)
        self.opener = opener
        self.events: queue.Queue[tuple[str, str]] = queue.Queue()
        self._lock = threading.RLock()
        self._login_thread: threading.Thread | None = None
        self._login_cancel = threading.Event()

    @property
    def registration(self) -> dict | None:
        return self.data.get("accounts", {}).get(self.data.get("active", ""))

    @property
    def active(self) -> dict | None:
        account = self.registration
        return account if account and account.get("access_token") else None

    @property
    def email(self) -> str:
        account = self.active
        return str(account.get("email") or account.get("subject")) if account else ""

    def poll_event(self) -> tuple[str, str] | None:
        try:
            return self.events.get_nowait()
        except queue.Empty:
            return None

    def begin_login(self, *, browser: Callable[[str], None] = open_in_chrome) -> None:
        if self._login_thread and self._login_thread.is_alive():
            self._login_cancel.set()
            self._login_thread.join(timeout=2)
            if self._login_thread.is_alive():
                raise RuntimeError("上一次登录正在完成，请稍候再试")
        self._login_cancel = threading.Event()
        self._login_thread = threading.Thread(target=self._login_worker, args=(browser, self._login_cancel), daemon=True,
                                              name="chatgpt-login")
        self._login_thread.start()

    def _login_worker(self, browser: Callable[[str], None], cancel: threading.Event) -> None:
        server = None
        try:
            received: queue.Queue[dict[str, str]] = queue.Queue(maxsize=1)
            class Callback(BaseHTTPRequestHandler):
                def do_GET(self):
                    parsed = urlparse(self.path)
                    if parsed.path != "/auth/callback":
                        self.send_error(404)
                        return
                    values = {key: values[0] for key, values in parse_qs(parsed.query).items()}
                    if not hmac.compare_digest(values.get("state", ""), attempt.state):
                        self.send_error(400, "Invalid state")
                        return
                    received.put_nowait(values)
                    body = "<html><meta charset='utf-8'><body><h2>已收到授权回跳</h2><p>请返回 Dota 2 聊天翻译器，查看连接是否成功。</p></body></html>".encode("utf-8")
                    self.send_response(200)
                    self.send_header("Content-Type", "text/html; charset=utf-8")
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)
                def log_message(self, *_args):
                    pass
            server = HTTPServer(("127.0.0.1", 0), Callback)
            server.timeout = 1
            account = self.registration
            redirect_uri = f"http://127.0.0.1:{server.server_port}/auth/callback"
            attempt = OAuthAttempt.new(
                self.data["host_id"], redirect_uri,
                client_id=account["client_id"] if account else "dynamic_agent_client",
                id_token_hint=account.get("id_token", "") if account else "",
                login_hint=account.get("email", "") if account else "",
            )
            browser(attempt.authorize_url())
            self.events.put(("pending", "已在 Chrome 打开登录页，等待授权…"))
            deadline = time.monotonic() + 600
            while received.empty() and time.monotonic() < deadline and not cancel.is_set():
                server.handle_request()
            if cancel.is_set():
                return
            if received.empty():
                raise TimeoutError("登录等待超时，请重新点击登录")
            code, issued_client = validate_callback(attempt, received.get_nowait())
            token = _json_request(TOKEN_URL, data={
                "grant_type": "authorization_code", "client_id": issued_client,
                "code": code, "code_verifier": attempt.verifier,
                "redirect_uri": redirect_uri, "resource": RESOURCE,
            }, form=True, opener=self.opener)
            scopes = str(token.get("scope", "")).split()
            if "chatgpt.tokens.use.direct" not in scopes:
                raise PermissionError("当前账号未授予 ChatGPT 方案翻译权限")
            claims = validate_id_token(str(token.get("id_token", "")), issued_client, attempt.nonce,
                                       fetch_json=lambda url: _json_request(url, opener=self.opener))
            if account and account.get("subject") and claims["sub"] != account["subject"]:
                raise ValueError("登录账号与原账号不一致")
            if not token.get("access_token") or not token.get("refresh_token"):
                raise ValueError("登录服务未返回完整凭据")
            record = {
                "client_id": issued_client, "subject": claims["sub"],
                "email": claims.get("email", ""), "id_token": token["id_token"],
                "access_token": token["access_token"], "refresh_token": token["refresh_token"],
                "expires_at": time.time() + int(token.get("expires_in", 3600)), "scopes": scopes,
            }
            with self._lock:
                self.data.setdefault("accounts", {})[issued_client] = record
                self.data["active"] = issued_client
                self.store.save(self.data)
            self.events.put(("success", f"已连接 ChatGPT：{self.email}"))
        except Exception as exc:
            if not cancel.is_set():
                self.events.put(("error", str(exc)))
        finally:
            if server is not None:
                server.server_close()

    def access_token(self) -> str:
        with self._lock:
            account = self.active
            if not account or not account.get("access_token"):
                raise RuntimeError("请先用 Chrome 登录 ChatGPT")
            if time.time() < float(account.get("expires_at", 0)) - 60:
                return account["access_token"]
            token = _json_request(TOKEN_URL, data={
                "grant_type": "refresh_token", "client_id": account["client_id"],
                "refresh_token": account["refresh_token"], "resource": RESOURCE,
            }, form=True, opener=self.opener)
            if not token.get("access_token") or not token.get("refresh_token"):
                raise RuntimeError("ChatGPT 登录已失效，请重新登录")
            account.update(access_token=token["access_token"], refresh_token=token["refresh_token"],
                           expires_at=time.time() + int(token.get("expires_in", 3600)))
            if token.get("scope"):
                account["scopes"] = str(token["scope"]).split()
            self.store.save(self.data)
            return account["access_token"]

    def list_models(self) -> list[tuple[str, str]]:
        result = _json_request(MODELS_URL, token=self.access_token(), opener=self.opener)
        models = result.get("models", [])
        return [(item["slug"], item.get("display_name") or item["slug"])
                for item in models if isinstance(item, dict) and item.get("visibility") == "list" and item.get("slug")]

    def sign_out(self) -> bool:
        """Clear local tokens; return whether remote revocation was confirmed."""
        with self._lock:
            account = self.active
            if not account:
                return True
            revoked = False
            try:
                discovery = _json_request(DISCOVERY_URL, opener=self.opener)
                endpoint = discovery.get("revocation_endpoint", "")
                if not isinstance(endpoint, str) or not endpoint.startswith(AUTH_BASE + "/"):
                    raise ValueError("撤销地址无效")
                payload = urlencode({"token": account["refresh_token"], "token_type_hint": "refresh_token",
                                     "client_id": account["client_id"]}).encode("utf-8")
                req = request.Request(endpoint, data=payload, headers={"Content-Type": "application/x-www-form-urlencoded"}, method="POST")
                with self.opener(req, timeout=15, context=_verified_context()):
                    pass
                revoked = True
            except (OSError, ValueError):
                pass
            for field in ("access_token", "refresh_token", "id_token", "expires_at"):
                account.pop(field, None)
            self.store.save(self.data)
            return revoked


def parse_response_stream(response: Any) -> str:
    chunks: list[str] = []
    completed = False
    for raw in response:
        line = raw.decode("utf-8") if isinstance(raw, bytes) else raw
        if not line.startswith("data: "):
            continue
        data = line[6:].strip()
        if data == "[DONE]":
            break
        event = json.loads(data)
        kind = event.get("type")
        if kind == "response.output_text.delta":
            chunks.append(str(event.get("delta", "")))
        elif kind in ("response.failed", "response.incomplete", "error"):
            error = event.get("response", {}).get("error") or event.get("error") or {}
            raise RuntimeError(str(error.get("code") or error.get("message") or kind))
        elif kind == "response.completed":
            completed = True
    if not completed:
        raise RuntimeError("翻译流未收到 response.completed")
    result = "".join(chunks).strip()
    if not result:
        raise ValueError("模型未返回译文")
    return result


class ChatGPTPlanProvider:
    def __init__(self, token_getter: Callable[[], str], model: str,
                 instructions: str = DEFAULT_INSTRUCTIONS,
                 opener: Callable[..., Any] = request.urlopen) -> None:
        self.token_getter = token_getter
        self.model = model
        self.instructions = instructions or DEFAULT_INSTRUCTIONS
        self.opener = opener

    def translate(self, text: str, source_language: str) -> str:
        if source_language != "rus":
            raise ValueError("ChatGPT 仅用于俄语聊天")
        return self._translate_with_instructions(text, self.instructions)

    def translate_outgoing(self, text: str) -> str:
        return self._translate_with_instructions(text, OUTGOING_INSTRUCTIONS)

    def _translate_with_instructions(self, text: str, instructions: str) -> str:
        if not self.model:
            raise RuntimeError("请先选择 ChatGPT 模型")
        payload = json.dumps({
            "model": self.model,
            "instructions": instructions,
            "input": [{"role": "user", "content": text}],
            "store": False,
            "stream": True,
        }, ensure_ascii=False).encode("utf-8")
        req = request.Request(RESPONSES_URL, data=payload, headers={
            "Authorization": "Bearer " + self.token_getter(),
            "Content-Type": "application/json",
            "Accept": "text/event-stream",
        }, method="POST")
        try:
            with self.opener(req, timeout=30, context=_verified_context()) as response:
                return parse_response_stream(response)
        except HTTPError as exc:
            code = ""
            detail = ""
            try:
                payload = json.loads(exc.read(8192))
                if isinstance(payload, dict):
                    error = payload.get("error")
                    if isinstance(error, dict):
                        code = str(error.get("code") or "")
                        detail = str(error.get("message") or "")
                    elif isinstance(payload.get("detail"), str):
                        detail = payload["detail"]
            except (OSError, ValueError, UnicodeError):
                pass
            parts = [f"ChatGPT HTTP {exc.code}"]
            if code:
                parts.append(code[:100])
            if detail:
                parts.append(detail[:180])
            raise RuntimeError(" · ".join(parts)) from exc
