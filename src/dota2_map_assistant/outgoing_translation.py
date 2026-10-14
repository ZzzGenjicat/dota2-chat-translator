"""Translate player-written Chinese chat into Russian for manual sending."""

from __future__ import annotations

from .offline_translation import OfflineTranslationProvider, M2M100Engine, make_translation_cache
from .chat_lexicon import ChatLexicon


class OutgoingTranslator:
    def __init__(self, provider: OfflineTranslationProvider | None = None) -> None:
        if provider is None:
            lexicon = ChatLexicon()
            engine = M2M100Engine()
            provider = OfflineTranslationProvider(engine, make_translation_cache(engine.model_path, lexicon), 'ru', lexicon)
        self.provider = provider

    def translate(self, text: str, backend: str = 'offline', model: str = '') -> str:
        source = text.strip()
        if not source:
            raise ValueError("请输入中文消息")
        # Legacy labels cannot enable an online translation path.
        return self.provider.translate(source, 'zho')
