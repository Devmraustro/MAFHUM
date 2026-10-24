"""Unit tests for text normalization, segmentation and language detection.

These lock in the "never silently destroy meaning" rule: Arabic diacritics,
tatweel, zero-width joiners and punctuation must survive normalization.
"""

from __future__ import annotations

from mafhum_api.services.documents.normalizer import (
    contains_arabic,
    detect_language_hint,
    normalize_paragraph,
    normalize_text,
)
from mafhum_api.services.documents.segmenter import segment_paragraphs


class TestNormalization:
    def test_preserves_arabic_diacritics(self) -> None:
        text = "بِسْمِ اللَّهِ الرَّحْمَٰنِ الرَّحِيمِ"
        assert normalize_text(text) == text

    def test_preserves_tatweel(self) -> None:
        text = "عـــربـي"
        assert normalize_text(text) == text

    def test_preserves_zero_width_joiners(self) -> None:
        text = "لا\u200dإله"
        assert "\u200c" not in text  # sanity
        assert normalize_text(text) == text

    def test_preserves_arabic_punctuation(self) -> None:
        text = "السؤال؟ الجواب؛ نعم، صحيح."
        assert normalize_text(text) == text

    def test_preserves_french_accents(self) -> None:
        text = "Étude sur l'efficacité des systèmes — résumé."
        assert normalize_text(text) == text

    def test_strips_byte_order_mark(self) -> None:
        assert normalize_text("\ufeffنص") == "نص"

    def test_strips_zero_width_space_artefact(self) -> None:
        assert normalize_text("كل\u200bمة") == "كلمة"

    def test_strips_control_characters(self) -> None:
        assert normalize_text("نص\x00\x07\x1b") == "نص"

    def test_normalizes_line_endings(self) -> None:
        assert normalize_text("a\r\nb\rc") == "a\nb\nc"

    def test_collapses_repeated_spaces_but_not_newlines(self) -> None:
        assert normalize_text("a    b\n\n\n\nc") == "a b\n\nc"

    def test_empty_input(self) -> None:
        assert normalize_text("") == ""

    def test_nfc_normalization_is_applied(self) -> None:
        decomposed = "e\u0301tude"
        assert normalize_text(decomposed) == "\u00e9tude"

    def test_paragraph_joins_internal_newlines(self) -> None:
        assert normalize_paragraph("سطر أول\nسطر ثان") == "سطر أول سطر ثان"

    def test_paragraph_drops_whitespace_only(self) -> None:
        assert normalize_paragraph("   \n  ") == ""


class TestLanguageHint:
    def test_arabic_detected(self) -> None:
        assert detect_language_hint("هذا نص عربي واضح") == "ar"

    def test_french_detected(self) -> None:
        assert detect_language_hint("Ceci est un texte francais") == "fr"

    def test_empty_is_undecided(self) -> None:
        assert detect_language_hint("") is None

    def test_digits_only_is_undecided(self) -> None:
        assert detect_language_hint("12345 6789") is None

    def test_mixed_majority_arabic(self) -> None:
        assert detect_language_hint("هذا نص عربي مع كلمة PDF") == "ar"

    def test_contains_helpers(self) -> None:
        assert contains_arabic("عربي")
        assert not contains_arabic("francais")


class TestSegmentation:
    def test_preserves_paragraph_boundaries(self) -> None:
        segments = segment_paragraphs([(1, "الفقرة الأولى"), (1, "الفقرة الثانية")], max_chars=1500)
        assert [s.text for s in segments] == ["الفقرة الأولى", "الفقرة الثانية"]
        assert [s.ordinal for s in segments] == [0, 1]

    def test_records_page_numbers(self) -> None:
        segments = segment_paragraphs([(1, "page one text"), (2, "page two text")], max_chars=1500)
        assert [s.page_number for s in segments] == [1, 2]

    def test_drops_empty_paragraphs_and_keeps_ordinals_contiguous(self) -> None:
        segments = segment_paragraphs([(1, "أ"), (1, "   "), (1, "ب")], max_chars=1500)
        assert [s.ordinal for s in segments] == [0, 1]

    def test_splits_over_long_paragraph_on_sentence_boundary(self) -> None:
        long_text = "جملة كاملة. " * 200
        segments = segment_paragraphs([(1, long_text.strip())], max_chars=200)
        assert len(segments) > 1
        assert all(len(s.text) <= 400 for s in segments)
        assert [s.ordinal for s in segments] == list(range(len(segments)))

    def test_handles_single_token_longer_than_budget(self) -> None:
        blob = "ك" * 5000
        segments = segment_paragraphs([(1, blob)], max_chars=100)
        assert segments
        assert all(len(s.text) <= 200 for s in segments)

    def test_french_sentence_splitting(self) -> None:
        text = "Première phrase du paragraphe. Deuxième phrase du même paragraphe."
        segments = segment_paragraphs([(1, text)], max_chars=40)
        assert len(segments) == 2
        assert segments[0].text.endswith("paragraphe.")

    def test_char_count_matches_text(self) -> None:
        segments = segment_paragraphs([(1, "نص تجريبي")], max_chars=1500)
        assert segments[0].char_count == len(segments[0].text)
