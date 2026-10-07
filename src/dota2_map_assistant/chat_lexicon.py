"""Editable Dota chat translations. Protected spans keep their exact strength."""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from .app_paths import resource_root

LEXICON_PATH = resource_root() / 'data' / 'dota_chat_lexicon.json'


class ChatLexicon:
    def __init__(self, path: Path = LEXICON_PATH) -> None:
        try:
            raw = path.read_bytes()
            data = json.loads(raw)
            if data.get('schema_version') != 1:
                raise ValueError('词库版本不受支持')
            self.fingerprint = hashlib.sha256(raw).hexdigest()[:16]
            self._phrases = {'zh': {}, 'ru': {}}
            self._terms = {'zh': {}, 'ru': {}}
            for phrase in data['phrases']:
                for language in ('zh', 'ru'):
                    other = 'ru' if language == 'zh' else 'zh'
                    self._phrases[language][self._key(phrase[language])] = phrase[other]
                    for alias in phrase.get(language + '_aliases', []):
                        self._phrases[language][self._key(alias)] = phrase[other]
            for term in data['terms']:
                for language in ('zh', 'ru'):
                    other = 'ru' if language == 'zh' else 'zh'
                    for alias in [term[language], *term.get(language + '_aliases', [])]:
                        key = alias.casefold()
                        if key in self._terms[language] and self._terms[language][key] != term[other]:
                            raise ValueError(f'重复词条：{alias}')
                        self._terms[language][key] = term[other]
            self._patterns = {}
            self._spans = {language: {**self._terms[language], **self._phrases[language]} for language in ('zh', 'ru')}
            for language, terms in self._spans.items():
                patterns = []
                for term in sorted(terms, key=len, reverse=True):
                    if language == 'zh' and len(term) == 1:
                        continue  # 眼睛、粉丝、香烟 must not become game terminology.
                    escaped = re.escape(term)
                    if language == 'zh':
                        patterns.append(escaped if re.search(r'[\u4e00-\u9fff]', term)
                                        else rf'(?<![A-Za-z0-9_]){escaped}(?![A-Za-z0-9_])')
                    else:
                        patterns.append(rf'(?<!\w){escaped}(?!\w)')
                self._patterns[language] = re.compile('|'.join(patterns) or r'(?!)', re.IGNORECASE)
        except (OSError, KeyError, TypeError, ValueError) as exc:
            raise RuntimeError(f'聊天词库无法加载：{path.name}：{exc}') from exc

    @staticmethod
    def _key(text: str) -> str:
        return re.sub(r'\s+', ' ', text.strip()).casefold().rstrip('。.!！?？')

    def exact(self, text: str, source: str) -> str | None:
        result = self._phrases[source].get(self._key(text))
        if result is not None:
            suffix = re.search(r'[.!！?？]+$', text)
            return result + (suffix.group().replace('！', '!').replace('？', '?') if suffix else '')
        return self._terms[source].get(text.strip().casefold())

    def split(self, text: str, source: str) -> list[tuple[str, bool]]:
        chunks: list[tuple[str, bool]] = []
        start = 0
        for match in self._patterns[source].finditer(text):
            if match.start() > start:
                chunks.append((text[start:match.start()], False))
            chunks.append((self._spans[source][match.group().casefold()], True))
            start = match.end()
        if start < len(text):
            chunks.append((text[start:], False))
        return chunks
