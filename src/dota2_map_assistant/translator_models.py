from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class TextObservation:
    text: str
    language: str
    confidence: float | None
    observed_at: float


@dataclass(frozen=True)
class TranslationEntry:
    key: str
    source_text: str
    language: str
    translated_text: str
    status: str
    observed_at: float


@dataclass(frozen=True)
class NetworkProfile:
    name: str
    min_interval_seconds: float
    max_chars_per_minute: int


PROFILE_LIMITS = {
    "Full": NetworkProfile("Full", 0.8, 12_000),
    "Balanced": NetworkProfile("Balanced", 2.5, 3_000),
    "Low": NetworkProfile("Low", 8.0, 800),
}
