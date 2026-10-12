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
            self._spans = {'zh': {}, 'ru': {}}
            self._heroes: dict[str, str] = {}
            self._profanity = {'zh': {}, 'ru': {}}
            self._english_terms = {}
            self._fixed_swears = {'zh': set(), 'ru': set()}
            for phrase in data['phrases']:
                for language in self._directions(phrase):
                    other = 'ru' if language == 'zh' else 'zh'
                    for alias in [phrase[language], *phrase.get(language + '_aliases', [])]:
                        key = self._key(alias)
                        self._phrases[language][key] = phrase[other]
                        if phrase.get('strength', 0) >= 2:
                            self._fixed_swears[language].add(key)
                        if not phrase.get('whole_message_only', False):
                            self._spans[language][key] = phrase[other]
            for term in data['terms']:
                for language in self._directions(term):
                    other = 'ru' if language == 'zh' else 'zh'
                    for alias in [term[language], *term.get(language + '_aliases', [])]:
                        key = self._key(alias)
                        if key in self._terms[language] and self._terms[language][key] != term[other]:
                            raise ValueError(f'重复词条：{alias}')
                        self._terms[language][key] = term[other]
                        if language == 'ru' and term.get('en'):
                            self._english_terms[key] = term['en']
                        if term.get('kind') == 'profanity':
                            self._fixed_swears[language].add(key)
                        if term.get('kind') == 'profanity':
                            self._profanity[language][key] = term[other]
                        if not term.get('whole_message_only', False):
                            # Phrase meanings take priority over standalone terms.
                            self._spans[language].setdefault(key, term[other])
                        if language == 'ru' and term.get('kind') == 'hero':
                            self._heroes[key] = term[other]
            self._patterns = {}
            for language, terms in self._spans.items():
                patterns = []
                for term in sorted(terms, key=len, reverse=True):
                    if language == 'zh' and len(term) == 1:
                        continue  # 眼睛、粉丝、香烟 must not become game terminology.
                    escaped = re.escape(term).replace(r'\ ', r'\s+')
                    if language == 'zh':
                        patterns.append(escaped if re.search(r'[\u4e00-\u9fff]', term)
                                        else rf'(?<![A-Za-z0-9_]){escaped}(?![A-Za-z0-9_])')
                    else:
                        patterns.append(rf'(?<!\w){escaped}(?!\w)')
                self._patterns[language] = re.compile('|'.join(patterns) or r'(?!)', re.IGNORECASE)
            self._prepare_russian_templates()
        except (OSError, KeyError, TypeError, ValueError) as exc:
            raise RuntimeError(f'聊天词库无法加载：{path.name}：{exc}') from exc

    @staticmethod
    def _key(text: str) -> str:
        return re.sub(r'\s+', ' ', text.strip()).casefold().rstrip('。.!！?？')

    def hints(self, text: str, source: str) -> list[str]:
        """Relevant term hints; never substitute/split grammar-bearing phrases."""
        hints = []
        if source == 'zh' and re.search(r'这(?:波|把)我的', text) and re.search(r'按错|失误|错了|打错', text):
            hints.append('这波我的/这把我的 = мой косяк（在承认失误的语境是我的锅，不是抢兵线）')
        for term, meaning in sorted(self._terms[source].items(), key=lambda item: -len(item[0])):
            if len(term) < 2:
                continue
            if term in self._profanity[source]:
                continue  # These are preserved by the constrained decoder.
            pattern = re.escape(term) if source == 'zh' else rf'(?<!\w){re.escape(term)}(?!\w)'
            if not re.search(pattern, text, re.IGNORECASE):
                continue
            if source == 'ru' and term.startswith('софт'):
                meaning = '软件；Dota 作弊语境中指外挂，工作软件语境不指外挂'
            hint = f'{term} = {meaning}'
            if hint not in hints:
                hints.append(hint)
            if len(hints) >= 12:
                break
        if source == 'ru':
            subjects = {'все': '所有人', **self._subjects}
            for match in re.finditer(r'(?<!\w)(' + '|'.join(subjects) + r')\s+(?:афк|afk)(?!\w)', text, re.I):
                subject = match[1].casefold()
                plural = '都' if subject in ('все', 'мы', 'вы', 'они') else ''
                hints.insert(0, f'{match.group()} = {subjects[subject]}{plural}在挂机（状态；按原文保留问号）')
            for alias, hero in self._heroes.items():
                if re.search(rf'(?<!\w)на\s+{re.escape(alias)}(?!\w)', text, re.I):
                    hints.insert(0, f'на {alias} = 玩{hero}，指使用该英雄的玩家')
                    break
        return hints[:12]

    def protect_profanity(self, text: str, source: str) -> tuple[str, dict[str, str]]:
        """Keep known offensive words intact while translating the whole grammar."""
        terms = self._profanity[source]
        alternatives = []
        for term in sorted(terms, key=len, reverse=True):
            escaped = re.escape(term)
            alternatives.append(escaped if source == 'zh' else rf'(?<!\w){escaped}(?!\w)')
        pattern = re.compile('|'.join(alternatives) or r'(?!)', re.I)
        replacements = {}
        def protect(match):
            marker = f'[[DOTA_WORD_{len(replacements)}]]'
            while marker in text:
                marker = marker.replace('WORD_', 'WORD_X_')
            meaning = terms[self._key(match.group())]
            if source == 'ru' and self._key(match.group()) == 'сука' and re.match(r'\s*,', text[match.end():]):
                meaning = '他妈的'  # An interjection; "ты сука" remains a noun insult.
            replacements[marker] = meaning
            return marker
        return pattern.sub(protect, text), replacements

    def profanity_hints(self, text: str, source: str) -> list[str]:
        """Original offensive spans, longest first, for a whole-source prompt."""
        terms = self._profanity[source]
        alternatives = [re.escape(term) if source == 'zh' else rf'(?<!\w){re.escape(term)}(?!\w)'
                        for term in sorted(terms, key=len, reverse=True)]
        return [match.group() for match in re.finditer('|'.join(alternatives) or r'(?!)', text, re.I)]

    def is_fixed_swear(self, text: str, source: str) -> bool:
        return self._key(text) in self._fixed_swears[source]

    @staticmethod
    def restore_profanity(output: str, replacements: dict[str, str]) -> str:
        remaining = {}
        for meaning in replacements.values():
            if meaning not in remaining:
                expected = sum(value == meaning for value in replacements.values())
                rendered = output.casefold().count(meaning.casefold())
                if rendered > expected:
                    raise ValueError('模型额外重复了原文粗话，请重试')
                remaining[meaning] = rendered
        for marker, meaning in replacements.items():
            if output.count(marker) != 1:
                raise ValueError('模型遗漏或重复了原文粗话，请重试')
            if remaining[meaning]:
                output = output.replace(marker, '')
                remaining[meaning] -= 1
            else:
                output = output.replace(marker, meaning)
        return output

    @staticmethod
    def _directions(entry: dict) -> tuple[str, ...] | list[str]:
        directions = entry.get('directions', ('zh', 'ru'))
        if not isinstance(directions, (tuple, list)) or not directions or any(
                language not in ('zh', 'ru') for language in directions):
            raise ValueError('词条 directions 必须包含 zh 或 ru')
        return directions

    def _prepare_russian_templates(self) -> None:
        # Hero aliases are listed once in the editable data, not once per sentence.
        heroes = '|'.join(re.escape(alias) for alias in sorted(self._heroes, key=len, reverse=True)) or r'(?!)'
        self._subjects = {'я': '我', 'ты': '你', 'он': '他', 'она': '她',
                          'мы': '我们', 'вы': '你们', 'они': '他们'}
        self._owners = {'меня': '我', 'тебя': '你', 'него': '他', 'неё': '她', 'нее': '她',
                        'нас': '我们', 'вас': '你们', 'них': '他们'}
        subjects = '|'.join(self._subjects)
        owners = '|'.join(self._owners)
        self._ownership = re.compile(
            rf'у (?P<who>{heroes}|{owners}) (?P<claim>(?:есть )?(?:софт|читы)|(?:нет|нету) (?:софта|читов))')
        self._claim = re.compile(
            rf'(?P<who>{heroes}|{subjects}) (?P<claim>софт|читер|софтер|с читами|на софте|не читер|не софтер|без читов|без софта)')
        self._playing = re.compile(
            rf'(?:(?P<who>{subjects}) (?:игра(?:ю|ешь|ет|ем|ете|ют) )?)?на (?P<hero>{heroes})')
        self._obscene_claim = re.compile(
            rf'(?P<who>{heroes}|{subjects}) (?P<swear>ебаный|ёбаный) (?P<cheater>читер|софтер)')
        self._obscene_command = re.compile(r'соси(?:те)? (?:(?P<swear>ебаный|ёбаный) )?(?:читер|софтер)')

    def _russian_message(self, key: str) -> str | None:
        match = self._ownership.fullmatch(key)
        if match:
            who = self._heroes.get(match['who'], self._owners.get(match['who']))
            negative = match['claim'].startswith(('нет ', 'нету '))
            return who + ('没开挂' if negative else '开挂了')
        match = self._claim.fullmatch(key)
        if match:
            who = self._heroes.get(match['who'], self._subjects.get(match['who']))
            negative = match['claim'].startswith(('не ', 'без '))
            return who + ('没开挂' if negative else '开挂了')
        match = self._playing.fullmatch(key)
        if match:
            hero = self._heroes[match['hero']]
            return self._subjects[match['who']] + '玩' + hero if match['who'] else '玩' + hero + '的'
        match = self._obscene_claim.fullmatch(key)
        if match:
            who = self._heroes.get(match['who'], self._subjects.get(match['who']))
            return who + '是狗日的开挂者'
        match = self._obscene_command.fullmatch(key)
        if match:
            return '含屌去，' + ('狗日的' if match['swear'] else '') + '开挂者'
        return None

    def exact(self, text: str, source: str) -> str | None:
        key = self._key(text)
        result = self._phrases[source].get(key)
        if result is None:
            result = self._terms[source].get(key)
        if result is None and source == 'ru':
            result = self._russian_message(key)
        if result is not None:
            suffix = re.search(r'[.!！?？]+$', text.rstrip())
            return result + (suffix.group().replace('！', '!').replace('？', '?') if suffix else '')
        return None

    @staticmethod
    def _literal_software(text: str, start: int, end: int) -> bool:
        before, after = text[:start].casefold(), text[end:].casefold()
        return bool(re.search(
            r'(?<!\w)(?:обновил|обновила|обновить|обнови|скачал|скачала|скачать|скачай|'
            r'установил|установила|установить|установи|бесплатный|новый|полезный|версия|лицензия)\s+$', before)
            or re.match(r'\s+(?:для\s+(?:работы|монтажа|видео|учёбы|учебы)|(?:не\s+)?работает)(?!\w)', after))

    def split(self, text: str, source: str) -> list[tuple[str, bool]]:
        chunks: list[tuple[str, bool]] = []
        start = 0
        for match in self._patterns[source].finditer(text):
            if match.start() > start:
                chunks.append((text[start:match.start()], False))
            key = self._key(match.group())
            translated = self._spans[source][key]
            if source == 'ru' and key in ('софт', 'софта', 'софту', 'софтом', 'софте'):
                if self._literal_software(text, match.start(), match.end()):
                    translated = '软件'
            elif source == 'ru' and key in ('нет софта', 'софта нет', 'без софта'):
                if self._literal_software(text, match.start(), match.end()):
                    translated = '没有软件'
            elif source == 'ru' and key == 'на софте':
                if self._literal_software(text, match.start(), match.end()):
                    translated = '用软件'
            chunks.append((translated, True))
            start = match.end()
        if start < len(text):
            chunks.append((text[start:], False))
        return chunks
