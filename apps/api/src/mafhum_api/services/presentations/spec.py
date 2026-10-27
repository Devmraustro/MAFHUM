"""The typed, bounded deck specification shared by every generator.

The spec is deliberately a plain JSON-serialisable value object. It is the only
thing that crosses the language boundary to the Node benchmark generator, so a
generator cannot reach into a database, a document, or a user record. Nothing in
a spec is generated prose: ``quote`` values are verbatim passages and titles are
either supplied by the student or fixed structural labels.

Bounds here are a security control as much as a formatting rule. A student can
put any string into a title; without caps a single request could ask the
generator to build a deck with unbounded text or a chart with unbounded series.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from typing import Any, Literal

SCHEMA_VERSION = "1.0"

MAX_SLIDES = 12
MAX_TITLE_CHARS = 200
MAX_TEXT_CHARS = 900
MAX_ITEMS_PER_SLIDE = 8
MAX_CITATIONS = 12
MAX_TABLE_ROWS = 14
MAX_CHART_SERIES = 10

DeckLanguage = Literal["ar", "fr"]
SlideKind = Literal["title", "bullets", "quote", "chart", "table"]
JobStatusValue = Literal["queued", "running", "succeeded", "failed"]


class SpecError(ValueError):
    """A spec violated a bound. Converted to a safe client error by the service."""


@dataclass(frozen=True, slots=True)
class DeckCitation:
    """One citable passage. ``available`` is recomputed on read, never trusted."""

    index: int
    quote: str
    locator: str
    title: str | None = None
    page_number: int | None = None
    ordinal: int | None = None
    url: str | None = None
    document_id: str | None = None
    source_id: str | None = None
    available: bool = True


@dataclass(frozen=True, slots=True)
class DeckSlide:
    kind: SlideKind
    title: str
    text: str = ""
    items: tuple[str, ...] = ()
    citation_indices: tuple[int, ...] = ()
    chart_labels: tuple[str, ...] = ()
    chart_values: tuple[int, ...] = ()
    table_headers: tuple[str, ...] = ()
    table_rows: tuple[tuple[str, ...], ...] = ()


@dataclass(frozen=True, slots=True)
class DeckSpec:
    title: str
    subtitle: str
    language: DeckLanguage
    rtl: bool
    draft_notice: str
    slides: tuple[DeckSlide, ...]
    citations: tuple[DeckCitation, ...]
    schema_version: str = SCHEMA_VERSION

    def to_json(self) -> str:
        return json.dumps(asdict(self), ensure_ascii=False, separators=(",", ":"))

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> DeckSpec:
        """Rebuild a spec from a stored JSON blob, validating it again."""
        spec = cls(
            title=str(raw.get("title", "")),
            subtitle=str(raw.get("subtitle", "")),
            language=str(raw.get("language", "ar")),  # type: ignore[arg-type]
            rtl=bool(raw.get("rtl", True)),
            draft_notice=str(raw.get("draft_notice", "")),
            slides=tuple(
                DeckSlide(
                    kind=slide["kind"],
                    title=slide["title"],
                    text=slide.get("text", ""),
                    items=tuple(slide.get("items", ())),
                    citation_indices=tuple(slide.get("citation_indices", ())),
                    chart_labels=tuple(slide.get("chart_labels", ())),
                    chart_values=tuple(slide.get("chart_values", ())),
                    table_headers=tuple(slide.get("table_headers", ())),
                    table_rows=tuple(tuple(row) for row in slide.get("table_rows", ())),
                )
                for slide in raw.get("slides", ())
            ),
            citations=tuple(
                DeckCitation(
                    index=int(c["index"]),
                    quote=c.get("quote", ""),
                    locator=c.get("locator", ""),
                    title=c.get("title"),
                    page_number=c.get("page_number"),
                    ordinal=c.get("ordinal"),
                    url=c.get("url"),
                    document_id=c.get("document_id"),
                    source_id=c.get("source_id"),
                    available=bool(c.get("available", True)),
                )
                for c in raw.get("citations", ())
            ),
            schema_version=str(raw.get("schema_version", SCHEMA_VERSION)),
        )
        spec.validate()
        return spec

    def validate(self) -> None:
        """Reject anything that could make generation unbounded or malformed."""
        if self.language not in ("ar", "fr"):
            raise SpecError("unsupported language")
        if self.rtl != (self.language == "ar"):
            raise SpecError("rtl must match the language")
        if not 1 <= len(self.title) <= MAX_TITLE_CHARS:
            raise SpecError("title length out of bounds")
        if len(self.subtitle) > MAX_TITLE_CHARS or len(self.draft_notice) > MAX_TITLE_CHARS:
            raise SpecError("subtitle/draft notice length out of bounds")
        if not 1 <= len(self.slides) <= MAX_SLIDES:
            raise SpecError("slide count out of bounds")
        if len(self.citations) > MAX_CITATIONS:
            raise SpecError("citation count out of bounds")
        if [c.index for c in self.citations] != list(range(1, len(self.citations) + 1)):
            raise SpecError("citation indices must be contiguous from 1")
        for slide in self.slides:
            if slide.kind not in ("title", "bullets", "quote", "chart", "table"):
                raise SpecError("unknown slide kind")
            if not 1 <= len(slide.title) <= MAX_TITLE_CHARS:
                raise SpecError("slide title length out of bounds")
            if len(slide.text) > MAX_TEXT_CHARS:
                raise SpecError("slide text length out of bounds")
            if len(slide.items) > MAX_ITEMS_PER_SLIDE:
                raise SpecError("too many bullet items")
            if any(len(item) > MAX_TEXT_CHARS for item in slide.items):
                raise SpecError("bullet item length out of bounds")
            if slide.table_rows and len(slide.table_rows) > MAX_TABLE_ROWS:
                raise SpecError("too many table rows")
            if slide.chart_labels and len(slide.chart_labels) > MAX_CHART_SERIES:
                raise SpecError("too many chart series")
            if len(slide.chart_labels) != len(slide.chart_values):
                raise SpecError("chart labels and values must have equal length")
            if slide.kind in ("quote", "chart") and not slide.citation_indices:
                raise SpecError("evidence slides must name their citations")
            if any(index < 1 or index > len(self.citations) for index in slide.citation_indices):
                raise SpecError("slide references an unknown citation")


__all__ = [
    "MAX_SLIDES",
    "MAX_TITLE_CHARS",
    "DeckCitation",
    "DeckSlide",
    "DeckSpec",
    "SpecError",
]
