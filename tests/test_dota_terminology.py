import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from dota2_map_assistant.dota_terminology import (
    DotaAwareTranslationProvider,
    local_term_translation,
    localize_translation,
    prepare_for_translation,
)
from dota2_map_assistant.dota_hero_roster import HERO_ROSTER


class RecordingProvider:
    def __init__(self) -> None:
        self.calls = []

    def translate(self, text: str, language: str) -> str:
        self.calls.append((text, language))
        return "DS is mid and AM is missing"


class DotaTerminologyTests(unittest.TestCase):
    def test_custom_provider_must_return_chinese_for_russian_chat(self) -> None:
        class LatinProvider:
            def translate(self, _text, _language):
                return "VVEB"

        with self.assertRaisesRegex(ValueError, "中文"):
            DotaAwareTranslationProvider(LatinProvider()).translate("и въеб", "rus")
        with self.assertRaisesRegex(ValueError, "中文"):
            DotaAwareTranslationProvider(LatinProvider()).translate("керри BKB", "rus")

    def test_full_russian_sentences_are_sent_to_translation_service(self) -> None:
        self.assertIsNone(local_term_translation("я ниразу не выиграл со спектрой"))
        self.assertIsNone(local_term_translation("я только за"))

    def test_dota_slang_is_localized_by_terms_across_different_messages(self) -> None:
        self.assertEqual(
            localize_translation("слева мясо", "左侧肉类"),
            "左侧好打的目标",
        )
        self.assertEqual(
            localize_translation("справа мясо", "右侧肉类"),
            "右侧好打的目标",
        )
        self.assertEqual(
            localize_translation("керри 19к нетворса", "19K网络携带"),
            "核心净资产 19K",
        )

    def test_russian_hero_name_corrects_literal_spectrum_mistranslation(self) -> None:
        self.assertEqual(
            localize_translation("со мной спектру закидывают", "他们把频谱扔给我"),
            "他们把幽鬼扔给我",
        )

    def test_official_hero_roster_is_complete_and_unique(self) -> None:
        self.assertEqual(len(HERO_ROSTER), 127)
        self.assertEqual(len({hero_id for hero_id, _english, _chinese in HERO_ROSTER}), 127)
        self.assertIn((155, "Largo", "朗戈"), HERO_ROSTER)
        self.assertIn((78, "Brewmaster", "酒仙"), HERO_ROSTER)

    def test_hero_names_not_in_original_small_glossary_work_offline(self) -> None:
        for _hero_id, english, chinese in HERO_ROSTER:
            with self.subTest(hero=english):
                self.assertEqual(local_term_translation(english), chinese)
        self.assertEqual(local_term_translation("Largo / Ringmaster"), "朗戈、百戏大王")
        self.assertEqual(local_term_translation("Nature's Prophet"), "自然先知")

    def test_brewmaster_mistranslation_is_corrected(self) -> None:
        self.assertEqual(
            localize_translation("Brewmaster and AM", "酿酒师和上午"),
            "酒仙和敌法师",
        )

    def test_hero_abbreviations_expand_before_network_translation(self) -> None:
        self.assertEqual(
            prepare_for_translation("ds mid, am missing"),
            "Dark Seer mid, Anti-Mage missing",
        )

    def test_ordinary_am_is_not_treated_as_a_hero(self) -> None:
        text = "I am going mid at 3 am"
        self.assertEqual(prepare_for_translation(text), text)

    def test_local_translation_works_without_network_for_only_terms(self) -> None:
        self.assertEqual(local_term_translation("AM / DS"), "敌法师、黑暗贤者")
        self.assertEqual(local_term_translation("jugg / buyback"), "主宰、买活")
        self.assertIsNone(local_term_translation("am missing"))
        self.assertIsNone(local_term_translation("ES"))

    def test_translated_hero_names_are_canonical(self) -> None:
        self.assertEqual(
            localize_translation("DS mid, AM missing", "DS在中路，上午失踪了"),
            "黑暗贤者在中路，敌法师失踪了",
        )

    def test_wrapper_preserves_original_language_and_localizes_result(self) -> None:
        provider = RecordingProvider()
        wrapped = DotaAwareTranslationProvider(provider)

        translated = wrapped.translate("ds mid, am missing", "eng")

        self.assertEqual(provider.calls, [("Dark Seer mid, Anti-Mage missing", "eng")])
        self.assertEqual(translated, "黑暗贤者 is mid and 敌法师 is missing")

    def test_longer_hero_names_do_not_match_inside_other_words(self) -> None:
        self.assertEqual(prepare_for_translation("pudge with drow"), "Pudge with Drow Ranger")
        self.assertEqual(prepare_for_translation("my airline is late"), "my airline is late")


if __name__ == "__main__":
    unittest.main()
