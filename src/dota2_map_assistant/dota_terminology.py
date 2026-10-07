from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Protocol

from .dota_hero_roster import HERO_ROSTER


@dataclass(frozen=True)
class DotaTerm:
    abbreviation: str
    english: str
    chinese: str
    chinese_variants: tuple[str, ...] = ()


HERO_ALIASES = {
    "Anti-Mage": ("AM",),
    "Ancient Apparition": ("AA",),
    "Bounty Hunter": ("BH",),
    "Chaos Knight": ("CK",),
    "Crystal Maiden": ("CM",),
    "Dark Seer": ("DS",),
    "Dark Willow": ("DW",),
    "Death Prophet": ("DP",),
    "Dragon Knight": ("DK",),
    "Drow Ranger": ("DROW",),
    "Elder Titan": ("ET",),
    "Faceless Void": ("FV",),
    "Juggernaut": ("JUGG",),
    "Keeper of the Light": ("KOTL",),
    "Legion Commander": ("LC",),
    "Lina": ("LINA",),
    "Lion": ("LION",),
    "Lone Druid": ("LD",),
    "Monkey King": ("MK",),
    "Nature's Prophet": ("NP",),
    "Night Stalker": ("NS",),
    "Outworld Destroyer": ("OD",),
    "Phantom Assassin": ("PA",),
    "Phantom Lancer": ("PL",),
    "Primal Beast": ("PB",),
    "Pudge": ("PUDGE",),
    "Queen of Pain": ("QOP",),
    "Shadow Demon": ("SD",),
    "Shadow Fiend": ("SF",),
    "Slark": ("SLARK",),
    "Spirit Breaker": ("SB",),
    "Templar Assassin": ("TA",),
    "Terrorblade": ("TB",),
    "Tidehunter": ("TIDE",),
    "Winter Wyvern": ("WW",),
    "Windranger": ("WR",),
    "Wraith King": ("WK",),
}
HERO_VARIANTS = {
    "Anti-Mage": ("反法师", "反魔法师", "上午"),
    "Axe": ("斧头",),
    "Brewmaster": ("酿酒师",),
    "Dark Seer": ("黑暗先知",),
    "Nature's Prophet": ("先知",),
    "Primal Beast": ("兽",),
}
_HERO_NAMES = {english: chinese for _id, english, chinese in HERO_ROSTER}
HERO_TERMS = tuple(
    DotaTerm(english, english, chinese, HERO_VARIANTS.get(english, ()))
    for _id, english, chinese in HERO_ROSTER
)
ALIAS_TERMS = tuple(
    DotaTerm(alias, english, _HERO_NAMES[english], HERO_VARIANTS.get(english, ()))
    for english, aliases in HERO_ALIASES.items() for alias in aliases
    if alias.casefold() != english.casefold()
)
GAME_TERMS = (
    DotaTerm("BKB", "Black King Bar", "黑皇杖"),
    DotaTerm("TP", "teleport", "传送"),
    DotaTerm("ROSH", "Roshan", "肉山"),
    DotaTerm("AEGIS", "Aegis of the Immortal", "不朽之守护"),
    DotaTerm("BUYBACK", "buyback", "买活", ("回购",)),
    DotaTerm("DEWARD", "deward", "排眼"),
)
TERMS = HERO_TERMS + ALIAS_TERMS + GAME_TERMS

class TranslationProvider(Protocol):
    def translate(self, text: str, source_language: str) -> str:
        ...


def _abbreviation_pattern(term: DotaTerm) -> re.Pattern[str]:
    insensitive_aliases = {"DS", "AM", "SF", "TP", "BKB"}
    insensitive = term.abbreviation == term.english or len(term.abbreviation) > 2 or term.abbreviation in insensitive_aliases
    flags = re.IGNORECASE if insensitive else 0
    return re.compile(rf"(?<![A-Za-z0-9]){re.escape(term.abbreviation)}(?![A-Za-z0-9])", flags)


def _is_ordinary_am(text: str, start: int, end: int) -> bool:
    before = text[:start].rstrip()
    after = text[end:]
    return bool(
        re.search(r"\bI$", before, re.IGNORECASE)
        or re.search(r"\b\d{1,2}$", before)
        or re.match(r"\s+I\b", after, re.IGNORECASE)
    )


