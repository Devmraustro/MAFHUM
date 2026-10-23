"""Grounded answers built only from evidence that actually exists.

The central rule: **an answer is a selection of real passages, never a
generation.** Every claim the endpoint returns is a verbatim quotation of a
segment that is in the database, carrying the document, page and ordinal it came
from. There is no code path that can invent a citation, a page number or a
quotation, because nothing here produces text that was not retrieved.

When the evidence does not cover the question, the engine says so and returns
nothing else. "Insufficient evidence" is a first-class answer, not a failure to
be papered over with plausible prose.

Retrieval is lexical and language-aware rather than semantic: there is no
embedding model in the default install, and a lexical scorer that can be read
and audited is preferable to a black box whose mistakes look like confidence.
Arabic is normalised before matching — diacritics removed and the
alef/ya/ta-marbuta variants folded — because a query written without tashkeel
must still find a document written with it.
"""

from __future__ import annotations

import itertools
import re
import unicodedata
import uuid
from dataclasses import dataclass, field

# Arabic diacritics (harakat, tanwin, shadda, sukun, superscript alef) and
# tatweel. Removing them lets an unvowelled query match a vowelled document.
_ARABIC_DIACRITICS = dict.fromkeys(
    map(ord, "\u064b\u064c\u064d\u064e\u064f\u0650\u0651\u0652\u0670\u0640")
)

_ARABIC_FOLD = {
    ord("أ"): "ا",
    ord("إ"): "ا",
    ord("آ"): "ا",
    ord("ٱ"): "ا",
    ord("ى"): "ي",
    ord("ئ"): "ي",
    ord("ة"): "ه",
    ord("ؤ"): "و",
}

_TOKEN_RE = re.compile(r"[^\W\d_]+", re.UNICODE)

# Below this fraction of the question's content terms being present, the evidence
# is declared insufficient rather than stretched.
MIN_COVERAGE = 0.34
# A single segment must overlap the question by at least this much to be cited.
MIN_SEGMENT_SCORE = 0.2

ARABIC_RANGE = re.compile(r"[\u0600-\u06ff]")


@dataclass(frozen=True, slots=True)
class Evidence:
    """One retrievable passage, with everything needed to cite it."""

    kind: str  # "document" or "external_source"
    text: str
    document_id: uuid.UUID | None = None
    source_id: uuid.UUID | None = None
    title: str | None = None
    page_number: int | None = None
    ordinal: int | None = None
    url: str | None = None


@dataclass(frozen=True, slots=True)
class Citation:
    """A locator plus the verbatim text found there."""

    kind: str
    quote: str
    document_id: uuid.UUID | None
    source_id: uuid.UUID | None
    title: str | None
    page_number: int | None
    ordinal: int | None
    url: str | None
    score: float

    @property
    def locator(self) -> str:
        """A human-readable locator. Unknown parts are omitted, never guessed."""
        parts: list[str] = []
        if self.title:
            parts.append(self.title)
        if self.page_number is not None:
            parts.append(f"p. {self.page_number}")
        if self.ordinal is not None:
            parts.append(f"§{self.ordinal}")
        return " — ".join(parts)


@dataclass(slots=True)
class GroundedAnswer:
    question: str
    language: str
    status: str  # "grounded" | "insufficient_evidence" | "no_evidence_available"
    evidence_sufficient: bool
    coverage: float
    citations: list[Citation] = field(default_factory=list)
    explanation: str = ""
    # What the selection is actually based on. Always "lexical_overlap": there is
    # no semantic model here, and claiming otherwise would overstate the result.
    support_basis: str = "lexical_overlap"
    # Set when two selected passages word the same thing differently but state
    # different values. Citing either alone would hide the disagreement.
    conflicting_evidence: bool = False
    # Set when the question is polar (yes/no). An extractive lexical match can
    # surface a passage that mentions the same entities, but it cannot establish
    # the answer to a yes/no question, so support is not asserted.
    polar_question: bool = False


def detect_language(text: str) -> str:
    """Return ``"ar"`` or ``"fr"`` from the script actually present."""
    return "ar" if ARABIC_RANGE.search(text or "") else "fr"


def _normalize(text: str) -> str:
    folded = unicodedata.normalize("NFKC", text or "").translate(_ARABIC_DIACRITICS)
    return folded.translate(_ARABIC_FOLD).lower()


def _tokens(text: str) -> set[str]:
    return {m.group(0) for m in _TOKEN_RE.finditer(_normalize(text)) if len(m.group(0)) > 1}


def _is_stopword(token: str) -> bool:
    # Short function words that carry no topical signal in either language.
    return token in {
        "من",
        "في",
        "على",
        "إلى",
        "عن",
        "مع",
        "هذا",
        "هذه",
        "ذلك",
        "التي",
        "الذي",
        "ما",
        "هل",
        "هو",
        "هي",
        "كان",
        "que",
        "qui",
        "est",
        "les",
        "des",
        "une",
        "pour",
        "dans",
        "sur",
        "avec",
    }


