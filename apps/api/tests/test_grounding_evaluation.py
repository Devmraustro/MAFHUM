"""Pin the citation/grounding evaluation so regressions are caught.

The rates asserted below are the *measured* output of
``tools/evaluate_citation_grounding.py`` over
``tests/evaluation/citation_grounding_v1.json``, not aspirational targets.

``claim_support_honest`` is asserted against a floor of 0.83, not 1.0, and that
is deliberate. Four cases in the dataset contain a passage that shares its
subject matter with the question and therefore gets retrieved, but that a human
reading both texts would not accept as support -- "the President addresses
Parliament" cited for "can the President dissolve Parliament?". A lexical
grounding scorer cannot tell those apart. Reporting 100% here would mean either
removing the cases that expose the limitation or inferring support from the
quotation being present, which is exactly the error this evaluation exists to
prevent.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

TOOLS = Path(__file__).resolve().parent.parent / "tools" / "evaluate_citation_grounding.py"
EVAL_DIR = Path(__file__).resolve().parent / "evaluation"
V1 = EVAL_DIR / "citation_grounding_v1.json"
V2 = EVAL_DIR / "citation_grounding_v2.json"


def run(dataset: Path, tmp_path_factory: pytest.TempPathFactory) -> dict[str, object]:
    """Score a dataset and return the report, whatever the exit code.

    The exit code is returned alongside it rather than asserted here, because v1
    legitimately exits non-zero: four of its labels record the behaviour of the
    engine as it was before the fixes, so they no longer match. Asserting the
    specific known deviation is more informative than forcing exit 0.
    """
    report_path = tmp_path_factory.mktemp("grounding") / f"{dataset.stem}.json"
    completed = subprocess.run(
        [sys.executable, str(TOOLS), "--json", str(report_path), "--dataset", str(dataset)],
        capture_output=True,
        text=True,
        check=False,
    )
    report = json.loads(report_path.read_text(encoding="utf-8"))
    report["_exit_code"] = completed.returncode
    return report


@pytest.fixture(scope="module")
def report(tmp_path_factory: pytest.TempPathFactory) -> dict[str, object]:
    """v2 is authoritative: v1's four unsupported-claim labels are stale."""
    return run(V2, tmp_path_factory)


@pytest.fixture(scope="module")
def baseline_report(tmp_path_factory: pytest.TempPathFactory) -> dict[str, object]:
    return run(V1, tmp_path_factory)


# The four v1 cases whose expected_status recorded the defective behaviour.
STALE_V1_LABELS = {
    "ar-06-citation-exists-but-does-not-support",
    "ar-07-conflicting-passages",
    "fr-05-citation-exists-but-does-not-support",
    "fr-06-conflicting-passages",
}


class TestDatasetShape:
    def test_dataset_covers_both_languages_and_all_required_categories(self) -> None:
        dataset = json.loads(V2.read_text(encoding="utf-8"))
        cases = dataset["cases"]

        languages = {case["language"] for case in cases}
        assert languages == {"ar", "fr"}
        assert sum(1 for c in cases if c["language"] == "ar") == sum(
            1 for c in cases if c["language"] == "fr"
        ), "Arabic and French must stay balanced"

        required = {
            "topical_similarity_no_entailment",
            "partial_evidence",
            "polar_answerable",
            "supported_claim",
            "citation_does_not_support",
            "insufficient_evidence",
            "conflicting",
            "missing_page_metadata",
            "missing_publication_metadata",
            "mixed_language",
            "prompt_injection",
            "no_evidence_available",
        }
        present = {case["category"] for case in cases}
        assert required <= present, f"missing categories: {sorted(required - present)}"

    def test_support_labels_exist_where_semantics_matter(self) -> None:
        """Every unsupported case must carry a human label, or the metric is vacuous."""
        dataset = json.loads(V2.read_text(encoding="utf-8"))
        for case in dataset["cases"]:
            if case["category"] in {"citation_does_not_support", "conflicting"}:
                assert case["supports_claim"] is False, case["id"]
            assert "supports_claim" in case, case["id"]


class TestBaselineIsPreservedNotEdited:
    """v1 stays byte-identical so the original numbers remain reproducible."""

    # Recorded digest of citation_grounding_v1.json as committed at 0f1c654.
    # Embedded so the baseline can be verified in a shallow clone, where the
    # original commit is not present -- a CI checkout uses depth 1, and a test
    # that shells out to `git show <sha>` fails there with exit 128.
    V1_SHA256 = "ae55a569872deead615f35423f531cefba02a704dd05c85e093fb3d7c91aa02b"

    def test_v1_is_unchanged_since_it_was_committed(self) -> None:
        """The 24-case baseline must still be byte-for-byte what was measured."""
        import hashlib

        content = V1.read_bytes()
        assert hashlib.sha256(content).hexdigest() == self.V1_SHA256, (
            "citation_grounding_v1.json has been modified. The published baseline "
            "(20/24 claim_support_honest) is only meaningful against the original "
            "file, so editing it is not a way to change the score."
        )

    def test_v1_matches_the_committed_blob_when_history_is_available(self) -> None:
        """Cross-check against git, where the clone has enough history."""
        import subprocess as sp

        result = sp.run(
            ["git", "show", "0f1c654:apps/api/tests/evaluation/citation_grounding_v1.json"],
            capture_output=True,
            text=True,
            cwd=Path(__file__).resolve().parents[3],
        )
        if result.returncode != 0:
            pytest.skip(
                "commit 0f1c654 is not in this clone (shallow checkout); "
                "the digest test above covers the same guarantee"
            )
        assert V1.read_text(encoding="utf-8") == result.stdout

    def test_v1_claim_support_improved_from_the_original_measurement(
        self, baseline_report: dict[str, object]
    ) -> None:
        """83.3% -> 100%, on the same 24 cases, with the metric definition intact."""
        scores = baseline_report["scores"]
        assert isinstance(scores, dict)
        entry = scores["claim_support_honest"]
        assert isinstance(entry, dict)
        assert entry["rate"] == 1.0, entry
        assert entry["total"] == 24

    def test_v1_status_correct_deviates_only_on_the_four_stale_labels(
        self, baseline_report: dict[str, object]
    ) -> None:
        """The drop is explained, not hidden.

        v1 labelled these four as expected_status='grounded' while also labelling
        supports_claim=false -- it recorded what the buggy engine did. The engine
        now refuses, which is the correct behaviour, so it disagrees with the
        stale label. Nothing else may deviate.
        """
        scores = baseline_report["scores"]
        assert isinstance(scores, dict)
        entry = scores["status_correct"]
        assert isinstance(entry, dict)
        assert entry["passed"] == 20, entry

        deviating = {
            r["id"]
            for r in baseline_report["results"]  # type: ignore[union-attr]
            if not r["checks"]["status_correct"]
        }
        assert deviating == STALE_V1_LABELS

    def test_v1_exits_nonzero_and_that_is_expected(
        self, baseline_report: dict[str, object]
    ) -> None:
        assert baseline_report["_exit_code"] == 1


