"""One definition of "the same Arabic word", shared by every matcher.

Two tables of diacritics and letter folds had grown in the codebase — one in the
document search service, one in the research grounding engine. They happened to
hold the same characters, which is exactly the kind of agreement that survives
until someone adds a rule to one of them: a student would then find a passage by
searching the document but not by asking the tutor a question about it.

This module is now the only place those rules live.
"""

from __future__ import annotations

import unicodedata

# Arabic diacritics (harakat, tanwin, shadda, sukun, superscript alef) and
# tatweel. Removing them lets an unvowelled query match a vowelled document.
_DIACRITIC_CHARS = "\u064b\u064c\u064d\u064e\u064f\u0650\u0651\u0652\u0670\u0640"
ARABIC_DIACRITICS = dict.fromkeys(map(ord, _DIACRITIC_CHARS))

# Letter forms that Arabic writers use interchangeably. Folding them is what makes
# "أحمد" find "احمد", and "صلاة" find "صلاه".
ARABIC_FOLD = {
    ord("أ"): "ا",
    ord("إ"): "ا",
    ord("آ"): "ا",
    ord("ٱ"): "ا",
    ord("ى"): "ي",
    ord("ئ"): "ي",
    ord("ة"): "ه",
    ord("ؤ"): "و",
}


def fold_arabic(value: str) -> str:
    """Strip diacritics, fold letter variants and lowercase.

    Unicode is normalised (NFKC) first so presentation forms — a lam-alef typed
    as one glyph, Arabic-Indic digits in some keyboards — compare equal to their
    canonical spelling. Case folding is included because the same helper serves
    French text, where "Étude" must match "etude".

    Whitespace is not collapsed here: callers that need token equality split on
    whitespace themselves, and callers that need to preserve line structure keep
    it.
    """
    normalised = unicodedata.normalize("NFKC", value or "")
    stripped = normalised.translate(ARABIC_DIACRITICS).translate(ARABIC_FOLD)
    return stripped.lower()
