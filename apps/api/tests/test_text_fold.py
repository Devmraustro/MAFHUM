"""The shared Arabic folding contract.

Document search and the research engine both delegate here, so these tests pin the
equivalences a student actually relies on. If a rule is ever added to one of them
and not the other, it now lives in one place and fails here once.
"""

from __future__ import annotations

import pytest

from mafhum_api.services.research.grounding import _normalize as grounding_normalise
from mafhum_api.services.text.fold import fold_arabic


@pytest.mark.parametrize(
    ("written", "typed"),
    [
        ("أحمد", "احمد"),
        ("إبراهيم", "ابراهيم"),
        ("آمنة", "امنه"),
        ("مصطفى", "مصطفي"),
        ("قضية", "قضيه"),
        ("مؤمن", "مومن"),
        ("مسؤول", "مسوول"),
    ],
)
def test_letter_variants_fold_together(written: str, typed: str) -> None:
    assert fold_arabic(written) == fold_arabic(typed)


def test_diacritics_and_tatweel_are_removed() -> None:
    # "العَلميَّة" vowelled, "العلمية" plain, "العـلمية" with tatweel.
    assert fold_arabic("العَلميَّة") == fold_arabic("العلمية") == fold_arabic("العـلمية")


def test_french_case_and_accents_are_folded_for_ascii_only() -> None:
    # Accents are NOT stripped: "cours" and "cœur" are different words, and
    # silently merging them would return passages the student did not ask for.
    assert fold_arabic("Étude") == fold_arabic("étude")
    assert fold_arabic("cœur") != fold_arabic("coeur")


def test_normalisation_is_idempotent() -> None:
    once = fold_arabic("الأَسْئِلَةُ")
    assert fold_arabic(once) == once


def test_the_engine_and_search_agree() -> None:
    """The whole point of the shared module."""
    sample = "الإجابةُ على الأسئلة المتكرّرة"
    assert grounding_normalise(sample) == fold_arabic(sample)


def test_ocr_capability_is_reported_honestly() -> None:
    """The UI tells students that a scan is not searchable; that must stay true.

    If someone ships a backend without updating this module, the endpoint would
    keep saying "unavailable" and the test would keep passing while the product
    silently under-claims. The assertion pair below therefore pins both halves:
    no backend ⇒ not available, with a reason a student can read.
    """
    from mafhum_api.services.documents import ocr

    status = ocr.status()
    assert status.available is False
    assert "not implemented" in status.reason
    assert "text layer" in status.reason