def _content_tokens(text: str) -> set[str]:
    return {t for t in _tokens(text) if not _is_stopword(t)}


def score_segment(question_tokens: set[str], segment_text: str) -> float:
    """Fraction of the question's content terms that the segment contains."""
    if not question_tokens:
        return 0.0
    overlap = question_tokens & _content_tokens(segment_text)
    return len(overlap) / len(question_tokens)


# Polar (yes/no) question markers. Detecting the question form is reliable in a
# way that detecting entailment is not, and it matters: a lexical match can
# surface a passage naming the same entities, but that says nothing about whether
# the answer to a yes/no question is yes.
POLAR_MARKERS_AR = ("هل ", "أيجوز", "أيمكن", "هل يجوز", "هل يمكن", "هل يصح")
POLAR_MARKERS_FR = (
    "est-ce que",
    "est-il",
    "est-elle",
    "peut-il",
    "peut-elle",
    "doit-il",
    "doit-elle",
    "a-t-il",
    "a-t-elle",
    "est-ce qu",
)

# Numerals, because a disagreement about a quantity is the one kind of
# contradiction a lexical engine can actually see. Digits in both scripts, plus
# the number words. Arabic number words are matched after normalization and
# tolerate the single-letter prefixes (بـ، لـ، وـ) that attach to them, which is
# why "بست" ("six") is recognised and a bare \b boundary would not be.
_NUMERAL = re.compile(
    r"\d+|[٠-٩]+|"
    r"\b(?:un|une|deux|trois|quatre|cinq|six|sept|huit|neuf|dix|onze|douze)\b|"
    r"(?:^|[\s])(?:[بلوفك])?(?:واحد|اثنين|اثنان|ثلاث|ثلاثه|اربع|اربعه|خمس|خمسه|"
    r"ست|سته|سبع|سبعه|ثمان|ثمانيه|تسع|تسعه|عشر|عشره|احدي|احدي عشر)(?=[\s.,،;]|$)"
)


def _numerals_in(text: str) -> set[str]:
    """Normalized set of numeric values stated in a passage."""
    return set(_NUMERAL.findall(_normalize(text or "")))


def _containment(first: set[str], second: set[str]) -> float:
    """Share of the *smaller* token set held in common.

    Jaccard is the wrong measure here. Two passages that contradict each other
    typically agree on the subject and disagree on the verb, so their full-token
    overlap stays low while they are plainly talking about the same thing.
    Measuring against the smaller set captures that.
    """
    if not first or not second:
        return 0.0
    return len(first & second) / min(len(first), len(second))


def is_polar_question(question: str) -> bool:
    """True for a yes/no question, in either language."""
    text = (question or "").strip().lower()
    if text.startswith(POLAR_MARKERS_AR) or any(m in text for m in POLAR_MARKERS_AR):
        return True
    return any(m in text for m in POLAR_MARKERS_FR)


def _as_quote(text: str) -> Citation:
    """Adapt an Evidence-like item so the same comparison covers both pools."""
    return Citation(
        kind="document",
        quote=text,
        document_id=None,
        source_id=None,
        title=None,
        page_number=None,
        ordinal=None,
        url=None,
        score=0.0,
    )


def detect_conflict(
    citations: list[Citation],
    pool: list[tuple[float, Evidence]] | None = None,
) -> bool:
    """True when two passages look alike but state different values.

    Near-duplicate wording plus differing numerals is a contradiction: two texts
    describing the same thing and giving different figures. Reporting either one
    alone would hide that they disagree, and reporting both under a confident
    answer would be worse.

    This is deliberately narrow. It catches numeric disagreement between passages
    that are otherwise saying the same thing; it does not detect contradiction in
    general, and it must not be described as doing so.
    """
    candidates: list[Citation] = list(citations[:4])
    if pool:
        for _score, item in pool:
            if len(candidates) >= 6:
                break
            candidates.append(_as_quote(item.text))
    if len(candidates) < 2:
        return False
    for first, second in itertools.combinations(candidates, 2):
        first_tokens = _content_tokens(first.quote)
        second_tokens = _content_tokens(second.quote)
        if not first_tokens or not second_tokens:
            continue
        overlap = _containment(first_tokens, second_tokens)
        if overlap < 0.35:
            continue
        first_numbers = _numerals_in(first.quote)
        second_numbers = _numerals_in(second.quote)
        if first_numbers and second_numbers and first_numbers != second_numbers:
            return True
    return False


