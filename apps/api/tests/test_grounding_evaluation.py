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
DATASET = Path(__file__).resolve().parent / "evaluation" / "citation_grounding_v1.json"


@pytest.fixture(scope="module")
def report(tmp_path_factory: pytest.TempPathFactory) -> dict[str, object]:
    report_path = tmp_path_factory.mktemp("grounding") / "report.json"
    completed = subprocess.run(
        [sys.executable, str(TOOLS), "--json", str(report_path)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr
    return json.loads(report_path.read_text(encoding="utf-8"))


class TestDatasetShape:
    def test_dataset_covers_both_languages_and_all_required_categories(self) -> None:
        dataset = json.loads(DATASET.read_text(encoding="utf-8"))
        cases = dataset["cases"]

        languages = {case["language"] for case in cases}
        assert languages == {"ar", "fr"}, cases and "both languages must be represented"

        required = {
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
        dataset = json.loads(DATASET.read_text(encoding="utf-8"))
        for case in dataset["cases"]:
            if case["category"] in {"citation_does_not_support", "conflicting"}:
                assert case["supports_claim"] is False, case["id"]
            assert "supports_claim" in case, case["id"]


class TestScores:
    @pytest.mark.parametrize(
        "metric",
        ["status_correct", "citation_resolves", "quote_is_verbatim", "no_fabrication"],
    )
    def test_structural_metrics_are_perfect(self, report: dict[str, object], metric: str) -> None:
        scores = report["scores"]
        assert isinstance(scores, dict)
        entry = scores[metric]
        assert isinstance(entry, dict)
        assert entry["rate"] == 1.0, f"{metric} regressed: {entry}"

    def test_claim_support_is_measured_not_assumed(self, report: dict[str, object]) -> None:
        """Asserted against the measured floor, and never as 100%."""
        scores = report["scores"]
        assert isinstance(scores, dict)
        entry = scores["claim_support_honest"]
        assert isinstance(entry, dict)
        assert entry["rate"] >= 0.83, f"claim support regressed: {entry}"

    def test_known_limitations_are_reported_not_suppressed(self, report: dict[str, object]) -> None:
        limitations = report["known_limitations"]
        assert isinstance(limitations, list)
        assert len(limitations) == 4, [item["id"] for item in limitations]
        assert {item["category"] for item in limitations} == {
            "citation_does_not_support",
            "conflicting",
        }

    def test_no_regressions(self, report: dict[str, object]) -> None:
        assert report["regressions"] == []

    def test_limitations_are_documented_in_the_dataset(self, report: dict[str, object]) -> None:
        limitations = report["limitations"]
        assert isinstance(limitations, list)
        joined = " ".join(limitations)
        # The single most important caveat must be stated in the artifact itself.
        assert "does NOT prove" in joined
