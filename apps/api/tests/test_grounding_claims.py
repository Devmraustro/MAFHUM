"""Regression tests for the claim-support fixes.

Two behaviours were added to ``grounding.py`` after the citation evaluation
showed the engine asserting support it had not established. These tests pin them
to the *rules*, not to the evaluation's specific sentences, so a fix cannot be
made to work only for the cases that exposed it.

The honest framing, restated because the tests encode it: neither rule detects
entailment. Conflict detection sees numeric disagreement between passages that
share their subject; the polar rule declines to answer yes/no questions at all.
A passage that is topically related but does not entail the claim is still
mis-grounded when neither rule applies, and ``ar-13``/``fr-13`` in the v2 dataset
are kept precisely to show that.
"""

from __future__ import annotations

import pytest

from mafhum_api.services.research.grounding import (
    Evidence,
    detect_conflict,
    ground_answer,
    is_polar_question,
)


def ev(text: str, page: int = 1, ordinal: int = 1) -> Evidence:
    return Evidence(kind="document", text=text, page_number=page, ordinal=ordinal)


class TestPolarQuestionDetection:
    @pytest.mark.parametrize(
        "question",
        [
            "هل يجوز لرئيس الجمهورية حل البرلمان؟",
            "هل يمكن الطعن في النتائج؟",
            "أيجوز التأجيل؟",
            "Est-ce que le Président peut dissoudre le Parlement ?",
            "Le Président peut-il dissoudre le Parlement ?",
            "La loi est-elle applicable ?",
            "Le texte a-t-il été publié ?",
        ],
    )
    def test_polar_questions_are_recognised(self, question: str) -> None:
        assert is_polar_question(question) is True

    @pytest.mark.parametrize(
        "question",
        [
            "ما هي مدة ولاية رئيس الجمهورية؟",
            "كيف يتم التصويت على مشاريع القوانين؟",
            "Quelle est la durée du mandat présidentiel ?",
            "Quelles sont les conditions d'éligibilité ?",
            "Qui préside le Conseil des ministres ?",
        ],
    )
    def test_interrogative_questions_are_not_treated_as_polar(self, question: str) -> None:
        """The rule must not swallow every question, or it would refuse all of them."""
        assert is_polar_question(question) is False

    def test_polar_question_is_not_asserted_as_answered(self) -> None:
        """A passage naming the same entities does not answer a yes/no question."""
        answer = ground_answer(
            "هل يجوز لرئيس الجمهورية حل البرلمان؟",
            [ev("يوجه رئيس الجمهورية خطابا إلى البرلمان في افتتاح الدورة التشريعية.")],
        )
        assert answer.polar_question is True
        assert answer.evidence_sufficient is False
        assert answer.status == "insufficient_evidence"
        # The passage is still shown, labelled as related rather than as proof.
        assert answer.citations, "relevant passages stay visible"

    def test_polar_rule_applies_in_french_too(self) -> None:
        answer = ground_answer(
            "Le Président peut-il dissoudre le Parlement ?",
            [ev("Le Président de la République adresse un message au Parlement.")],
        )
        assert answer.polar_question is True
        assert answer.evidence_sufficient is False

    def test_non_polar_question_still_grounds(self) -> None:
        """The conservative rule must not break ordinary grounded answers."""
        answer = ground_answer(
            "ما هي مدة ولاية رئيس الجمهورية؟",
            [ev("تحدد مدة ولاية رئيس الجمهورية بخمس سنوات قابلة للتجديد مرة واحدة.")],
        )
        assert answer.polar_question is False
        assert answer.status == "grounded"
        assert answer.evidence_sufficient is True


