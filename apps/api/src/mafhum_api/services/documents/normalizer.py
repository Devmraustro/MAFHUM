"""Conservative text normalization.

Hard rule: **never silently destroy meaning.** Arabic diacritics (harakat),
tatweel, punctuation, and the zero-width joiners used in Arabic/Persian/French
typography are all preserved. What we do remove is genuinely inert: byte-order
marks, C0/C1 control characters, and the U+200B zero-width *space* that PDF text
extractors commonly emit between words.

Everything here is deterministic and reversible-in-spirit: no transliteration,
no case folding, no diacritic stripping, no "smart" rewriting.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

# Preserved deliberately (documented so a future editor does not "clean" them):
#   U+0640 ARABIC TATWEEL
#   U+064B..U+0652, U+0670 harakat / superscript alef
#   U+200C ZERO WIDTH NON-JOINER, U+200D ZERO WIDTH JOINER
#   U+060C ARABIC COMMA, U+061B ARABIC SEMICOLON, U+061F ARABIC QUESTION MARK
_REMOVED_CHARS = "".join(
    [
        "\ufeff",  # BOM
        "\u200b",  # zero width space (PDF artefact)
        "\u200e",  # LRM
        "\u200f",  # RLM
        "\u00ad",  # soft hyphen
    ]
)
_REMOVE_RE = re.compile(f"[{re.escape(_REMOVED_CHARS)}]")
# C0 controls except tab/newline/CR, plus DEL and C1.
_CONTROL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f-\x9f]")
_SPACES_RE = re.compile(r"[ \t\u00a0\u2007\u202f]{2,}")
_BLANK_LINES_RE = re.compile(r"\n{3,}")


@dataclass(frozen=True, slots=True)
class NormalizationStats:
    input_chars: int
    output_chars: int
    removed_chars: int

    @property
    def removal_ratio(self) -> float:
        return self.removed_chars / self.input_chars if self.input_chars else 0.0


def normalize_text(text: str) -> str:
    """Normalize a single extracted string, preserving script semantics."""
    if not text:
        return ""
    # NFC so composed Arabic/French characters have one canonical representation;
    # NFC (not NFKC) avoids folding ligatures or full-width forms.
    result = unicodedata.normalize("NFC", text)
    result = _REMOVE_RE.sub("", result)
    result = _CONTROL_RE.sub("", result)
    result = result.replace("\r\n", "\n").replace("\r", "\n")
    result = _SPACES_RE.sub(" ", result)
    result = _BLANK_LINES_RE.sub("\n\n", result)
    return result.strip()


def normalize_paragraph(text: str) -> str:
    """Normalize one paragraph and collapse its internal newlines."""
    normalized = normalize_text(text)
    if not normalized:
        return ""
    return " ".join(part.strip() for part in normalized.split("\n") if part.strip())


def contains_arabic(text: str) -> bool:
    return any("\u0600" <= ch <= "\u06ff" or "\u0750" <= ch <= "\u077f" for ch in text)


def contains_latin(text: str) -> bool:
    return any(
        ("a" <= ch <= "z") or ("A" <= ch <= "Z") or "\u00c0" <= ch <= "\u024f" for ch in text
    )


def detect_language_hint(text: str) -> str | None:
    """Return a coarse ``ar`` / ``fr`` hint, or ``None`` when undecidable.

    This is a hint for UI direction and sorting only. It is never used to
    rewrite text, and an undecidable result stays ``None`` rather than defaulting
    to a fabricated language.
    """
    if not text:
        return None
    arabic = sum(1 for ch in text if "\u0600" <= ch <= "\u06ff")
    latin = sum(1 for ch in text if ("a" <= ch <= "z") or ("A" <= ch <= "Z"))
    total = arabic + latin
    if total == 0:
        return None
    if arabic / total >= 0.5:
        return "ar"
    if latin / total >= 0.5:
        return "fr"
    return None
