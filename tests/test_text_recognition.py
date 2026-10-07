import io
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

from PIL import Image


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from dota2_map_assistant.text_recognition import (
    ChatMessageCandidate,
    ObservationDeduplicator,
    consolidate_observations,
    normalize_text,
    parse_tesseract_tsv,
    parse_dota_chat_tsv,
    OCRReader,
    _prepare_image,
    _normalize_chat_message,
)
from dota2_map_assistant.translator_models import TextObservation


class TextRecognitionTests(unittest.TestCase):
    def test_numeric_russian_chat_recovers_latin_lookalikes(self) -> None:
        self.assertEqual(_normalize_chat_message("30k Ha 32"), "30к на 32")
        self.assertEqual(_normalize_chat_message("керри 19K нетворса"), "керри 19к нетворса")

    def test_sidebar_russian_without_player_message_separator_is_ignored(self) -> None:
        reader = OCRReader.__new__(OCRReader)
        reader.available = True
        reader._tesseract = "tesseract"
        reader.languages = ("rus", "eng")
        reader.status = "ready"
        header = "level\tpage_num\tblock_num\tpar_num\tline_num\tword_num\tleft\ttop\twidth\theight\tconf\ttext\n"
        names = (
            "5\t1\t1\t1\t1\t1\t710\t100\t130\t20\t96\tЧернодырый\n"
            "5\t1\t2\t1\t1\t1\t710\t130\t140\t20\t96\tсига+обсудить\n"
            "5\t1\t2\t1\t1\t2\t850\t130\t40\t20\t96\tмид\n"
        )
        result = type("Result", (), {"returncode": 0, "stdout": (header + names).encode(), "stderr": b""})()
        with patch("dota2_map_assistant.text_recognition.subprocess.run", return_value=result):
            observations = reader.read(Image.new("RGB", (896, 469), "black"))
        self.assertEqual(observations, [])

    def test_color_layout_recovers_chat_when_binary_and_gray_find_no_rows(self) -> None:
        reader = OCRReader.__new__(OCRReader)
        reader.available = True
        reader._tesseract = "tesseract"
        reader.languages = ("rus", "eng")
        reader.status = "ready"
        header = "level\tpage_num\tblock_num\tpar_num\tline_num\tword_num\tleft\ttop\twidth\theight\tconf\ttext\n"
        chat = (
            "5\t1\t1\t1\t1\t1\t53\t278\t50\t20\t95\tmncll\n"
            "5\t1\t1\t1\t1\t2\t111\t278\t7\t20\t95\t:\n"
            "5\t1\t1\t1\t1\t3\t129\t278\t50\t20\t95\tслева\n"
            "5\t1\t1\t1\t1\t4\t187\t278\t50\t20\t95\tмясо\n"
        )
        results = [
            type("Result", (), {"returncode": 0, "stdout": data.encode(), "stderr": b""})()
            for data in (header, header, header + chat,
                         header + "5\t1\t1\t1\t1\t1\t5\t5\t100\t20\t95\tслева мясо\n")
        ]
        with patch("dota2_map_assistant.text_recognition.subprocess.run", side_effect=results):
            observations = reader.read(Image.new("RGB", (896, 469), "black"))
        self.assertEqual([item.text for item in observations], ["слева мясо"])

    def test_visible_chat_separator_without_message_does_not_emit_sidebar_name(self) -> None:
        reader = OCRReader.__new__(OCRReader)
        reader.available = True
        reader._tesseract = "tesseract"
        reader.languages = ("rus", "eng")
        reader.status = "ready"
        header = "level\tpage_num\tblock_num\tpar_num\tline_num\tword_num\tleft\ttop\twidth\theight\tconf\ttext\n"
        layout = header + (
            "5\t1\t1\t1\t1\t1\t10\t20\t90\t20\t95\tИгрок:\n"
            "5\t1\t2\t1\t1\t1\t700\t20\t140\t20\t95\tПризер\n"
        )
        result = type("Result", (), {"returncode": 0, "stdout": layout.encode(), "stderr": b""})()
        with patch("dota2_map_assistant.text_recognition.subprocess.run", return_value=result):
            observations = reader.read(Image.new("RGB", (900, 60), "black"))

        self.assertEqual(observations, [])

    def test_identical_short_chat_text_is_emitted_only_once(self) -> None:
        repeated = [TextObservation("да", "rus", 0.9, 1.0), TextObservation("да", "rus", 0.8, 1.0)]

        self.assertEqual([item.text for item in consolidate_observations(repeated)], ["да"])

    def test_bracketed_player_suffix_can_mark_message_when_colon_is_missed(self) -> None:
        header = "level\tpage_num\tblock_num\tpar_num\tline_num\tword_num\tleft\ttop\twidth\theight\tconf\ttext\n"
        tsv = header + (
            "5\t1\t1\t1\t1\t1\t300\t90\t130\t30\t90\tPro100Belka\n"
            "5\t1\t2\t1\t1\t1\t440\t90\t80\t30\t90\t[*Val*]\n"
            "5\t1\t3\t1\t1\t1\t550\t90\t80\t30\t90\tолухи\n"
        )

        self.assertEqual([item.observation.text for item in parse_dota_chat_tsv(tsv)], ["олухи"])

    def test_reader_merges_chat_lines_found_in_different_ocr_passes(self) -> None:
        reader = OCRReader.__new__(OCRReader)
        reader.available = True
        reader._tesseract = "tesseract"
        reader.languages = ("rus", "eng")
        reader.status = "ready"
        header = "level\tpage_num\tblock_num\tpar_num\tline_num\tword_num\tleft\ttop\twidth\theight\tconf\ttext\n"
        first = (
            "5\t1\t1\t1\t1\t1\t10\t20\t40\t20\t95\tRen:\n"
            "5\t1\t2\t1\t1\t1\t55\t20\t80\t20\t95\tпривет\n"
        )
        second = (
            "5\t1\t3\t1\t1\t1\t10\t80\t40\t20\t95\tRen:\n"
            "5\t1\t4\t1\t1\t1\t55\t80\t80\t20\t95\tпока\n"
        )
        results = [
            type("Result", (), {"returncode": 0, "stdout": (header + rows).encode(), "stderr": b""})()
            for rows in (
                first, first + second,
                "5\t1\t1\t1\t1\t1\t4\t4\t80\t20\t95\tпривет\n",
                "5\t1\t1\t1\t1\t1\t4\t4\t80\t20\t95\tпока\n",
            )
        ]
        with patch("dota2_map_assistant.text_recognition.subprocess.run", side_effect=results):
            observations = reader.read(Image.new("RGB", (200, 100), "black"))

        self.assertEqual([item.text for item in observations], ["привет", "пока"])

    def test_reader_uses_color_pass_when_gray_detects_more_separators_than_messages(self) -> None:
        reader = OCRReader.__new__(OCRReader)
        reader.available = True
        reader._tesseract = "tesseract"
        reader.languages = ("rus", "eng")
        reader.status = "ready"
        header = "level\tpage_num\tblock_num\tpar_num\tline_num\tword_num\tleft\ttop\twidth\theight\tconf\ttext\n"
        def row(block: int, top: int, message: str) -> str:
            return (
                f"5\t1\t{block}\t1\t1\t1\t10\t{top}\t40\t20\t95\tRen:\n"
                + (f"5\t1\t{block}\t1\t1\t2\t55\t{top}\t80\t20\t95\t{message}\n" if message else "")
            )
        gray = row(1, 20, "первый") + row(2, 80, "") + row(3, 140, "третий")
        color = row(2, 40, "второй")
        results = [
            type("Result", (), {"returncode": 0, "stdout": (header + text).encode(), "stderr": b""})()
            for text in (
                "", gray, color,
                "5\t1\t1\t1\t1\t1\t4\t4\t80\t20\t95\tпервый\n",
                "5\t1\t1\t1\t1\t1\t4\t4\t80\t20\t95\tвторой\n",
                "5\t1\t1\t1\t1\t1\t4\t4\t80\t20\t95\tтретий\n",
            )
        ]
        with patch("dota2_map_assistant.text_recognition.subprocess.run", side_effect=results):
            messages = reader.read(Image.new("RGB", (200, 100), "black"))

        self.assertEqual([item.text for item in messages], ["первый", "второй", "третий"])

    def test_message_crop_recovers_low_confidence_words_after_colon(self) -> None:
        reader = OCRReader.__new__(OCRReader)
        reader.available = True
        reader._tesseract = "tesseract"
        reader.languages = ("rus", "eng")
        reader.status = "ready"
        header = "level\tpage_num\tblock_num\tpar_num\tline_num\tword_num\tleft\ttop\twidth\theight\tconf\ttext\n"
        layout = header + (
            "5\t1\t1\t1\t1\t1\t10\t20\t80\t20\t95\tPlayer\n"
            "5\t1\t1\t1\t1\t2\t100\t20\t6\t20\t95\t:\n"
            "5\t1\t1\t1\t1\t3\t120\t20\t10\t20\t0\tя\n"
            "5\t1\t1\t1\t1\t4\t135\t20\t70\t20\t0\tтолько\n"
            "5\t1\t1\t1\t1\t5\t220\t20\t20\t20\t96\tза\n"
        )
        calls = [0]
        def fake_ocr(_command, **kwargs):
            calls[0] += 1
            if calls[0] <= 2:
                data = layout
            else:
                crop_width = Image.open(io.BytesIO(kwargs["input"])).width
                word = "я только за" if crop_width > 60 else "за"
                data = header + f"5\t1\t1\t1\t1\t1\t2\t2\t80\t20\t95\t{word}\n"
            return type("Result", (), {"returncode": 0, "stdout": data.encode(), "stderr": b""})()
        with patch("dota2_map_assistant.text_recognition.subprocess.run", side_effect=fake_ocr):
            messages = reader.read(Image.new("RGB", (200, 70), "black"))

        self.assertEqual([item.text for item in messages], ["я только за"])

    def test_chat_rows_stay_top_down_and_exclude_distant_sidebar_text(self) -> None:
        header = "level\tpage_num\tblock_num\tpar_num\tline_num\tword_num\tleft\ttop\twidth\theight\tconf\ttext\n"
        words = [
            (1, 110, 100, 255, 30, "SUPERHUIOBA"),
            (2, 389, 110, 6, 17, ";"),
            (3, 424, 100, 100, 30, "я"),
            (4, 540, 100, 130, 30, "выиграл"),
            (5, 1420, 102, 100, 30, "darkness"),
            (6, 110, 150, 255, 30, "SUPERHUIOBA"),
            (7, 389, 160, 6, 17, ":"),
            (8, 424, 150, 100, 30, "иду"),
            (9, 540, 150, 100, 30, "подряд"),
            (10, 1420, 151, 100, 30, "xingho"),
        ]
        tsv = header + "".join(
            f"5\t1\t{block}\t1\t1\t1\t{left}\t{top}\t{width}\t{height}\t95\t{text}\n"
            for block, left, top, width, height, text in words
        )

        self.assertEqual(
            [item.observation.text for item in parse_dota_chat_tsv(tsv)],
            ["я выиграл", "иду подряд"],
        )

    def test_reader_retries_gray_chat_lines_before_returning_name_fragments(self) -> None:
        reader = OCRReader.__new__(OCRReader)
        reader.available = True
        reader._tesseract = "tesseract"
        reader.languages = ("eng", "rus")
        reader.status = "ready"
        header = "level\tpage_num\tblock_num\tpar_num\tline_num\tword_num\tleft\ttop\twidth\theight\tconf\ttext\n"
        binary = header + "5\t1\t1\t1\t1\t1\t110\t100\t255\t30\t95\tSUPERHUIOBA\n"
        gray = header + (
            "5\t1\t1\t1\t1\t1\t110\t100\t255\t30\t95\tSUPERHUIOBA\n"
            "5\t1\t2\t1\t1\t1\t389\t110\t6\t17\t95\t:\n"
            "5\t1\t3\t1\t1\t1\t424\t100\t100\t30\t95\tпривет\n"
        )
        crop = header + "5\t1\t1\t1\t1\t1\t4\t4\t80\t20\t95\tпривет\n"
        responses = [
            type("Result", (), {"returncode": 0, "stdout": data.encode(), "stderr": b""})()
            for data in (binary, gray, crop)
        ]
        with patch("dota2_map_assistant.text_recognition.subprocess.run", side_effect=responses):
            result = reader.read(Image.new("RGB", (800, 200), "black"))

        self.assertEqual([item.text for item in result], ["привет"])

    def test_reader_recognizes_message_crop_without_translating_player_name(self) -> None:
        reader = OCRReader.__new__(OCRReader)
        reader.available = True
        reader._tesseract = "tesseract"
        reader.languages = ("eng", "rus")
        reader.status = "ready"
        header = "level\tpage_num\tblock_num\tpar_num\tline_num\tword_num\tleft\ttop\twidth\theight\tconf\ttext\n"
        layout = header + (
            "5\t1\t1\t1\t1\t1\t10\t40\t30\t20\t90\t[Guild]\n"
            "5\t1\t2\t1\t1\t1\t45\t40\t35\t20\t90\tPlayer\n"
            "5\t1\t3\t1\t1\t1\t82\t40\t6\t20\t90\t:\n"
            "5\t1\t4\t1\t1\t1\t92\t40\t35\t20\t90\tолуди\n"
        )
        crop = header + "5\t1\t1\t1\t1\t1\t5\t5\t35\t15\t93\tолухи\n"
        results = [
            type("Result", (), {"returncode": 0, "stdout": data.encode(), "stderr": b""})()
            for data in (layout, header, header, crop)
        ]

        with patch("dota2_map_assistant.text_recognition.subprocess.run", side_effect=results) as run:
            observations = reader.read(Image.new("RGB", (200, 60), "black"))

        self.assertEqual([item.text for item in observations], ["олухи"])
        self.assertEqual(run.call_count, 4)
        self.assertEqual(run.call_args.args[0][run.call_args.args[0].index("--psm") + 1], "7")

    def test_dota_chat_tsv_rejoins_fragments_by_visual_row_and_keeps_only_message(self) -> None:
        header = "level\tpage_num\tblock_num\tpar_num\tline_num\tword_num\tleft\ttop\twidth\theight\tconf\ttext\n"
        rows = [
            (1, 116, 65, 183, 23, 55, "[L.E.G.E.N.D.A]"),
            (1, 307, 66, 128, 17, 82, "Pro100Belka"),
            (1, 443, 56, 88, 32, 88, "[*Val*]"),
            (2, 522, 52, 11, 41, 87, ";"),
            (3, 542, 55, 43, 46, 92, "дав"),
            (3, 595, 65, 60, 12, 75, "трон"),
            (4, 116, 99, 183, 23, 55, "[L.E.G.E.N.D.A]"),
            (4, 307, 99, 128, 17, 82, "Pro100Belka"),
            (4, 443, 99, 88, 32, 88, "[*Val*]"),
            (5, 547, 99, 63, 30, 60, "олухи"),
        ]
        tsv = header + "".join(
            f"5\t1\t{block}\t1\t1\t1\t{left}\t{top}\t{width}\t{height}\t{conf}\t{text}\n"
            for block, left, top, width, height, conf, text in rows
        )

        messages = parse_dota_chat_tsv(tsv, observed_at=7.0)

        self.assertEqual([message.observation.text for message in messages], ["да в трон", "олухи"])
        self.assertEqual(messages[0].left, 542)
        self.assertEqual(messages[1].left, 547)

    def test_short_message_after_player_colon_is_accepted(self) -> None:
        header = "level\tpage_num\tblock_num\tpar_num\tline_num\tword_num\tleft\ttop\twidth\theight\tconf\ttext\n"
        tsv = header + (
            "5\t1\t1\t1\t1\t1\t10\t20\t40\t16\t95\tRen:\n"
            "5\t1\t2\t1\t1\t1\t55\t20\t25\t16\t94\tgo\n"
        )

        messages = parse_dota_chat_tsv(tsv, observed_at=7.0)

        self.assertEqual([message.observation.text for message in messages], ["go"])

    def test_chat_wheel_arrow_is_excluded_before_ocr_strips_player_prefix(self) -> None:
        header = "level\tpage_num\tblock_num\tpar_num\tline_num\tword_num\tleft\ttop\twidth\theight\tconf\ttext\n"
        rows = [
            (1, 20, "Ren:"), (1, 60, "▶"), (1, 90, "я"), (1, 110, "только"),
            (2, 20, "▶"), (2, 45, "Ren:"), (2, 90, "я"), (2, 110, "только"),
            (3, 20, "Ren:"), (3, 60, "я"), (3, 90, "только"),
        ]
        tsv = header + "".join(
            f"5\t1\t{line}\t1\t1\t1\t{left}\t{line * 30}\t25\t15\t95\t{text}\n"
            for line, left, text in rows
        )
        self.assertEqual([item.observation.text for item in parse_dota_chat_tsv(tsv)], ["я только"])

    def test_refinement_discards_message_if_crop_reveals_hidden_wheel_arrow(self) -> None:
        reader = OCRReader.__new__(OCRReader)
        reader.languages = ("rus", "eng")
        header = "level\tpage_num\tblock_num\tpar_num\tline_num\tword_num\tleft\ttop\twidth\theight\tconf\ttext\n"
        tsv = header + (
            "5\t1\t1\t1\t1\t1\t0\t10\t15\t15\t90\t▶\n"
            "5\t1\t1\t1\t1\t2\t20\t10\t20\t15\t90\tя\n"
            "5\t1\t1\t1\t1\t3\t45\t10\t55\t15\t90\tтолько\n"
        )
        reader._run_tesseract_tsv = lambda *_args, **_kwargs: tsv
        candidate = ChatMessageCandidate(TextObservation("я только", "rus", 0.9, 1.0),
                                         10, 10, 110, 30, True, 5)
        self.assertEqual(reader._read_chat_messages(Image.new("RGB", (200, 60)), [candidate], 1), [])

    def test_message_joined_to_player_token_does_not_recrop_name(self) -> None:
        reader = OCRReader.__new__(OCRReader)
        reader.available = True
        reader._tesseract = "tesseract"
        reader.languages = ("eng", "rus")
        reader.status = "ready"
        header = "level\tpage_num\tblock_num\tpar_num\tline_num\tword_num\tleft\ttop\twidth\theight\tconf\ttext\n"
        layout = header + "5\t1\t1\t1\t1\t1\t10\t20\t100\t20\t95\tRen:go\n"
        result = type("Result", (), {"returncode": 0, "stdout": layout.encode(), "stderr": b""})()

        with patch("dota2_map_assistant.text_recognition.subprocess.run", return_value=result) as run:
            observations = reader.read(Image.new("RGB", (200, 60), "black"))

        self.assertEqual([item.text for item in observations], ["go"])
        self.assertEqual(run.call_count, 2)

    def test_unpaired_player_prefix_is_not_translated_as_chat(self) -> None:
        reader = OCRReader.__new__(OCRReader)
        reader.available = True
        reader._tesseract = "tesseract"
        reader.languages = ("eng", "rus")
        reader.status = "ready"
        header = "level\tpage_num\tblock_num\tpar_num\tline_num\tword_num\tleft\ttop\twidth\theight\tconf\ttext\n"
        layout = header + (
            "5\t1\t1\t1\t1\t1\t10\t20\t30\t20\t90\t[Guild]\n"
            "5\t1\t2\t1\t1\t1\t45\t20\t90\t20\t90\tPro100Belka\n"
        )
        result = type("Result", (), {"returncode": 0, "stdout": layout.encode(), "stderr": b""})()

        with patch("dota2_map_assistant.text_recognition.subprocess.run", return_value=result):
            observations = reader.read(Image.new("RGB", (200, 60), "black"))

        self.assertEqual(observations, [])

    def test_semicolon_in_message_is_not_mistaken_for_player_separator(self) -> None:
        header = "level\tpage_num\tblock_num\tpar_num\tline_num\tword_num\tleft\ttop\twidth\theight\tconf\ttext\n"
        tsv = header + (
            "5\t1\t1\t1\t1\t1\t0\t20\t30\t15\t95\tgo\n"
            "5\t1\t1\t1\t1\t2\t35\t20\t35\t15\t95\tmid;\n"
            "5\t1\t1\t1\t1\t3\t75\t20\t30\t15\t95\tnow\n"
        )

        self.assertEqual(parse_dota_chat_tsv(tsv), [])

    def test_common_russian_call_recovers_space_after_ocr_joins_words(self) -> None:
        header = "level\tpage_num\tblock_num\tpar_num\tline_num\tword_num\tleft\ttop\twidth\theight\tconf\ttext\n"
        tsv = header + (
            "5\t1\t1\t1\t1\t1\t10\t20\t30\t15\t95\tRen:\n"
            "5\t1\t1\t1\t1\t2\t45\t20\t90\t15\t95\tдавтрон\n"
        )

        self.assertEqual([item.observation.text for item in parse_dota_chat_tsv(tsv)], ["да в трон"])

    def test_russian_chat_ocr_repairs_joined_or_extra_letters(self) -> None:
        header = "level\tpage_num\tblock_num\tpar_num\tline_num\tword_num\tleft\ttop\twidth\theight\tconf\ttext\n"
        source = ["ятолько за", "дате кажется", "со мной спректру закидывают", "и ниразу ейне проебал", "олуди", "и ниразуей не проебал", "нугерой такой да"]
        tsv = header + "".join(
            f"5\t1\t{i}\t1\t1\t1\t10\t{i * 50}\t30\t20\t95\tRen:\n"
            f"5\t1\t{i}\t1\t1\t2\t45\t{i * 50}\t230\t20\t95\t{message}\n"
            for i, message in enumerate(source, 1)
        )

        self.assertEqual(
            [item.observation.text for item in parse_dota_chat_tsv(tsv)],
            ["я только за", "да те кажется", "со мной спектру закидывают", "и ниразу ей не проебал", "олухи", "и ниразу ей не проебал", "ну герой такой да"],
        )

    def test_unstructured_russian_fragments_are_not_emitted(self) -> None:
        reader = OCRReader.__new__(OCRReader)
        reader.available = True
        reader._tesseract = "tesseract"
        reader.languages = ("eng", "rus")
        reader.status = "ready"
        header = "level\tpage_num\tblock_num\tpar_num\tline_num\tword_num\tleft\ttop\twidth\theight\tconf\ttext\n"

        def result(lines):
            rows = "".join(
                f"5\t1\t{index}\t1\t1\t1\t0\t{index * 20}\t80\t10\t95.0\t{text}\n"
                for index, text in enumerate(lines, 1)
            )
            return type("Result", (), {"returncode": 0, "stdout": (header + rows).encode(), "stderr": b""})()

        with patch("dota2_map_assistant.text_recognition.subprocess.run", side_effect=[
            result(["Ren", "Ren", "Ren", "Ren"]),
            result(["Ren", "Ren", "Ren", "Ren", "нет надо там", "моментик", "ana", "sorry for sf"]),
            result(["ж Ren", "нет надо там", "моментик", "для", "sorry for sf"]),
        ]) as run:
            observations = reader.read(Image.new("RGB", (20, 10), "black"))

        self.assertEqual(observations, [])
        self.assertEqual(run.call_count, 3)

    def test_player_name_only_result_does_not_emit_unpaired_message(self) -> None:
        reader = OCRReader.__new__(OCRReader)
        reader.available = True
        reader._tesseract = "tesseract"
        reader.languages = ("eng", "rus")
        reader.status = "ready"
        header = "level\tpage_num\tblock_num\tpar_num\tline_num\tword_num\tleft\ttop\twidth\theight\tconf\ttext\n"
        name = "5\t1\t1\t1\t1\t1\t0\t0\t30\t10\t97.0\tRen\n"
        message = "5\t1\t2\t1\t1\t1\t35\t0\t80\t10\t95.0\tsorry for sf\n"
        binary = type("Result", (), {"returncode": 0, "stdout": (header + name).encode(), "stderr": b""})()
        grayscale = type("Result", (), {"returncode": 0, "stdout": (header + name + message).encode(), "stderr": b""})()

        with patch("dota2_map_assistant.text_recognition.subprocess.run", side_effect=[binary, grayscale]) as run:
            observations = reader.read(Image.new("RGB", (20, 10), "black"))

        self.assertEqual(observations, [])
        self.assertEqual(run.call_count, 3)

    def test_repeated_short_player_label_is_removed_when_messages_are_present(self) -> None:
        observations = [TextObservation("Ren", "eng", 0.95, 1.0) for _ in range(4)]
        observations += [
            TextObservation("sorry for sf", "eng", 0.95, 1.0),
            TextObservation("просто ульт", "rus", 0.95, 1.0),
        ]

        self.assertEqual(
            [item.text for item in consolidate_observations(observations)],
            ["sorry for sf", "просто ульт"],
        )

    def test_low_contrast_unpaired_text_is_not_emitted(self) -> None:
        reader = OCRReader.__new__(OCRReader)
        reader.available = True
        reader._tesseract = "tesseract"
        reader.languages = ("eng", "rus")
        reader.status = "ready"
        image = Image.new("RGB", (3, 1))
        image.putdata([(20, 40, 60), (40, 80, 120), (60, 120, 180)])
        header = "level\tpage_num\tblock_num\tpar_num\tline_num\tword_num\tleft\ttop\twidth\theight\tconf\ttext\n"
        message = "5\t1\t1\t1\t1\t1\t0\t0\t30\t10\t95.0\tпросто ульт\n"
        empty = type("Result", (), {"returncode": 0, "stdout": header.encode(), "stderr": b""})()
        recognized = type("Result", (), {"returncode": 0, "stdout": (header + message).encode(), "stderr": b""})()

        with patch("dota2_map_assistant.text_recognition.subprocess.run", side_effect=[empty, recognized]) as run:
            observations = reader.read(image)

        self.assertEqual(observations, [])
        self.assertEqual(run.call_count, 3)
        grayscale_payload = run.call_args_list[1].kwargs["input"]
        self.assertTrue(any(0 < pixel < 255 for pixel in Image.open(io.BytesIO(grayscale_payload)).getdata()))

    def test_dota_chat_preprocessing_separates_bright_text_from_scene(self) -> None:
        image = Image.new("RGB", (2, 1))
        image.putdata([(75, 90, 80), (250, 250, 245)])

        prepared = _prepare_image(image)

        self.assertEqual(set(prepared.getdata()), {0, 255})

    def test_ocr_reader_uses_sparse_text_segmentation(self) -> None:
        reader = OCRReader.__new__(OCRReader)
        reader.available = True
        reader._tesseract = "tesseract"
        reader.languages = ("eng", "rus")
        reader.status = "ready"
        result = type("Result", (), {"returncode": 0, "stdout": b"", "stderr": b""})()

        with patch("dota2_map_assistant.text_recognition.subprocess.run", return_value=result) as run:
            reader.read(Image.new("RGB", (20, 10), "black"))

        self.assertEqual(run.call_args_list[0].args[0][run.call_args_list[0].args[0].index("--psm") + 1], "11")

    def test_parse_tesseract_tsv_groups_words_into_an_observation(self) -> None:
        tsv = (
            "level\tpage_num\tblock_num\tpar_num\tline_num\tword_num\tleft\ttop\twidth\theight\tconf\ttext\n"
            "5\t1\t1\t1\t1\t1\t0\t0\t20\t10\t95.0\tHello\n"
            "5\t1\t1\t1\t1\t2\t22\t0\t20\t10\t95.0\tworld\n"
        )

        observations = parse_tesseract_tsv(tsv, observed_at=7.0)

        self.assertEqual(len(observations), 1)
        self.assertEqual(observations[0].text, "Hello world")
        self.assertEqual(observations[0].language, "eng")
        self.assertEqual(observations[0].observed_at, 7.0)

    def test_normalize_text_collapses_ocr_whitespace(self) -> None:
        self.assertEqual(normalize_text("  Hello\n   мир  "), "Hello мир")

    def test_low_confidence_ocr_noise_is_not_emitted(self) -> None:
        header = "level\tpage_num\tblock_num\tpar_num\tline_num\tword_num\tleft\ttop\twidth\theight\tconf\ttext\n"
        rows = (
            "5\t1\t1\t1\t1\t1\t0\t0\t20\t10\t42.0\tZ>\n"
            "5\t1\t2\t1\t1\t1\t0\t20\t20\t10\t95.0\tHello\n"
            "5\t1\t2\t1\t1\t2\t22\t20\t20\t10\t95.0\tworld\n"
        )

        observations = parse_tesseract_tsv(header + rows, observed_at=7.0)

        self.assertEqual([item.text for item in observations], ["Hello world"])

    def test_symbol_noise_before_message_colon_is_removed(self) -> None:
        header = "level\tpage_num\tblock_num\tpar_num\tline_num\tword_num\tleft\ttop\twidth\theight\tconf\ttext\n"
        row = "5\t1\t1\t1\t1\t1\t0\t0\t80\t10\t85.0\twy &%* : Привет, как дела?\n"

        observations = parse_tesseract_tsv(header + row, observed_at=7.0)

        self.assertEqual([item.text for item in observations], ["Привет, как дела?"])

    def test_repeated_chat_lines_choose_clearer_ocr_version(self) -> None:
        noisy = TextObservation("wy EX : Вы, стайка поросят, идите домой траву есть", "rus", 0.73, 1.0)
        clear = TextObservation("Вы, стайка поросят, идите домой траву есть", "rus", 0.91, 1.0)

        consolidated = consolidate_observations([noisy, clear])

        self.assertEqual(consolidated, [clear])

    def test_repeated_words_do_not_outscore_one_complete_sentence(self) -> None:
        complete = TextObservation("Please push the bottom lane with our team now", "eng", 0.84, 1.0)
        repeated = TextObservation(
            "push the bottom lane with our team now Please push the bottom lane with our team now",
            "eng",
            0.94,
            1.0,
        )

        self.assertEqual(consolidate_observations([complete, repeated]), [complete])

    def test_negation_and_numbers_are_not_treated_as_ocr_duplicates(self) -> None:
        go = TextObservation("Please go to the middle lane at 2 minutes", "eng", 0.9, 1.0)
        do_not_go = TextObservation("Please do not go to the middle lane at 2 minutes", "eng", 0.9, 1.0)
        later = TextObservation("Please go to the middle lane at 3 minutes", "eng", 0.9, 1.0)

        self.assertEqual(consolidate_observations([go, do_not_go, later]), [go, do_not_go, later])

    def test_different_lane_calls_are_not_merged_as_ocr_noise(self) -> None:
        top = TextObservation("Please push top lane with our team now", "eng", 0.9, 1.0)
        bottom = TextObservation("Please push bot lane with our team now", "eng", 0.9, 1.0)

        self.assertEqual(consolidate_observations([top, bottom]), [top, bottom])

    def test_duplicate_is_language_aware(self) -> None:
        dedupe = ObservationDeduplicator()
        item = TextObservation("Hello", "eng", 0.9, 1.0)

        self.assertFalse(dedupe.is_duplicate(item))
        self.assertTrue(dedupe.is_duplicate(TextObservation(" Hello ", "eng", 0.8, 2.0)))
        self.assertFalse(dedupe.is_duplicate(TextObservation("Hello", "rus", 0.8, 3.0)))


if __name__ == "__main__":
    unittest.main()
