from __future__ import annotations

import csv
import difflib
import io
import os
import re
import shutil
import subprocess
import time
from collections import Counter
from dataclasses import dataclass
from typing import Any

from PIL import Image, ImageFilter, ImageOps

from .translator_models import TextObservation


SUPPORTED_OCR_LANGUAGES = ("rus", "eng")
_WHITESPACE = re.compile(r"\s+")
_CHAT_WHEEL_ARROWS = "▶►▷▸▹⏵›»"
_CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)


@dataclass(frozen=True)
class ChatMessageCandidate:
    observation: TextObservation
    left: int
    top: int
    right: int
    bottom: int
    needs_refinement: bool = True
    crop_left: int | None = None


@dataclass(frozen=True)
class _OCRWord:
    text: str
    left: int
    top: int
    right: int
    bottom: int
    confidence: float

    @property
    def center_y(self) -> float:
        return (self.top + self.bottom) / 2


def normalize_text(text: str) -> str:
    return _WHITESPACE.sub(" ", text.replace("\x00", " ")).strip()


def is_chat_wheel_message(text: str) -> bool:
    """Dota prefixes localized chat-wheel phrases and spoken lines with an arrow."""
    stripped = text.lstrip()
    return bool(stripped) and (stripped[0] in _CHAT_WHEEL_ARROWS or stripped.startswith(">"))


def observation_key(text: str, language: str) -> str:
    return f"{language.casefold()}:{normalize_text(text).casefold()}"


class ObservationDeduplicator:
    def __init__(self, max_keys: int = 4096) -> None:
        self.max_keys = max(32, max_keys)
        self._keys: set[str] = set()

    def is_duplicate(self, observation: TextObservation) -> bool:
        text = normalize_text(observation.text)
        if not text:
            return True
        key = observation_key(text, observation.language)
        if key in self._keys:
            return True
        if len(self._keys) >= self.max_keys:
            self._keys.clear()
        self._keys.add(key)
        return False

    def clear(self) -> None:
        self._keys.clear()


