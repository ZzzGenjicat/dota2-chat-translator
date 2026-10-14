"""Editable Dota chat translations. Protected spans keep their exact strength."""
from __future__ import annotations

import hashlib
from collections import Counter
import json
import re
from string import Formatter
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
            self._ru_forms = data.get('ru_profanity_forms', {})
            self._zh_forms = data.get('zh_profanity_forms', {})
            if any(not isinstance(forms, dict) or any(
                    not isinstance(key, str) or not isinstance(values, list) or
                    any(not isinstance(value, str) or not value or '\n' in value for value in values)
                    for key, values in forms.items()) for forms in (self._ru_forms, self._zh_forms)):
                raise ValueError('粗话词形必须为词语与词形列表')
            self._anchors = data.get('profanity_anchors', {})
            if not isinstance(self._anchors, dict) or any(
                    language not in ('zh', 'ru') or not isinstance(anchors, dict) or any(
                    not isinstance(key, str) or not key or not isinstance(value, str) or not value or '\n' in value
                    for key, value in anchors.items()) for language, anchors in self._anchors.items()):
                raise ValueError('粗话约束必须为对应语言的粗话词语')
            self._phrases = {'zh': {}, 'ru': {}}
            self._terms = {'zh': {}, 'ru': {}}
            self._spans = {'zh': {}, 'ru': {}}
            self._heroes: dict[str, str] = {}
            zh_heroes = set()
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
                        elif language == 'zh' and term.get('kind') == 'hero':
                            zh_heroes.add(key)
            self._patterns = {}
            for language, terms in self._spans.items():
                patterns = []
                for term in sorted(terms, key=len, reverse=True):
                    if language == 'zh' and len(term) == 1:
                        continue  # 眼睛、粉丝、香烟 must not become game terminology.
                    patterns.append(self._span_pattern(term, language))
                self._patterns[language] = re.compile('|'.join(patterns) or r'(?!)', re.IGNORECASE)
            self._hero_ultimate = re.compile('(?:' + ('|'.join(re.escape(hero) for hero in sorted(zh_heroes, key=len, reverse=True)) or r'(?!)')
                + r')(?:的)?大(?:招)?(?!衣|学|哥|家|鱼|地|灾|雨|事)', re.I)
            self._prepare_russian_templates()
            self._prepare_chat_templates(data)
        except (OSError, KeyError, TypeError, ValueError) as exc:
            raise RuntimeError(f'聊天词库无法加载：{path.name}：{exc}') from exc

    @staticmethod
    def _key(text: str) -> str:
        return re.sub(r'\s+', ' ', text.strip()).casefold().rstrip('。.!！?？')

    @staticmethod
    def _span_pattern(term: str, source: str) -> str:
        escaped = re.escape(term).replace(r'\ ', r'\s+')
        if source == 'ru':
            return rf'(?<!\w){escaped}(?!\w)'
        if not re.search(r'[\u4e00-\u9fff]', term):
            return rf'(?<![A-Za-z0-9_]){escaped}(?![A-Za-z0-9_])'
        if term == '妈的':
            escaped = r'(?<![\u4e00-\u9fff])' + escaped
        if term.endswith('操'):
            escaped += r'(?!作|控|纵|练|场|持|办|盘|劳|守|弄)'
        if term.endswith('雾'):
            escaped += r'(?!化|灯|机|器)'
        if term == '点人':
            escaped += r'(?!头|数|口)'
        return escaped

    def hints(self, text: str, source: str, history: tuple[str, ...] = ()) -> list[str]:
        """Relevant term hints; never substitute/split grammar-bearing phrases."""
        hints = self._game_phrases(text, source, history)
        if source == 'zh' and re.search(r'这(?:波|把)我的', text) and re.search(r'按错|失误|错了|打错', text):
            hints.append('这波我的/这把我的 = мой косяк')
        for term, meaning in sorted(self._terms[source].items(), key=lambda item: -len(item[0])):
            if len(term) < 2:
                continue
            if term in self._profanity[source]:
                continue  # These are preserved by the constrained decoder.
            pattern = self._span_pattern(term, source)
            if not re.search(pattern, text, re.IGNORECASE):
                continue
            if source == 'ru':
                # These short slang words also have literal non-game meanings.
                lower = text.casefold()
                if term in ('обс', 'обса', 'обсов', 'obs', 'observer') and re.search(
                        r'стрим|запис[а-яё]*\s+видео|программ[а-яё]*|трансляц', lower):
                    meaning = 'OBS 录制/直播软件'
                elif term in ('респ', 'респа', 'респавн', 'респавна', 'resp', 'respawn') and re.search(
                        rf'(?<!\w)(?:{re.escape(term)}\s+(?:рош[а-яё]*|рун[а-яё]*|крип[а-яё]*|нейтрал[а-яё]*|кемп[а-яё]*|кэмп[а-яё]*)|'
                        rf'(?:рош[а-яё]*|рун[а-яё]*|крип[а-яё]*|нейтрал[а-яё]*|кемп[а-яё]*|кэмп[а-яё]*)\s+{re.escape(term)})(?!\w)', lower):
                    meaning = '刷新'
                elif term in ('смок', 'смока', 'смоках', 'smoke'):
                    meaning = '诡计之雾'
                elif term in ('сейв', 'сейва', 'сейвом', 'save') and re.search(r'файл|проект|документ|реплей|запис[а-яё]*', lower):
                    meaning = '保存'
                elif term in ('стак', 'стаки', 'stack') and re.search(r'пати|игрок[а-яё]*|впятером|вдвоём', lower):
                    meaning = '组排队伍'
                elif term in ('кура', 'курой', 'куру', 'куре') and re.search(r'ужин|обед|жарен|варен|суп|тарелк', lower):
                    meaning = '鸡肉'
            if source == 'ru' and term.startswith('софт'):
                literal = any(self._literal_software(text, match.start(), match.end())
                              for match in re.finditer(pattern, text, re.I))
                work_context = re.search(r'(?<!\w)(?:программ[а-яё]*|монтаж[а-яё]*|работ[а-яё]*)(?!\w)', '\n'.join(history), re.I)
                meaning = '软件' if literal or work_context else '外挂'
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
                question = '?' if re.match(r'\s*[?？]', text[match.end():]) else ''
                hints.insert(0, f'{match.group()} = {subjects[subject]}{plural}在挂机{question}')
            for alias, hero in self._heroes.items():
                if re.search(rf'(?<!\w)на\s+{re.escape(alias)}(?!\w)', text, re.I):
                    hints.insert(0, f'на {alias} = 玩{hero}的')
                    break
        return hints[:12]

    def _game_phrases(self, text: str, source: str, history: tuple[str, ...]) -> list[str]:
        """A few recurring game constructions, without replacing sentence parts."""
        hints = []
        if source == 'zh':
            if re.search(r'别误会(?:我|你|他|她|它)(?:是|在|要|想|会|没|不)', text):
                hints.append('别误会 = не думай, что')
            if self._hero_ultimate.search(text):
                hints.append('大 = ульт')
            digits = dict(zip('零一二三四五六七八九', '0123456789'))
            for match in re.finditer(r'([一二三四五六七八九\d])换([一二三四五六七八九\d])', text):
                first, second = (digits.get(match[n], match[n]) for n in (1, 2))
                hints.append(f'{match.group()} = размен {first} на {second}')
            if re.search(r'白交大|(?:不|别|先|再|乱)交大(?!学)|交大(?:了|招|给|之前|以后)', text):
                hints.append('交大 = использовать ульт')
            for match in re.finditer(r'(?:还(?:有|要|得)?\s*)?(\d+)\s*秒(?:后|才能|才)?(?:自然)?复活', text):
                hints.append(f'{match.group()} = респ через {match[1]} сек')
            # Single-character item nicknames are ambiguous outside these
            # constructions; never turn 粉丝/粉底/羊肉/weather into game items.
            if re.search(r'(?:买|带|撒|补)(?:了|个|一[个包])?粉(?!底|色|丝|笔|末|状)|粉(?:没带|没买|用完|撒了)', text):
                hints.append('粉 = даст')
            if re.search(r'(?:买|带|用|开)(?:个|一个)?雾(?!化|灯|机|器)|雾(?:我|你|他|我们|你们)?买', text):
                hints.append('雾 = смок')
            if re.search(r'(?:接羊|先羊)(?!肉|排|腿|毛|奶|汤|皮|头|年)|羊(?:他|她|对面|住)', text):
                hints.append('羊 = хекс')
            for match in re.finditer(r'(?:刷|刷完|刷会儿|刷一会儿|去刷|打钱刷)三角(?:区)?', text):
                hints.append(f'{match.group()} = фармить треугольник')
            if re.search(r'(?:大鱼(?:人)?|斯拉达).*?点灯|点灯.*?(?:大鱼(?:人)?|斯拉达)', text):
                hints.append('点灯 = дать ульт')
            return hints
        if source != 'ru':
            return hints
        for match in re.finditer(r'(?<!\w)говно\s*,?\s+а\s+не\s+(\w+)', text, re.I):
            meaning = self._terms['ru'].get(match[1].casefold())
            if meaning:
                if match[1].casefold().startswith('сейв') and re.search(r'файл|проект|документ|реплей', text, re.I):
                    meaning = '保存'
                hints.append(f'{match.group()} = 不是{meaning}，是狗屎')
        if re.search(r'(?<!\w)сало(?!\w)', text, re.I) and re.search(r'мант[а-яё]*|сним[а-яё]*|снят[а-яё]*|диспел[а-яё]*', text, re.I):
            hints.append('снимать сало = 解除沉默')
        salve = re.search(r'(?<!\w)(?:салва|фласка)\s+на\s+мне(?!\w)', text, re.I)
        if salve:
            hints.append(f'{salve.group()} = 我正在用治疗药膏回血')
        buyback = r'(?:байбек(?:а|ом)?|bb|бб)'
        for pattern, meaning in (
            (rf'{buyback}\s+не\s+(?:жми|нажимай|юзай|используй)', '别使用买活'),
            (rf'не\s+(?:жми|нажимай|юзай|используй)\s+{buyback}', '别使用买活'),
            (rf'{buyback}\s+(?:нет|нету)', '没有买活可用'),
            (rf'(?:нет|нету|без)\s+{buyback}', '没有买活可用'),
            (rf'{buyback}\s+через\s+(\d+)\s*(?:сек|с\b)', None),
        ):
            for match in re.finditer(r'(?<!\w)' + pattern + r'(?!\w)', text, re.I):
                value = meaning if meaning is not None else f'买活冷却还有{match[1]}秒'
                hints.append(f'{match.group()} = {value}')
        level = re.search(r'(?<!\w)(?:шестой\s+(?:взял|апнул)|(?:взял|апнул)\s+шестой)(?!\w)', text, re.I)
        if level:
            hints.append(f'{level.group()} = 升到六级')
            movement = re.search(r'(?<!\w)(?:могу\s+выйти|выйду(?:\s+с\s+линии)?)(?!\w)', text, re.I)
            if movement:
                hints.append(f'{movement.group()} = 可以离线游走支援')
        context = text + '\n' + '\n'.join(history[-3:])
        finger = re.search(r'(?<!\w)пал(?:ец|ьца|ьцем)(?!\w)', text, re.I)
        if finger and re.search(r'(?<!\w)(?:лион[а-яё]*|lion)(?!\w)|莱恩', context, re.I) and not re.search(
                r'болит|слом[а-яё]*|порез[а-яё]*|кров[а-яё]*|травм[а-яё]*', text, re.I):
            hints.append(f'{finger.group()} = 死亡一指')
        if re.search(r'(?<!\w)смок[а-яё]*(?!\w)', text, re.I) and re.search(r'(?<!\w)лома[а-яё]*(?!\w)', text, re.I):
            hints.append('ломать смок = 破雾')
        if re.search(r'(?<!\w)тащ(?:им|у|ить|ишь|ат)(?!\w)', text, re.I):
            hints.append('тащить = 带队赢比赛')
        if re.search(r'(?<!\w)(?:кормить|фидить|фид)[а-яё]*(?!\w)', text, re.I):
            hints.append('кормить/фидить = 送人头')
            if re.search(r'(?<!\w)курой(?!\w)', text, re.I):
                hints.insert(0, 'курой кормить = 送信使')
        for match in re.finditer(r'(?<!\w)(?:стакни|стакай|стакнуть|стакать)\s+(малый|маленький|большой)(?!\w)', text, re.I):
            size = '大' if match[1].casefold() == 'большой' else '小'
            hints.insert(0, f'{match.group()} = 叠{size}野')
        return hints[:6]

    def _profanity_matches(self, text: str, source: str):
        terms = self._profanity[source]
        alternatives = []
        for term in sorted(terms, key=len, reverse=True):
            escaped = self._span_pattern(term, source)
            if source == 'zh' and len(term) == 1:
                escaped = rf'(?<![\u4e00-\u9fff]){escaped}(?![\u4e00-\u9fff])'
            alternatives.append(escaped)
        pattern = re.compile('|'.join(alternatives) or r'(?!)', re.I)
        for match in pattern.finditer(text):
            key = self._key(match.group())
            if source == 'ru' and key in ('сука', 'суки', 'суку', 'suka', 'cyka'):
                before, after = text[:match.start()], text[match.end():]
                if (re.search(r'(?:игрок|ник|никнейм)\s+$', before, re.I)
                        or re.match(r'\s+(?:родила|кормит\s+щенков)(?!\w)', after, re.I)):
                    continue
            yield match

    def protect_profanity(self, text: str, source: str) -> tuple[str, dict[str, str]]:
        """Keep offensive spans in their grammatical scope, longest idiom first."""
        terms = self._profanity[source]
        replacements = {}
        pieces, start = [], 0
        for match in self._profanity_matches(text, source):
            marker = f'[[DOTA_WORD_{len(replacements)}]]'
            while marker in text:
                marker = marker.replace('WORD_', 'WORD_X_')
            meaning = terms[self._key(match.group())]
            if source == 'ru' and self._key(match.group()) in ('сука', 'suka', 'cyka') and re.match(r'\s*,', text[match.end():]):
                meaning = '他妈的'  # An interjection; "ты сука" remains a noun insult.
            elif source == 'zh' and self._key(match.group()) == '鸡巴' and re.search(
                    r'(?:打|开|接|推|守|买|救|奶|补|刷|带)个$', text[:match.start()]) and re.match(
                    r'团|塔|钱|兵|野|线|肉山|装备|人|队|基地|盾', text[match.end():]):
                meaning = 'нахуй'  # Rhetorical game action: "какой нахуй файт".
            elif source == 'zh' and self._key(match.group()) == '鸡巴' and (
                    not text[:match.start()].strip() or re.search(r'[，,！!。]\s*$', text[:match.start()])) and re.match(
                    r'\s*[，,！!]', text[match.end():]):
                meaning = 'блять'
            replacements[marker] = meaning
            pieces += [text[start:match.start()], marker]
            start = match.end()
        pieces.append(text[start:])
        return ''.join(pieces), replacements

    def profanity_hints(self, text: str, source: str) -> list[str]:
        """Original offensive spans, longest first, for a whole-source prompt."""
        return [match.group() for match in self._profanity_matches(text, source)]

    def is_fixed_swear(self, text: str, source: str) -> bool:
        return self._key(text) in self._fixed_swears[source]

    def profanity_forms(self, language: str) -> dict[str, tuple[str, ...]]:
        """Explicit morphology, not weaker substitutes or extra insults."""
        if language not in ('ru', 'zh'):
            return {}
        meanings = set(self._profanity['zh' if language == 'ru' else 'ru'].values())
        meanings.update(self._anchors.get(language, {}).values())
        explicit = self._ru_forms if language == 'ru' else self._zh_forms
        result = {}
        for meaning in meanings:
            forms = [meaning, *explicit.get(meaning, ())]
            if language == 'ru':
                forms += [form.replace('ебан', 'ёбан') for form in forms if 'ебан' in form]
                forms += [form.replace('ё', 'е') for form in forms if 'ё' in form]
                forms += [form.replace('нахуй', 'на хуй') for form in forms if 'нахуй' in form]
                forms += [form[:1].upper() + form[1:] for form in forms]
            result[meaning] = tuple(dict.fromkeys(forms))
        return result

    def profanity_constraints(self, replacements: dict[str, str], target: str) -> dict[str, str]:
        """Constrain the vulgar word, leaving the idiom's verb/order to translation."""
        anchors = self._anchors.get(target, {})
        return {marker: anchors.get(meaning, meaning) for marker, meaning in replacements.items()}

    def validate_profanity(self, output: str, replacements: dict[str, str], source: str, target: str) -> None:
        forms = self.profanity_forms(target)
        replacements = self.profanity_constraints(replacements, target)
        vocabulary = set(self._profanity[source].values()) | set(replacements.values())
        vocabulary = set(self.profanity_constraints({word: word for word in vocabulary}, target).values())
        canonical = {}
        for meaning in vocabulary:
            for form in forms.get(meaning, (meaning,)):
                canonical[form.casefold()] = meaning
        pattern = '|'.join(re.escape(word) + (r'(?!妈|咪)' if target == 'zh' and word == '他妈' else '')
                           for word in sorted(canonical, key=len, reverse=True))
        if target == 'ru':
            pattern = r'(?<!\w)(?:' + pattern + r')(?!\w)'
        rendered = Counter(canonical[match.group().casefold()] for match in re.finditer(pattern or r'(?!)', output, re.I))
        if rendered != Counter(replacements.values()):
            raise ValueError('模型遗漏或重复了原文粗话，请重试')

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
        if result is None:
            result = self._chat_template(key, source)
        if result is not None:
            suffix = re.search(r'[.!！?？]+$', text.rstrip())
            return result + (suffix.group().replace('！', '!').replace('？', '?') if suffix else '')
        return None

    def _prepare_chat_templates(self, data: dict) -> None:
        """Compile editable whole-message forms using only controlled slots."""
        self._chat_templates = {'zh': [], 'ru': []}
        self._slot_values = {language: {'hero': {}, 'item': {}} for language in ('zh', 'ru')}
        for term in data['terms']:
            kind = term.get('kind')
            if kind not in ('hero', 'item'):
                continue
            for language in self._directions(term):
                other = 'ru' if language == 'zh' else 'zh'
                for alias in (term[language], *term.get(language + '_aliases', [])):
                    self._slot_values[language][kind][self._key(alias)] = term[other]
        templates = data.get('templates', [])
        if not isinstance(templates, list):
            raise ValueError('短句模板必须是列表')
        formatter = Formatter()
        for template in templates:
            slots = template['slots']
            if not isinstance(slots, dict) or not slots or any(
                    not isinstance(name, str) or not re.fullmatch(r'[a-z][a-z_]*', name)
                    or kind not in ('number', 'hero', 'item') for name, kind in slots.items()):
                raise ValueError('短句模板只允许数字、英雄和物品参数')
            for language in ('zh', 'ru'):
                fields = list(formatter.parse(template[language]))
                if {field for _, field, _, _ in fields if field is not None} != set(slots) or any(
                        field is not None and (spec or conversion) for _, field, spec, conversion in fields):
                    raise ValueError('双语模板必须使用相同的参数，且不允许格式表达式')
            for language in self._directions(template):
                pattern = []
                seen = set()
                for literal, field, _, _ in formatter.parse(self._key(template[language])):
                    pattern.append(re.escape(literal).replace(r'\ ', r'\s+'))
                    if field is None:
                        continue
                    if field in seen:
                        pattern.append(f'(?P={field})')
                        continue
                    seen.add(field)
                    kind = slots[field]
                    values = self._slot_values[language].get(kind, {})
                    value_pattern = r'[0-9]{1,4}' if kind == 'number' else (
                        '|'.join(re.escape(value) for value in sorted(values, key=len, reverse=True)) or r'(?!)')
                    pattern.append(f'(?P<{field}>{value_pattern})')
                other = 'ru' if language == 'zh' else 'zh'
                self._chat_templates[language].append((re.compile(''.join(pattern), re.I),
                                                       template[other], slots))

    def _chat_template(self, key: str, source: str) -> str | None:
        for pattern, target, slots in self._chat_templates[source]:
            match = pattern.fullmatch(key)
            if match is None:
                continue
            values = {name: value if slots[name] == 'number' else
                      self._slot_values[source][slots[name]][self._key(value)]
                      for name, value in match.groupdict().items()}
            return target.format_map(values)
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