class TestScores:
    @pytest.mark.parametrize("metric", ["citation_resolves", "quote_is_verbatim"])
    def test_citation_construction_is_perfect(self, report: dict[str, object], metric: str) -> None:
        scores = report["scores"]
        assert isinstance(scores, dict)
        entry = scores[metric]
        assert isinstance(entry, dict)
        assert entry["rate"] == 1.0, f"{metric} regressed: {entry}"

    @pytest.mark.parametrize(
        ("metric", "expected_passed"),
        [("status_correct", 33), ("no_fabrication", 33), ("claim_support_honest", 37)],
    )
    def test_measured_rates_are_held(
        self, report: dict[str, object], metric: str, expected_passed: int
    ) -> None:
        """Asserted against what was measured, not against 1.0.

        v2 deliberately contains cases the engine still gets wrong, so a perfect
        score here would mean the hard cases had been removed.
        """
        scores = report["scores"]
        assert isinstance(scores, dict)
        entry = scores[metric]
        assert isinstance(entry, dict)
        assert entry["passed"] == expected_passed, entry
        assert entry["total"] == 38

    def test_not_over_refusing_is_perfect(self, report: dict[str, object]) -> None:
        """Grounded cases must still be grounded; conservatism has a limit."""
        scores = report["scores"]
        assert isinstance(scores, dict)
        entry = scores["not_over_refusing"]
        assert isinstance(entry, dict)
        assert entry["rate"] == 1.0, entry

    def test_claim_support_is_measured_not_assumed(self, report: dict[str, object]) -> None:
        """Asserted against the measured floor, and never as 100%."""
        scores = report["scores"]
        assert isinstance(scores, dict)
        entry = scores["claim_support_honest"]
        assert isinstance(entry, dict)
        assert entry["rate"] >= 0.83, f"claim support regressed: {entry}"

    def test_known_limitations_are_reported_not_suppressed(self, report: dict[str, object]) -> None:
        """fr-15 remains a genuine miss and must stay reported.

        Its contradiction is stated in words ("la majorite" against "les deux
        tiers") with only one numeral between them, so the conflict detector
        cannot see the disagreement. Left visible rather than engineered away.
        """
        limitations = report["known_limitations"]
        assert isinstance(limitations, list)
        ids = [item["id"] for item in limitations]
        assert ids == ["fr-15-numeric-contradiction"], ids

    def test_remaining_weaknesses_stay_in_the_dataset(self, report: dict[str, object]) -> None:
        """Cases the engine still gets wrong must not be quietly deleted."""
        failing = {
            r["id"]
            for r in report["results"]  # type: ignore[union-attr]
            if not all(v for kk, v in r["checks"].items() if kk != "claim_support_honest")
        }
        assert {
            "ar-13-topical-no-entailment",
            "fr-14-partial-evidence",
            "fr-15-numeric-contradiction",
            "ar-16-insufficient",
            "fr-16-insufficient",
        } <= failing, failing

    def test_conservative_refusals_are_counted(self, report: dict[str, object]) -> None:
        """Refusing is safe but not free, so the cost is measured.

        ar-17/fr-17 are polar questions the passage does answer; the polar rule
        refuses them anyway. Without this counter a change that refused
        everything would look like a perfect score.
        """
        assert report["conservative_cost"] == 2

    def test_v2_fails_only_on_the_known_remainder(self, report: dict[str, object]) -> None:
        """v2 exits non-zero, and the reason is enumerated rather than tolerated.

        These five are cases the engine genuinely still gets wrong. If a sixth
        joins the set, that is a new regression and this test fails.
        """
        assert report["_exit_code"] == 1
        assert {r["id"] for r in report["regressions"]} == {  # type: ignore[union-attr]
            "ar-13-topical-no-entailment",
            "fr-14-partial-evidence",
            "fr-15-numeric-contradiction",
            "ar-16-insufficient",
            "fr-16-insufficient",
        }

    def test_limitations_are_documented_in_the_dataset(self, report: dict[str, object]) -> None:
        limitations = report["limitations"]
        assert isinstance(limitations, list)
        joined = " ".join(limitations)
        # The single most important caveat must be stated in the artifact itself.
        assert "does NOT prove" in joined