class TestConflictDetection:
    def test_differing_numerals_in_similar_passages_are_a_conflict(self) -> None:
        citations = ground_answer(
            "ما هي مدة عضوية مجلس الأمة؟",
            [
                ev("تحدد مدة عضوية مجلس الأمة بست سنوات.", 30, 1),
                ev("ينتخب أعضاء مجلس الأمة لمدة أربع سنوات قابلة للتجديد.", 31, 2),
            ],
        ).citations
        assert detect_conflict(citations) is True

    def test_french_word_numerals_are_detected(self) -> None:
        citations = ground_answer(
            "Quelle est la durée du mandat des sénateurs ?",
            [
                ev("La durée du mandat des sénateurs est fixée à six ans.", 30, 1),
                ev("Les sénateurs sont élus pour un mandat de quatre ans renouvelable.", 31, 2),
            ],
        ).citations
        assert detect_conflict(citations) is True

    def test_agreeing_passages_are_not_a_conflict(self) -> None:
        """The detector must not fire whenever two passages are cited together."""
        citations = ground_answer(
            "Quelle est la durée du mandat ?",
            [
                ev("La durée du mandat du Président est de cinq ans.", 12, 1),
                ev("Le mandat du Président de la République dure cinq ans.", 13, 2),
            ],
        ).citations
        assert detect_conflict(citations) is False

    def test_unrelated_passages_with_different_numbers_are_not_a_conflict(self) -> None:
        """Different numbers in passages about different things are not a dispute."""
        citations = ground_answer(
            "Quelle est la durée du mandat et le nombre de membres ?",
            [
                ev("La durée du mandat est de cinq ans.", 12, 1),
                ev("Le budget comprend douze chapitres distincts.", 40, 5),
            ],
        ).citations
        assert detect_conflict(citations) is False

    def test_conflict_downgrades_the_answer(self) -> None:
        answer = ground_answer(
            "Quelle est la durée du mandat des sénateurs ?",
            [
                ev("La durée du mandat des sénateurs est fixée à six ans.", 30, 1),
                ev("Les sénateurs sont élus pour un mandat de quatre ans renouvelable.", 31, 2),
            ],
        )
        assert answer.conflicting_evidence is True
        assert answer.evidence_sufficient is False
        assert answer.status == "insufficient_evidence"
        # Both passages stay visible so the disagreement is not hidden.
        assert len(answer.citations) == 2

    def test_conflict_is_checked_beyond_the_cited_passages(self) -> None:
        """A contradiction with a passage too weakly related to cite still counts.

        This was the second half of the defect: for the French case only one
        passage cleared the score gate, so the disagreement was invisible.
        """
        answer = ground_answer(
            "Quelle est la durée du mandat sénatorial ?",
            [
                ev("La durée du mandat des sénateurs est fixée à six ans.", 30, 1),
                ev("Les sénateurs sont élus pour un mandat de quatre ans renouvelable.", 31, 2),
            ],
        )
        assert answer.conflicting_evidence is True
        assert answer.status == "insufficient_evidence"


class TestHonestFraming:
    def test_support_basis_is_always_lexical(self) -> None:
        """Nothing here performs semantic evaluation, and the answer says so."""
        for question, evidence in (
            ("ما هي مدة الولاية؟", [ev("تحدد مدة ولاية رئيس الجمهورية بخمس سنوات.")]),
            ("هل يجوز الحل؟", [ev("يوجه رئيس الجمهورية خطابا إلى البرلمان.")]),
        ):
            answer = ground_answer(question, evidence)
            assert answer.support_basis == "lexical_overlap"

    def test_no_evidence_path_is_unchanged(self) -> None:
        answer = ground_answer("ما هي مدة الولاية؟", [])
        assert answer.status == "no_evidence_available"
        assert answer.citations == []
        assert answer.conflicting_evidence is False
        assert answer.polar_question is False

    def test_insufficient_evidence_path_is_unchanged(self) -> None:
        answer = ground_answer("zzzz qqqq xxxx", [ev("نص عربي عن أحكام مختلفة.")])
        assert answer.status == "insufficient_evidence"
        assert answer.evidence_sufficient is False