def _abbreviation_matches(text: str, term: DotaTerm) -> list[re.Match[str]]:
    matches = list(_abbreviation_pattern(term).finditer(text))
    if term.abbreviation == "AM":
        matches = [match for match in matches if not _is_ordinary_am(text, match.start(), match.end())]
    return matches


def mentioned_terms(text: str) -> list[DotaTerm]:
    result: list[DotaTerm] = []
    seen: set[str] = set()
    for term in TERMS:
        if term.english in seen:
            continue
        if _abbreviation_matches(text, term) or re.search(
            rf"(?<![A-Za-z0-9]){re.escape(term.english)}(?![A-Za-z0-9])", text, re.IGNORECASE
        ):
            result.append(term)
            seen.add(term.english)
    return result


def prepare_for_translation(text: str) -> str:
    text = re.sub(r"\bниразу\b", "ни разу", text, flags=re.IGNORECASE)
    text = re.sub(r"\bда те кажется\b", "да тебе кажется", text, flags=re.IGNORECASE)
    replacements: list[tuple[int, int, str]] = []
    for term in TERMS:
        for match in _abbreviation_matches(text, term):
            replacements.append((match.start(), match.end(), term.english))
    chosen: list[tuple[int, int, str]] = []
    for start, end, english in sorted(replacements, key=lambda item: (item[0], item[0] - item[1])):
        if not chosen or start >= chosen[-1][1]:
            chosen.append((start, end, english))
    for start, end, english in reversed(chosen):
        text = text[:start] + english + text[end:]
    return text


def local_term_translation(text: str) -> str | None:
    parts = re.split(r"\s*[/,，+&]\s*", text.strip())
    if not parts or any(not part for part in parts):
        return None
    translated: list[str] = []
    for part in parts:
        term = _exact_term(part)
        if term is not None:
            translated.append(term.chinese)
            continue
        for token in part.split():
            term = _exact_term(token)
            if term is None:
                return None
            translated.append(term.chinese)
    return "、".join(translated)


def _exact_term(text: str) -> DotaTerm | None:
    return next((term for term in TERMS if _abbreviation_pattern(term).fullmatch(text)), None)


def localize_translation(source: str, translated: str) -> str:
    if not translated:
        return translated
    source_lower = source.casefold()
    if re.search(r"\bмясо\b", source_lower):
        translated = translated.replace("肉类", "好打的目标").replace("肉", "好打的目标")
    if re.search(r"\bнетворс\w*\b", source_lower):
        translated = translated.replace("网络", "净资产").replace("净值", "净资产")
    if re.search(r"\bкерри\b", source_lower):
        translated = translated.replace("携带", "核心").replace("搬运", "核心")
    translated = re.sub(r"(\d+)\s*[Kkк]\s*净资产核心", r"核心净资产 \1K", translated)
    if re.search(r"\bмидак\w*\b", source_lower):
        translated = translated.replace("蜂蜜酒", "中单")
    elif re.search(r"\bмид\w*\b", source_lower):
        translated = translated.replace("蜂蜜酒", "中路")
    if re.search(r"\bспектр(?:а|ой|у|е|ы)\b", source, re.IGNORECASE):
        for mistranslation in ("频谱", "光谱", "Spectre"):
            translated = translated.replace(mistranslation, "幽鬼")
    hints: list[str] = []
    for term in mentioned_terms(source):
        if term.chinese in translated:
            continue
        for variant in term.chinese_variants:
            translated = translated.replace(variant, term.chinese)
        translated = re.sub(rf"(?<![A-Za-z]){re.escape(term.english)}(?![A-Za-z])", term.chinese, translated, flags=re.IGNORECASE)
        translated = _abbreviation_pattern(term).sub(term.chinese, translated)
        if term.chinese not in translated:
            hints.append(f"{term.abbreviation}＝{term.chinese}")
    if hints:
        translated = f"{translated}（{'；'.join(hints)}）"
    return translated


class DotaAwareTranslationProvider:
    def __init__(self, provider: TranslationProvider) -> None:
        self.provider = provider

    def translate(self, text: str, source_language: str) -> str:
        prepared = prepare_for_translation(text)
        translated = self.provider.translate(prepared, source_language)
        if source_language == "rus" and not any("\u4e00" <= char <= "\u9fff" for char in translated):
            raise ValueError("翻译服务未返回中文译文")
        localized = localize_translation(text, translated)
        return localized