class OCRReader:
    def __init__(self, languages: tuple[str, ...] = SUPPORTED_OCR_LANGUAGES) -> None:
        self.languages = tuple(languages)
        self.available = False
        self.status = "Checking local OCR"
        self._tesseract = _find_tesseract()
        if self._tesseract is None:
            self.status = "OCR unavailable: install Tesseract"
            return
        installed = _installed_languages(self._tesseract)
        missing = [language for language in self.languages if language not in installed]
        if missing:
            self.status = f"OCR missing language packs: {', '.join(missing)}"
            return
        self.available = True
        self.status = f"OCR ready: {' + '.join(self.languages)}"

    def read(self, image: Image.Image) -> list[TextObservation]:
        if not self.available or self._tesseract is None:
            return []
        primary_result = self._read_prepared(_prepare_image(image))
        if primary_result is None:
            return []
        primary, primary_tsv = primary_result
        chat = [(candidate, 2) for candidate in parse_dota_chat_tsv(primary_tsv)]
        if _has_chat_prefix(primary_tsv) or _count_chat_separators(primary_tsv):
            primary = []
        grayscale = ImageOps.grayscale(image)
        grayscale = grayscale.resize((max(1, grayscale.width * 2), max(1, grayscale.height * 2)))
        secondary_result = self._read_prepared(grayscale)
        if secondary_result is None:
            merged = _merge_chat_candidates(chat)
            return consolidate_observations(self._read_chat_messages(image, merged, scale=1)) if merged else []
        secondary, secondary_tsv = secondary_result
        chat.extend((candidate, 2) for candidate in parse_dota_chat_tsv(secondary_tsv))
        if _has_chat_prefix(secondary_tsv) or _count_chat_separators(secondary_tsv):
            secondary = []
        merged = _merge_chat_candidates(chat)
        if merged and (
            len(merged) < _count_chat_separators(secondary_tsv)
            or len(merged) == 1 and len(chat) == 1
        ):
            color_tsv = self._run_tesseract_tsv(image)
            if color_tsv is not None:
                chat.extend((candidate, 1) for candidate in parse_dota_chat_tsv(color_tsv))
                merged = _merge_chat_candidates(chat)
        if merged:
            return consolidate_observations(self._read_chat_messages(image, merged, scale=1))
        # Sparse OCR frequently reads sidebar names but misses the player separator.
        # A uniform-block pass recovers low-contrast chat rows; without a complete
        # player + separator + message layout, there is nothing safe to translate.
        color_tsv = self._run_tesseract_tsv(image, psm=6)
        if color_tsv is None:
            return []
        merged = _merge_chat_candidates([(candidate, 1) for candidate in parse_dota_chat_tsv(color_tsv)])
        return consolidate_observations(self._read_chat_messages(image, merged, scale=1)) if merged else []

    def _read_chat_messages(
        self, image: Image.Image, candidates: list[ChatMessageCandidate], scale: int,
    ) -> list[TextObservation]:
        messages: list[TextObservation] = []
        for candidate in candidates:
            if not candidate.needs_refinement:
                messages.append(candidate.observation)
                continue
            left = max(0, candidate.crop_left // scale + 3) if candidate.crop_left is not None else max(0, candidate.left // scale - 8)
            top = max(0, candidate.top // scale - 9)
            right = min(image.width, (candidate.right + scale - 1) // scale + 12)
            bottom = min(image.height, (candidate.bottom + scale - 1) // scale + 5)
            if right <= left or bottom <= top:
                messages.append(candidate.observation)
                continue
            crop = image.crop((left, top, right, bottom))
            crop_languages = tuple(language for language in ("rus", "eng") if language in self.languages)
            tsv = self._run_tesseract_tsv(crop, psm=7, languages=crop_languages or self.languages)
            if tsv and _crop_starts_with_chat_wheel_arrow(tsv):
                continue
            cropped = _message_from_crop_tsv(tsv, candidate.observation.observed_at) if tsv else None
            original = candidate.observation
            if (cropped is not None and any(char.isdigit() for char in original.text)
                    and len(original.text.split()) > len(cropped.text.split())
                    and (original.confidence or 0) >= 0.5):
                messages.append(original)
                continue
            if cropped is not None and (
                _cyrillic_count([cropped]) >= _cyrillic_count([original])
                or _cyrillic_count([original]) == 0
            ):
                messages.append(cropped)
            else:
                messages.append(original)
        return messages

    def _read_prepared(self, prepared: Image.Image) -> tuple[list[TextObservation], str] | None:
        tsv = self._run_tesseract_tsv(prepared)
        return (parse_tesseract_tsv(tsv), tsv) if tsv is not None else None

    def _run_tesseract_tsv(
        self, prepared: Image.Image, psm: int = 11, languages: tuple[str, ...] | None = None,
    ) -> str | None:
        payload = io.BytesIO()
        prepared.save(payload, format="PNG")
        command = [
            self._tesseract,
            "stdin",
            "stdout",
            "--psm",
            str(psm),
            "--oem",
            "3",
            "-l",
            "+".join(languages or self.languages),
            "tsv",
        ]
        try:
            result = subprocess.run(
                command,
                input=payload.getvalue(),
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=12,
                creationflags=_CREATE_NO_WINDOW if os.name == "nt" else 0,
                check=False,
            )
        except Exception as exc:  # pragma: no cover - depends on local installation
            self.status = f"OCR execution failed: {exc}"
            return None
        if result.returncode != 0:
            error = result.stderr.decode("utf-8", errors="replace").strip()
            self.status = f"OCR failed: {error or result.returncode}"
            return None
        return result.stdout.decode("utf-8", errors="replace")


def _ocr_result_score(observations: list[TextObservation]) -> int:
    return sum(max(0, len(normalize_text(item.text)) - 5) for item in observations)


def _cyrillic_count(observations: list[TextObservation]) -> int:
    return sum("\u0400" <= char <= "\u04ff" for item in observations for char in item.text)


def parse_tesseract_tsv(tsv: str, observed_at: float | None = None) -> list[TextObservation]:
    groups: dict[tuple[str, str, str], list[str]] = {}
    confidences: dict[tuple[str, str, str], list[float]] = {}
    reader = csv.DictReader(io.StringIO(tsv), delimiter="\t")
    for row in reader:
        text = normalize_text(row.get("text") or "")
        if not text:
            continue
        key = (
            row.get("block_num") or "",
            row.get("par_num") or "",
            row.get("line_num") or "",
        )
        groups.setdefault(key, []).append(text)
        confidence = _parse_confidence(row.get("conf"))
        if confidence is not None:
            confidences.setdefault(key, []).append(confidence)
    timestamp = time.time() if observed_at is None else observed_at
    observations: list[TextObservation] = []
    for key, words in groups.items():
        text = _clean_ocr_prefix(normalize_text(" ".join(words)))
        values = confidences.get(key, [])
        confidence = sum(values) / len(values) if values else None
        if _useful_ocr_line(text, confidence):
            observations.append(TextObservation(text, _guess_language(text), confidence, timestamp))
    return observations


def parse_dota_chat_tsv(tsv: str, observed_at: float | None = None) -> list[ChatMessageCandidate]:
    """Join OCR words by screen row, then extract only text after the player separator."""
    words: list[_OCRWord] = []
    for row in csv.DictReader(io.StringIO(tsv), delimiter="\t"):
        source_text = row.get("text") or ""
        if "\n" in source_text or "\r" in source_text or "\t" in source_text:
            continue
        raw = normalize_text(source_text)
        confidence = _parse_confidence(row.get("conf"))
        if not raw or confidence is None or confidence < 0.2:
            continue
        try:
            left = int(row["left"])
            top = int(row["top"])
            width = int(row["width"])
            height = int(row["height"])
        except (KeyError, TypeError, ValueError):
            continue
        if width <= 0 or height < 8:
            continue
        words.append(_OCRWord(raw, left, top, left + width, top + height, confidence))

    visual_rows: list[list[_OCRWord]] = []
    for word in sorted(words, key=lambda item: item.center_y):
        best = min(visual_rows, key=lambda line: abs(word.center_y - _row_center(line)), default=None)
        if best is not None and abs(word.center_y - _row_center(best)) <= max(9, min(word.bottom - word.top, _row_height(best)) * 0.45):
            best.append(word)
        else:
            visual_rows.append([word])

    timestamp = time.time() if observed_at is None else observed_at
    candidates: list[ChatMessageCandidate] = []
    for visual_row in visual_rows:
        for line in _split_distant_text(sorted(visual_row, key=lambda item: item.left)):
            candidate = _chat_candidate_from_line(line, timestamp)
            if candidate is not None:
                candidates.append(candidate)
    return candidates


def _split_distant_text(line: list[_OCRWord]) -> list[list[_OCRWord]]:
    segments: list[list[_OCRWord]] = []
    gap_limit = max(120, int(_row_height(line) * 3))
    for word in line:
        if not segments or word.left - max(previous.right for previous in segments[-1]) > gap_limit:
            segments.append([word])
        else:
            segments[-1].append(word)
    return segments


def _chat_candidate_from_line(line: list[_OCRWord], timestamp: float) -> ChatMessageCandidate | None:
    separator_index: int | None = None
    suffix = ""
    for index, word in enumerate(line):
        marker = ":" if ":" in word.text else ";" if (
            word.text == ";" or (word.text.endswith(";") and any("]" in previous.text for previous in line[:index + 1]))
        ) else ""
        if marker and "[" in word.text.split(marker, 1)[0] and "]" not in word.text.split(marker, 1)[0]:
            continue
        if marker and (index > 0 or word.text.index(marker) > 0):
            separator_index = index
            suffix = word.text.split(marker, 1)[1]
            break
    explicit_separator = separator_index is not None
    if separator_index is None:
        # Tesseract often loses the colon after a bracketed player suffix.
        bracket_count = sum("[" in word.text for word in line)
        has_player_before_suffix = any(
            len(word.text) >= 3 and any(char.isalpha() for char in word.text)
            and "[" not in word.text and "]" not in word.text
            for word in line[:-2]
        )
        if bracket_count >= 2 or has_player_before_suffix:
            closing = [index for index, word in enumerate(line[:-1]) if "]" in word.text]
            if closing:
                separator_index = closing[-1]
    if separator_index is None:
        return None
    if any(is_chat_wheel_message(word.text) for word in line[:separator_index + 1]):
        return None
    message_words = line[separator_index + 1:]
    raw_message = " ".join(([suffix] if suffix else []) + [word.text for word in message_words])
    if is_chat_wheel_message(raw_message):
        return None
    message = _normalize_chat_message(raw_message)
    if sum(char.isalpha() for char in message) < 2:
        return None
    confidence = sum(word.confidence for word in message_words) / len(message_words) if message_words else line[separator_index].confidence
    if confidence < 0.3:
        return None
    bounds = message_words or [line[separator_index]]
    return ChatMessageCandidate(
        TextObservation(message, _guess_language(message), confidence, timestamp),
        min(word.left for word in bounds), min(word.top for word in bounds),
        max(word.right for word in bounds), max(word.bottom for word in bounds),
        bool(message_words),
        line[separator_index].right if explicit_separator else None,
    )


def _row_center(words: list[_OCRWord]) -> float:
    return sum(word.center_y for word in words) / len(words)


def _row_height(words: list[_OCRWord]) -> float:
    return sum(word.bottom - word.top for word in words) / len(words)


def _merge_chat_candidates(items: list[tuple[ChatMessageCandidate, int]]) -> list[ChatMessageCandidate]:
    merged: list[ChatMessageCandidate] = []
    for candidate, scale in items:
        scaled = ChatMessageCandidate(
            candidate.observation,
            candidate.left // scale,
            candidate.top // scale,
            (candidate.right + scale - 1) // scale,
            (candidate.bottom + scale - 1) // scale,
            candidate.needs_refinement,
            candidate.crop_left // scale if candidate.crop_left is not None else None,
        )
        center = (scaled.top + scaled.bottom) / 2
        for index, existing in enumerate(merged):
            old_center = (existing.top + existing.bottom) / 2
            if abs(center - old_center) <= 9:
                if _chat_candidate_score(scaled) > _chat_candidate_score(existing):
                    merged[index] = scaled
                break
        else:
            merged.append(scaled)
    return sorted(merged, key=lambda item: (item.top + item.bottom, item.left))


def _chat_candidate_score(candidate: ChatMessageCandidate) -> tuple[bool, float, float]:
    text = candidate.observation.text
    letters = sum(char.isalpha() for char in text)
    cyrillic = sum("\u0400" <= char <= "\u04ff" for char in text)
    return (cyrillic > 0, cyrillic / max(1, letters), candidate.observation.confidence or 0.0)


def _has_chat_prefix(tsv: str) -> bool:
    return any(
        any(char in (row.get("text") or "") for char in "[]")
        for row in csv.DictReader(io.StringIO(tsv), delimiter="\t")
    )


def _count_chat_separators(tsv: str) -> int:
    count = 0
    for row in csv.DictReader(io.StringIO(tsv), delimiter="\t"):
        text = (row.get("text") or "").strip()
        if text in (":", ";") or (text.endswith(":") and len(text) < 60):
            count += 1
    return count


def _message_from_crop_tsv(tsv: str, observed_at: float) -> TextObservation | None:
    words: list[tuple[int, str, float]] = []
    for row in csv.DictReader(io.StringIO(tsv), delimiter="\t"):
        text = normalize_text(row.get("text") or "")
        confidence = _parse_confidence(row.get("conf"))
        if text and confidence is not None and confidence >= 0.2:
            try:
                left = int(row["left"])
            except (KeyError, TypeError, ValueError):
                continue
            words.append((left, text, confidence))
    if not words:
        return None
    words.sort(key=lambda item: item[0])
    text = _normalize_chat_message(" ".join(word for _, word, _ in words))
    visible = [char for char in text if not char.isspace()]
    letters = sum(char.isalpha() for char in visible)
    confidence = sum(value for _, _, value in words) / len(words)
    if letters < 2 or letters / max(1, len(visible)) < 0.5 or confidence < 0.4:
        return None
    return TextObservation(text, _guess_language(text), confidence, observed_at)


def _crop_starts_with_chat_wheel_arrow(tsv: str) -> bool:
    first: tuple[int, str] | None = None
    for row in csv.DictReader(io.StringIO(tsv), delimiter="\t"):
        text = normalize_text(row.get("text") or "")
        if not text:
            continue
        try:
            left = int(row["left"])
        except (KeyError, TypeError, ValueError):
            continue
        if first is None or left < first[0]:
            first = (left, text)
    return first is not None and is_chat_wheel_message(first[1])


def _normalize_chat_message(text: str) -> str:
    cleaned = normalize_text(text).strip(" \t\r\n_`'\"«»|.,;:‘’“”›\\")
    # Bilingual OCR confuses Latin and Cyrillic glyphs in numerical chat calls.
    cleaned = re.sub(r"(?<=\d)[kK](?=\b)", "к", cleaned)
    if re.search(r"\d|[\u0400-\u04ff]", cleaned):
        cleaned = re.sub(r"\b[HН][aа]\b", "на", cleaned)
    # This short, common Dota call loses the space between "да" and "в" in OCR.
    cleaned = re.sub(r"\bдав\s*трон\b", "да в трон", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"^ятолько\b", "я только", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\bдате кажется\b", "да те кажется", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\bспректру\b", "спектру", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\bейне\b", "ей не", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\bниразуей\b", "ниразу ей", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\bнугерой\b", "ну герой", cleaned, flags=re.IGNORECASE)
    return re.sub(r"\bолуди\b", "олухи", cleaned, flags=re.IGNORECASE)


def _clean_ocr_prefix(text: str) -> str:
    prefix, separator, message = text.partition(":")
    visible_prefix = [char for char in prefix if not char.isspace()]
    if separator and len(prefix) <= 30 and len(message.strip()) >= 12 and visible_prefix:
        symbols = sum(not char.isalnum() for char in visible_prefix)
        if symbols / len(visible_prefix) >= 0.25:
            return normalize_text(message)
    return text


def _useful_ocr_line(text: str, confidence: float | None) -> bool:
    visible = [char for char in text if not char.isspace()]
    letters = sum(char.isalpha() for char in visible)
    return bool(visible) and confidence is not None and confidence >= 0.75 and letters >= 3 and letters / len(visible) >= 0.55


def similar_observations(first: TextObservation, second: TextObservation) -> bool:
    if first.language != second.language:
        return False
    first_text = normalize_text(first.text).casefold()
    second_text = normalize_text(second.text).casefold()
    if min(len(first_text), len(second_text)) < 20:
        return False
    if re.findall(r"\d+", first_text) != re.findall(r"\d+", second_text):
        return False
    meaning_changing_words = {
        "no", "not", "never", "не", "нет", "ни", "nunca", "jamais", "nicht", "kein", "keine", "pas",
        "top", "bot", "bottom", "mid", "middle", "left", "right", "up", "down",
        "push", "defend", "retreat", "back", "stop", "go", "топ", "бот", "мид", "верх", "низ", "назад",
    }
    first_tokens = set(re.findall(r"[^\W\d_]+", first_text))
    second_tokens = set(re.findall(r"[^\W\d_]+", second_text))
    if (first_tokens & meaning_changing_words) != (second_tokens & meaning_changing_words):
        return False
    match = difflib.SequenceMatcher(None, first_text, second_text, autojunk=False)
    longest = match.find_longest_match().size
    first_words = set(_matching_words(first_text))
    second_words = set(_matching_words(second_text))
    common_words = len(first_words & second_words)
    return (
        match.ratio() >= 0.8
        or longest >= 24 and longest / min(len(first_text), len(second_text)) >= 0.7
        or common_words >= 4 and common_words / min(len(first_words), len(second_words)) >= 0.65
    )


def _matching_words(text: str) -> list[str]:
    return re.findall(r"[^\W\d_]{3,}", text.casefold())


def _ocr_quality(observation: TextObservation) -> float:
    words = _matching_words(observation.text)
    repetition = (len(words) - len(set(words))) / len(words) if words else 0.0
    return (observation.confidence or 0.0) - 0.3 * repetition


def consolidate_observations(observations: list[TextObservation]) -> list[TextObservation]:
    short_counts = Counter(
        normalize_text(item.text).casefold()
        for item in observations
        if len(normalize_text(item.text)) <= 6
    )
    has_messages = sum(len(normalize_text(item.text)) >= 8 for item in observations) >= 2
    result: list[TextObservation] = []
    for observation in observations:
        if has_messages and short_counts[normalize_text(observation.text).casefold()] >= 3:
            continue
        for index, existing in enumerate(result):
            if observation_key(existing.text, existing.language) == observation_key(observation.text, observation.language) or similar_observations(existing, observation):
                if _ocr_quality(observation) > _ocr_quality(existing):
                    result[index] = observation
                break
        else:
            result.append(observation)
    return result


def _find_tesseract() -> str | None:
    candidates = [
        shutil.which("tesseract"),
        os.environ.get("TESSERACT_PATH"),
        r"C:\Program Files\Tesseract-OCR\tesseract.exe",
        r"C:\Program Files (x86)\Tesseract-OCR\tesseract.exe",
    ]
    for candidate in candidates:
        if candidate and os.path.isfile(candidate):
            return candidate
    return None


def _installed_languages(executable: str) -> set[str]:
    try:
        result = subprocess.run(
            [executable, "--list-langs"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=5,
            creationflags=_CREATE_NO_WINDOW if os.name == "nt" else 0,
            check=False,
        )
    except Exception:
        return set()
    languages = set()
    for line in result.stdout.decode("utf-8", errors="replace").splitlines():
        line = line.strip()
        if line and not line.lower().startswith("list of available"):
            languages.add(line)
    return languages


def _prepare_image(image: Image.Image) -> Image.Image:
    grayscale = ImageOps.grayscale(image)
    resized = grayscale.resize((max(1, grayscale.width * 2), max(1, grayscale.height * 2)))
    enhanced = ImageOps.autocontrast(resized).filter(ImageFilter.SHARPEN)
    return enhanced.point(lambda pixel: 255 if pixel >= 180 else 0)


def _parse_confidence(value: Any) -> float | None:
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return None
    if numeric < 0:
        return None
    return max(0.0, min(1.0, numeric / 100.0))


def _guess_language(text: str) -> str:
    if any("\u0400" <= char <= "\u04ff" for char in text):
        return "rus"
    lowered = text.casefold()
    markers = {
        "spa": (" el ", " la ", " que ", " una ", "¿", "¡"),
        "deu": (" der ", " die ", " das ", " und ", " nicht "),
        "fra": (" le ", " les ", " des ", " une ", " est "),
    }
    padded = f" {lowered} "
    scores = {language: sum(marker in padded for marker in words) for language, words in markers.items()}
    best_language, best_score = Counter(scores).most_common(1)[0]
    return best_language if best_score else "eng"
