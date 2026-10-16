"""Anchored Russian chat constructions with controlled hero/item slots."""
from __future__ import annotations

import re

QUOTES = re.compile(r'["“”‘’«»]|(?<!\w)\'|\'(?!\w)')
CLAUSES = re.compile(r'[^，,。.!！?？;；]+')


def alternatives(values):
    return '(?:' + '|'.join(re.escape(v) for v in sorted(values, key=len, reverse=True)) + ')' if values else r'(?!)'


class RussianChatCommands:
    def __init__(self, heroes, items):
        self.heroes, self.items = heroes, items
        self.hero = alternatives(heroes)
        self.item = alternatives(items)
        self.targets = {'рош':'肉山', 'роша':'肉山', 'рошу':'肉山', 'рошан':'肉山', 'рошана':'肉山',
                        'тор':'魔方', 'терзатель':'魔方', 'терзателя':'魔方',
                        'торментор':'魔方', 'торментора':'魔方'}
        self.target = alternatives(self.targets)
        self.owners = {'меня':'我', 'тебя':'你', 'него':'他', 'нее':'她', 'неё':'她',
                       'нас':'我们', 'вас':'你们', 'них':'他们', **heroes}
        self.owner = alternatives(self.owners)

    @staticmethod
    def normalized(text):
        # This spelling map is used only by fully anchored constructions below.
        # Unknown Latin prose is returned to the ordinary translation path.
        latin = {'kupi':'купи','ne':'не','a':'а','mantu':'манту','skadi':'скади',
                 'pls':'пожалуйста','bkb':'бкб','go':'го','rosh':'рошу','tormentor':'терзателя',
                 'blyat':'блять','gde':'где','wards':'варды','zayebal':'заебал','zaebal':'заебал','etot':'этот'}
        text = text.casefold()
        text = re.sub(r'\b[A-Za-z]+\b', lambda match: latin.get(match.group(), match.group()), text)
        text = re.sub(r',\s*(?:а\s+)?не\s+', ' не ', text)
        return ' '.join(text.split())

    def clause(self, text):
        if QUOTES.search(text):
            return None
        key = self.normalized(text)
        match = re.fullmatch(rf'(?:пацаны |ребята |ребят )?не идите (?:на )?хг у них (?P<first>{self.hero}) с (?P<first_skill>бх|хроно) и (?P<second>{self.hero}) с (?P<second_skill>бх|хроно) давайте лучше (?P<target>{self.target}) сначала', key)
        if match:
            first, second = self.heroes[match['first']], self.heroes[match['second']]
            skill1, skill2 = self.skill(first, match['first_skill']), self.skill(second, match['second_skill'])
            if skill1 and skill2:
                return '兄弟们，别上高地，对面' + first + '有' + skill1 + '，' + second + '有' + skill2 + '，先打' + self.targets[match['target']]
        match = re.fullmatch(rf'(?:го|идем|идём) (?P<target>{self.target})(?: (?:а )?не (?P<other>{self.target}))?', key)
        if match:
            return ('打' if match['other'] else '去打') + self.targets[match['target']] + ('，别打' + self.targets[match['other']] if match['other'] else '')
        match = re.fullmatch(rf'(?:забираем|заберем|заберём) (?P<target>{self.target}) и отходим', key)
        if match:
            return '打完' + self.targets[match['target']] + '就撤'
        match = re.fullmatch(rf'(?:го|идем|идём) (?P<target>{self.target}) пока они (?:мертвые|мёртвые)', key)
        if match:
            return '趁他们还没复活，去打' + self.targets[match['target']]
        if re.fullmatch(r'не (?:ходи|ходите|лезь|лезьте|идите) на (?:хг|hg) без (?:аеги|аегиса|аегиcа)', key):
            return '没盾别上高地'
        if re.fullmatch(r'не (?:ходи|ходите|лезь|лезьте|идите) (?:на )?(?:хг|hg)', key):
            return '别上高地'
        match = re.fullmatch(rf'(?:(?P<hero>{self.hero})[, ]+)?(?P<neg>не )?(?:купи|купите|покупай|покупайте|собери|соберите|собирай|собирайте) (?P<item>{self.item})(?: (?:а )?не (?P<other>{self.item}))?(?P<please> пожалуйста)?', key)
        if match:
            result = (self.heroes[match['hero']] + '，' if match['hero'] else '')
            result += ('别买' if match['neg'] else '买') + self.items[match['item']]
            if match['other']:
                result += '，别买' + self.items[match['other']]
            return result + ('，拜托了' if match['please'] else '')
        match = re.fullmatch(rf'у (?P<owner>{self.owner}) (?:есть )?(?P<item>{self.item})(?P<double> два ульта)?', key)
        if match and (not match['double'] or self.items[match['item']] == '刷新球'):
            return self.owners[match['owner']] + '有' + self.items[match['item']] + ('，可以放两次大招' if match['double'] else '')
        match = re.fullmatch(rf'у (?P<hero>{self.hero}) (?P<skill>бх|хроно)', key)
        if match:
            who = self.heroes[match['hero']]
            if skill := self.skill(who, match['skill']):
                return who + '有' + skill
        match = re.fullmatch(rf'у (?P<hero>{self.hero}) (?P<skill>хроно|бх|ульт) через (?P<seconds>[0-9]{{1,4}}) сек', key)
        if match:
            return self.heroes[match['hero']] + '的大招还有' + match['seconds'] + '秒'
        match = re.fullmatch(rf'не трогайте (?P<hero>{self.hero}) у (?:него|нее|неё) (?P<skill>бх|хроно)', key)
        if match:
            who = self.heroes[match['hero']]
            if skill := self.skill(who, match['skill']):
                return '别惹' + who + '，' + who + '有' + skill
        match = re.fullmatch(rf'мы (?:не можем|не сможем) убить (?:этого )?(?P<hero>{self.hero}) сейчас (?:вообще )?давайте (?:заберем|заберём) (?P<target>{self.target}) потом распушим линии', key)
        if match:
            return '我们现在杀不掉这个' + self.heroes[match['hero']] + '，先打' + self.targets[match['target']] + '，然后把兵线推出去'
        match = re.fullmatch(r'(?:(?P<swear>бля|блять)[, ]+)?где (?:варды|вард)', key)
        if match:
            return ('操，' if match['swear'] else '') + '眼在哪'
        match = re.fullmatch(rf'заебал этот (?P<hero>{self.hero})', key)
        if match:
            return '这' + self.heroes[match['hero']] + '真他妈烦'
        return None

    @staticmethod
    def skill(hero, abbreviation):
        allowed = ('谜团', '拉比克') if abbreviation == 'бх' else ('虚空假面', '拉比克')
        return ('黑洞' if abbreviation == 'бх' else '时间结界') if hero in allowed else None

    def spans(self, text):
        if QUOTES.search(text):
            return []
        return [(match.start(), match.end(), translated) for match in CLAUSES.finditer(text)
                if (translated := self.clause(match.group())) is not None]

    def message(self, text):
        # A comma can belong to a hero address; try the whole construction first.
        whole = self.clause(text.rstrip('。.!！?？'))
        if whole is not None:
            return whole
        matches = list(CLAUSES.finditer(text))
        if not matches or QUOTES.search(text):
            return None
        translated = [self.clause(match.group()) for match in matches]
        if any(value is None for value in translated):
            return None
        parts = []
        for index, (match, value) in enumerate(zip(matches, translated)):
            if index:
                separator = text[matches[index-1].end():match.start()]
                parts.append('。' if re.search(r'[。.!！?？]', separator) else '，')
            parts.append(value)
        return ''.join(parts)