def ground_answer(question: str, evidence: list[Evidence], *, limit: int = 5) -> GroundedAnswer:
    """Select the passages that support an answer to ``question``.

    Returns ``insufficient_evidence`` with no citations when nothing clears the
    threshold. Never invents a passage to fill the gap.
    """
    language = detect_language(question)
    question_tokens = _content_tokens(question)

    if not evidence:
        return GroundedAnswer(
            question=question,
            language=language,
            status="no_evidence_available",
            evidence_sufficient=False,
            coverage=0.0,
            explanation=(
                "لا تتوفر أي مصادر للبحث فيها."
                if language == "ar"
                else "Aucune source n'est disponible pour cette recherche."
            ),
        )

    if not question_tokens:
        return GroundedAnswer(
            question=question,
            language=language,
            status="insufficient_evidence",
            evidence_sufficient=False,
            coverage=0.0,
            explanation=(
                "السؤال لا يحتوي على كلمات قابلة للبحث."
                if language == "ar"
                else "La question ne contient aucun terme exploitable."
            ),
        )

    scored: list[tuple[float, Evidence]] = []
    # Ranked without the score gate. A passage that contradicts the selected one
    # is relevant to whether the answer can be asserted even when it is too weakly
    # related to be cited itself -- filtering it out first would hide exactly the
    # disagreement that matters.
    ranked: list[tuple[float, Evidence]] = []
    for item in evidence:
        score = score_segment(question_tokens, item.text)
        ranked.append((score, item))
        if score >= MIN_SEGMENT_SCORE:
            scored.append((score, item))

    scored.sort(key=lambda pair: pair[0], reverse=True)
    ranked.sort(key=lambda pair: pair[0], reverse=True)
    selected = scored[:limit]

    if not selected:
        return GroundedAnswer(
            question=question,
            language=language,
            status="insufficient_evidence",
            evidence_sufficient=False,
            coverage=0.0,
            explanation=(
                "لم يتم العثور في المصادر المتاحة على ما يكفي للإجابة. "
                "لا يمكن تقديم إجابة دون دليل."
                if language == "ar"
                else "Les sources disponibles ne contiennent pas assez d'éléments "
                "pour répondre. Aucune réponse n'est fournie sans preuve."
            ),
        )

    best_score = selected[0][0]
    citations = [
        Citation(
            kind=item.kind,
            quote=item.text.strip(),
            document_id=item.document_id,
            source_id=item.source_id,
            title=item.title,
            page_number=item.page_number,
            ordinal=item.ordinal,
            url=item.url,
            score=round(score, 4),
        )
        for score, item in selected
    ]

    sufficient = best_score >= MIN_COVERAGE
    if sufficient:
        explanation = (
            "الإجابة مبنية حصريًا على المقاطع المقتبسة أدناه، مع ذكر مصدر كل مقطع."
            if language == "ar"
            else "La réponse repose uniquement sur les passages cités ci-dessous, "
            "chacun accompagné de sa source."
        )
    else:
        explanation = (
            "المقاطع أدناه ذات صلة جزئية فقط؛ الدليل غير كافٍ لإجابة موثوقة."
            if language == "ar"
            else "Les passages ci-dessous ne sont que partiellement pertinents ; "
            "les preuves sont insuffisantes pour une réponse fiable."
        )

    # Two things lexical overlap cannot establish, checked before support is
    # asserted. Both are conservative: they downgrade to insufficient evidence
    # rather than risk an overstatement.
    # Checked against the wider ranked pool, not just the citations, so a
    # contradiction with a passage too weakly related to cite still counts.
    conflicting = detect_conflict(citations, ranked[: max(limit * 2, 6)]) if sufficient else False
    polar = is_polar_question(question)

    if sufficient and (conflicting or polar):
        sufficient = False
        if conflicting:
            explanation = (
                "المقاطع المختارة متشابهة الصياغة لكنها تذكر قيمًا مختلفة، لذا لا يمكن "
                "ترجيح أحدها. تُعرض كما هي دون اعتبارها إجابة."
                if language == "ar"
                else "Les passages retenus sont proches mais indiquent des valeurs "
                "différentes ; aucun ne peut être privilégié. Ils sont présentés "
                "tels quels, sans être donnés comme réponse."
            )
        else:
            explanation = (
                "السؤال يستوجب جوابًا بنعم أو لا، والمطابقة اللفظية لا تثبته. المقاطع "
                "أدناه ذات صلة بالموضوع فقط، ولا تؤكد الجواب."
                if language == "ar"
                else "La question appelle une réponse par oui ou non, qu'une "
                "correspondance lexicale ne peut établir. Les passages ci-dessous "
                "sont liés au sujet mais ne confirment pas la réponse."
            )

    return GroundedAnswer(
        question=question,
        language=language,
        status="grounded" if sufficient else "insufficient_evidence",
        evidence_sufficient=sufficient,
        coverage=round(best_score, 4),
        citations=citations,
        explanation=explanation,
        conflicting_evidence=conflicting,
        polar_question=polar,
    )
