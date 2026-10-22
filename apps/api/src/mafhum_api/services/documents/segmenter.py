"""Paragraph-preserving segmentation.

Segments keep paragraph boundaries where the parser provided them, and record
the page they came from. A paragraph longer than ``max_chars`` is split on
sentence boundaries first and, failing that, on whitespace, so retrieval can
later cite a bounded span without cutting mid-word.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass

from mafhum_api.services.documents.normalizer import normalize_paragraph

# Arabic and Latin sentence terminators, both followed by whitespace or EOL.
_SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?…؟؛])\s+(?=[\u0600-\u06ffA-Za-z0-9(\[«\"'“])")
_MAX_SENTENCE_LEN = 1200


@dataclass(frozen=True, slots=True)
class Segment:
    ordinal: int
    page_number: int | None
    text: str

    @property
    def char_count(self) -> int:
        return len(self.text)


def segment_paragraphs(
    paragraphs: Sequence[tuple[int | None, str]],
    *,
    max_chars: int,
) -> list[Segment]:
    """Split normalized ``(page, paragraph)`` pairs into bounded segments."""
    segments: list[Segment] = []
    ordinal = 0
    for page_number, raw in paragraphs:
        text = normalize_paragraph(raw)
        if not text:
            continue
        pieces = [text] if len(text) <= max_chars else _split_long(text, max_chars)
        for piece in pieces:
            piece = piece.strip()
            if not piece:
                continue
            segments.append(Segment(ordinal=ordinal, page_number=page_number, text=piece))
            ordinal += 1
    return segments


def _split_long(text: str, max_chars: int) -> list[str]:
    """Break an over-long paragraph on sentence then word boundaries."""
    sentences: list[str] = []
    for sentence in _SENTENCE_SPLIT_RE.split(text):
        sentence = sentence.strip()
        if not sentence:
            continue
        if len(sentence) <= max_chars:
            sentences.append(sentence)
        else:
            sentences.extend(_split_words(sentence, max_chars))
    return _pack(sentences, max_chars)


def _split_words(text: str, max_chars: int) -> list[str]:
    chunks: list[str] = []
    for word in text.split():
        while len(word) > max_chars:
            chunks.append(word[:max_chars])
            word = word[max_chars:]
        if word:
            chunks.append(word)
    return chunks


def _pack(pieces: Sequence[str], max_chars: int) -> list[str]:
    """Greedily pack pieces up to ``max_chars`` without splitting any of them."""
    packed: list[str] = []
    current: list[str] = []
    current_len = 0
    for piece in pieces:
        added = len(piece) + (1 if current else 0)
        if current and current_len + added > max_chars:
            packed.append(" ".join(current))
            current = [piece]
            current_len = len(piece)
        else:
            current.append(piece)
            current_len += added
    if current:
        packed.append(" ".join(current))
    # Guard against pathological single tokens longer than the budget.
    out: list[str] = []
    for item in packed:
        while len(item) > max_chars * 2:
            out.append(item[:max_chars])
            item = item[max_chars:]
        out.append(item)
    return out
