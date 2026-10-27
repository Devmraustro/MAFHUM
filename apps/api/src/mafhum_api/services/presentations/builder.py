"""Build a deck specification from persisted evidence.

This module never writes academic prose. Every quotation on an evidence slide is
copied verbatim from a stored citation; the chart counts real citations per
document; the table lists real locators. The remaining strings are fixed
structural labels in the requested language ("plan", "references", "draft").

That constraint is the whole point: a slide that looks authoritative must not
contain a sentence the model invented.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import cast

from mafhum_api.services.presentations.spec import (
    MAX_CHART_SERIES,
    MAX_TABLE_ROWS,
    MAX_TEXT_CHARS,
    DeckCitation,
    DeckSlide,
    DeckSpec,
    SpecError,
)

MAX_EVIDENCE_SLIDES = 3

_LABELS: dict[str, dict[str, object]] = {
    "ar": {
        "subtitle": "MAFHUM — عرض مستند إلى أدلة من ملفاتك",
        "draft": "مسودة للمراجعة — تحقق من المصادر قبل تقديم العرض",
        "plan_title": "خطة العرض",
        "plan": (
            "عرض السؤال والمادة المستند إليها",
            "مقاطع مقتبسة حرفيًا مع موضعها",
            "توزيع الاقتباسات على المستندات",
            "قائمة المراجع للتحقق",
        ),
        "evidence_title": "الدليل {index}",
        "chart_title": "توزيع الاقتباسات حسب المستند",
        "chart_series": "الاقتباسات",
        "table_title": "المراجع",
        "table_headers": ("#", "المصدر", "الموضع", "الحالة"),
        "unknown_source": "مصدر غير مُسمّى",
        "unknown_locator": "موضع غير معروف",
        "available": "متاح",
        "unavailable": "المصدر محذوف",
        "source_label": "مأخوذ حرفيًا من:",
    },
    "fr": {
        "subtitle": "MAFHUM — présentation fondée sur vos documents",
        "draft": "Brouillon à vérifier — contrôlez les sources avant de présenter",
        "plan_title": "Plan de la présentation",
        "plan": (
            "Question et matériel utilisé",
            "Extraits cités textuellement avec leur localisation",
            "Répartition des citations par document",
            "Liste des références à vérifier",
        ),
        "evidence_title": "Preuve {index}",
        "chart_title": "Répartition des citations par document",
        "chart_series": "Citations",
        "table_title": "Références",
        "table_headers": ("#", "Source", "Localisation", "État"),
        "unknown_source": "source non nommée",
        "unknown_locator": "localisation inconnue",
        "available": "disponible",
        "unavailable": "source supprimée",
        "source_label": "Extrait textuel de :",
    },
}


def labels(language: str) -> dict[str, object]:
    return _LABELS["ar" if language == "ar" else "fr"]


def _clip(text: str, limit: int = MAX_TEXT_CHARS) -> str:
    if len(text) <= limit:
        return text
    return text[: limit - 1].rstrip() + "…"


def build_deck_spec(
    *,
    title: str,
    question: str,
    citations: Sequence[DeckCitation],
    language: str,
) -> DeckSpec:
    """Compose a 4–6 slide deck from grounded citations.

    ``citations`` must already be verified as available by the caller. Passing an
    unavailable citation here is a programming error, not something to hide in a
    footnote: a deck must not present a deleted source as citable evidence.
    """
    if language not in ("ar", "fr"):
        raise SpecError("unsupported language")
    if not citations:
        raise SpecError("a deck needs at least one citation")
    if any(not citation.available for citation in citations):
        raise SpecError("unavailable citations cannot be put in a deck")

    text = labels(language)
    rtl = language == "ar"
    indices = tuple(c.index for c in citations)
    slides: list[DeckSlide] = [
        DeckSlide(kind="title", title=_clip(title)),
        DeckSlide(
            kind="bullets",
            title=str(text["plan_title"]),
            items=tuple(str(item) for item in cast("tuple[str, ...]", text["plan"])),
        ),
    ]

    for citation in citations[:MAX_EVIDENCE_SLIDES]:
        locator = citation.locator or str(text["unknown_locator"])
        slides.append(
            DeckSlide(
                kind="quote",
                title=str(text["evidence_title"]).format(index=citation.index),
                text=_clip(f"« {citation.quote} »"),
                items=(f"{text['source_label']} {locator}",),
                citation_indices=(citation.index,),
            )
        )

    # Counts are derived from the actual citations, not estimated. The chart is
    # a real editable chart object, so a student can correct it if they wish.
    counts: dict[str, int] = {}
    for citation in citations:
        key = _clip(citation.title or str(text["unknown_source"]), 40)
        counts[key] = counts.get(key, 0) + 1
    ordered = sorted(counts.items(), key=lambda pair: (-pair[1], pair[0]))[:MAX_CHART_SERIES]
    slides.append(
        DeckSlide(
            kind="chart",
            title=str(text["chart_title"]),
            chart_labels=tuple(label for label, _ in ordered),
            chart_values=tuple(value for _, value in ordered),
            citation_indices=indices,
        )
    )

    headers: tuple[str, ...] = tuple(str(h) for h in cast("tuple[str, ...]", text["table_headers"]))
    rows: list[tuple[str, ...]] = []
    for citation in citations[:MAX_TABLE_ROWS]:
        rows.append(
            (
                str(citation.index),
                _clip(citation.title or str(text["unknown_source"]), 60),
                _clip(citation.locator or str(text["unknown_locator"]), 60),
                str(text["available"] if citation.available else text["unavailable"]),
            )
        )
    slides.append(
        DeckSlide(
            kind="table",
            title=str(text["table_title"]),
            table_headers=headers,
            table_rows=tuple(rows),
            citation_indices=indices,
        )
    )

    return DeckSpec(
        title=_clip(title),
        subtitle=f"{text['subtitle']} — {_clip(question, 80)}",
        language="ar" if rtl else "fr",
        rtl=rtl,
        draft_notice=str(text["draft"]),
        slides=tuple(slides),
        citations=tuple(citations),
    )


__all__ = ["MAX_EVIDENCE_SLIDES", "build_deck_spec", "labels"]
