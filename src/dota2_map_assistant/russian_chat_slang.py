"""Whole-clause game grammar; ambiguous ordinary prose stays with the model."""
from __future__ import annotations

from decimal import Decimal
import re


def alternatives(values):
    return '(?:'+'|'.join(re.escape(v) for v in sorted(values,key=len,reverse=True))+')'


class RussianChatSlang:
    def __init__(self, heroes):
        self.heroes=heroes
        self.owners={'меня':'我','тебя':'你','него':'他','нее':'她','неё':'她',
                     'нас':'我们','вас':'你们','них':'他们',**heroes}
        self.recipients={'мне':'我','тебе':'你','ему':'他','ей':'她','нам':'我们',
                         'вам':'你们','им':'他们','керри':'大哥',**heroes}
        self.subjects={'я':'我','ты':'你','он':'他','она':'她','мы':'我们','вы':'你们','они':'他们',**heroes}
        self.owner=alternatives(self.owners)
        self.hero=alternatives(heroes)
        self.recipient=alternatives(self.recipients)
        self.subject=alternatives(self.subjects)
        self.rating=re.compile(
            rf'(?:(?P<owner>у {self.owner}) )?(?P<change>плюс |минус |[+\-])?'
            r'(?P<number>[0-9]{1,5}(?:[.,][0-9]{1,2})?)\s*'
            r'(?P<scale>к|k|тыщ|тыс\.?|тысяч|тысячи)?\s*(?:птс|ммр|mmr)')

    def clause(self,key):
        match=self.rating.fullmatch(key)
        if match:
            # A signed personal rating is ambiguous; only explicit gain/loss
            # statements and nonnegative absolute ratings are recognized.
            if match['owner'] and match['change']:
                return None
            value=Decimal(match['number'].replace(',','.'))*(1000 if match['scale'] else 1)
            if value!=value.to_integral() or value>99999:
                return None
            number=str(int(value))
            if match['change']:
                return ('掉了' if match['change'].strip() in ('минус','-') else '涨了')+number+'分'
            return (self.owners[match['owner'][2:]]+'有' if match['owner'] else '')+number+'分'
        match=re.fullmatch(rf'у (?P<owner>{self.owner}) (?P<n>[0-9]{{1,5}}) урона с руки',key)
        if match:
            return self.owners[match['owner']]+'有'+match['n']+'普攻伤害'

        reasons={'афк':'挂机','фид':'送人头','читы':'开挂','софт':'开挂','руин':'故意破坏比赛'}
        reason=alternatives(reasons)
        match=re.fullmatch(
            rf'(?P<neg>не )?(?:(?:киньте|кидайте|закиньте) репор(?:т|тик)|'
            rf'репорт|репортик|репортните|репортите)'
            rf'(?: (?P<hero>{self.hero}))? за (?P<reason>{reason})',key)
        if match:
            return ('别举报' if match['neg'] else '举报')+(self.heroes[match['hero']] if match['hero'] else '')+reasons[match['reason']]
        if key=='репортик афк закиньте':
            return '举报一下挂机'

        match=re.fullmatch(rf'(?P<neg>не )?(?:дай|дайте|давай|давайте) (?:(?P<before>{self.recipient}) )?спейс(?: (?P<after>{self.recipient}))?',key)
        if match and not (match['before'] and match['after']):
            recipient=match['before'] or match['after']
            return ('别给' if match['neg'] else '给')+(self.recipients[recipient] if recipient else '')+('发育空间' if match['neg'] else '点发育空间')
        match=re.fullmatch(rf'(?P<who>{self.subject}) (?P<neg>не )?(?P<verb>спейсил|спейсили|спейсим|спейсит|спейсишь|подспейсил|подспейсили|поспейсил|поспейсили)',key)
        if match:
            ongoing=match['verb'] in ('спейсим','спейсит','спейсишь')
            if ongoing:
                action=('没在' if match['neg'] else '在')+'创造发育空间'
            elif match['neg']:
                action='没创造发育空间'
            else:
                action='创造了'+('点' if match['verb'].startswith(('под','по')) else '')+'发育空间'
            return self.subjects[match['who']]+action
        if key=='спейса нет':return '没有发育空间'
        if key=='нам нужен спейс':return '我们需要发育空间'

        match=re.fullmatch(rf'(?:(?P<who>парни|ребята|ребят|{self.hero})[, ]+)?(?P<verb>чил(?:ь|л|ль)?ни(?:те)?|не тильтуй(?:те)?)(?: уже)?',key)
        if match:
            who=match['who']
            prefix=('兄弟们' if who in ('парни','ребята','ребят') else self.heroes[who])+'，' if who else ''
            return prefix+('别心态爆炸' if match['verb'].startswith('не ') else '冷静点')+('吧' if key.endswith(' уже') else '')
        lanes={'линия':'那一路','мид':'中路','топ':'上路','бот':'下路'}
        match=re.fullmatch(rf'у (?P<owner>{self.owner}) (?P<lane>{alternatives(lanes)}) падает',key)
        if match:return self.owners[match['owner']]+lanes[match['lane']]+'要崩了'
        match=re.fullmatch(r'(?P<who>ты|вы) (?:(?:просто|что) )?(?:больной|бьольной|бъольной|больные)',key)
        if match:return ('你们' if match['who']=='вы' else '你')+'有病吧'
        if re.fullmatch(r'ты (?:обезьяна|обезяна|оьезбяна)',key):return '你是猴子'
        return None
