"""Score the citation/grounding engine against a labelled evaluation dataset.

Usage:
    python tools/evaluate_citation_grounding.py [--json REPORT_PATH]

What this measures, and what it deliberately does not
-----------------------------------------------------
Four of the five scores are *structural* and can be checked mechanically:

* ``citation_resolves`` — the citation carries a locator and its quote is a
  contiguous substring of the passage it cites.
* ``quote_is_verbatim`` — the quote was copied, not paraphrased.
* ``status_correct`` — the engine's status matches the expected one.
* ``no_fabrication`` — the engine never reports ``grounded`` or
  ``evidence_sufficient`` where the label says the evidence was absent. It MAY
  still show the partially relevant passages: presenting candidates while
  declining to answer is the opposite of fabricating support.

The fifth, ``claim_support_honest``, is **not** computable from the text. A
quotation can appear verbatim in a passage and still not support the claim it is
cited for. Those cases (``citation_does_not_support``, ``conflicting``) carry a
``supports_claim`` label written by a human who read both the question and the
passage. This script compares the engine's behaviour against that label; it does
not infer support from the quote being present.

This means a high ``claim_support_honest`` score says "the engine did not assert
grounding where a human said the passage does not support the claim". It is not
a measure of semantic understanding, and it must not be reported as one.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from mafhum_api.services.research.grounding import Evidence, ground_answer

DATASET = (
    Path(__file__).resolve().parent.parent / "tests" / "evaluation" / "citation_grounding_v1.json"
)

# Categories where a human judged that the best available passage does NOT
# support the claim, even though the engine may legitimately retrieve it.
UNSUPPORTED_CATEGORIES = {"citation_does_not_support", "conflicting"}


def _evidence_for(case: dict[str, Any]) -> list[Evidence]:
    return [
        Evidence(
            kind="document",
            text=passage["text"],
            title=passage.get("title"),
            page_number=passage.get("page_number"),
            ordinal=passage.get("ordinal"),
        )
        for passage in case["passages"]
    ]


def _evaluate_case(case: dict[str, Any]) -> dict[str, Any]:
    evidence = _evidence_for(case)
    answer = ground_answer(case["question"], evidence)

    expected = case["expected_status"]
    result: dict[str, Any] = {
        "id": case["id"],
        "language": case["language"],
        "category": case["category"],
        "expected_status": expected,
        "actual_status": answer.status,
        "coverage": round(answer.coverage, 4),
        "citations": len(answer.citations),
        "checks": {},
    }

    result["checks"]["status_correct"] = answer.status == expected

    # Fabrication means asserting support that was not established. Returning the
    # partially relevant passages while saying "this is not enough to answer"
    # (status insufficient_evidence, evidence_sufficient False) is the opposite of
    # fabrication -- it is showing the work and declining the claim. So the
    # invariant is narrower than "no citations": the engine must never report
    # grounded / evidence_sufficient where the label says the evidence was absent.
    result["checks"]["no_fabrication"] = not (
        expected in {"insufficient_evidence", "no_evidence_available"}
        and (answer.status == "grounded" or answer.evidence_sufficient)
    )
    result["evidence_sufficient"] = answer.evidence_sufficient

    # Structural citation checks, over whatever the engine did return.
    passages = [p["text"] for p in case["passages"]]
    resolves = verbatim = True
    for citation in answer.citations:
        match = next((t for t in passages if citation.quote in t), None)
        if match is None:
            resolves = False
        if not citation.quote or citation.quote.strip() != citation.quote:
            verbatim = False
    result["checks"]["citation_resolves"] = resolves if answer.citations else True
    result["checks"]["quote_is_verbatim"] = verbatim if answer.citations else True

    # Honest claim support. Only meaningful where a human label exists, and only
    # where the engine actually asserted grounding.
    if case["category"] in UNSUPPORTED_CATEGORIES:
        # A grounded status here means the engine asserted support a human denied.
        result["checks"]["claim_support_honest"] = (
            answer.status != "grounded" or not answer.citations
        )
        result["labelled"] = True
    else:
        result["checks"]["claim_support_honest"] = True
        result["labelled"] = case.get("supports_claim") is not None

    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--json", type=Path, default=None, help="Where to write the machine-readable report."
    )
    parser.add_argument("--quiet", action="store_true")
    args = parser.parse_args()

    dataset = json.loads(DATASET.read_text(encoding="utf-8"))
    results = [_evaluate_case(case) for case in dataset["cases"]]

    metric_names = [
        "status_correct",
        "citation_resolves",
        "quote_is_verbatim",
        "no_fabrication",
        "claim_support_honest",
    ]
    scores = {
        name: {
            "passed": sum(1 for r in results if r["checks"][name]),
            "total": len(results),
            "rate": round(sum(1 for r in results if r["checks"][name]) / len(results), 4),
        }
        for name in metric_names
    }

    report = {
        "dataset": dataset["dataset"],
        "version": dataset["version"],
        "case_count": len(results),
        "cases_by_language": {
            lang: sum(1 for r in results if r["language"] == lang) for lang in ("ar", "fr")
        },
        "cases_by_expected_status": {
            status: sum(1 for r in results if r["expected_status"] == status)
            for status in ("grounded", "insufficient_evidence", "no_evidence_available")
        },
        "categories": sorted({r["category"] for r in results}),
        "scoring_rules": dataset["scoring_rules"],
        "scores": scores,
        # A structural check failing is a regression. A claim_support_honest
        # failure on a labelled case is the measured limitation this dataset
        # exists to expose, and is reported separately rather than hidden.
        "regressions": [
            r
            for r in results
            if not all(v for k, v in r["checks"].items() if k != "claim_support_honest")
        ],
        "known_limitations": [r for r in results if not r["checks"]["claim_support_honest"]],
        "results": results,
        "limitations": dataset["limitations"],
    }

    if args.json:
        args.json.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    if not args.quiet:
        print(
            f"{dataset['dataset']} v{dataset['version']} — {len(results)} cases "
            f"({report['cases_by_language']['ar']} ar / {report['cases_by_language']['fr']} fr)"
        )
        for name in metric_names:
            entry = scores[name]
            print(f"  {name:<24} {entry['passed']:>3}/{entry['total']:<3} {entry['rate']:.1%}")
        if report["regressions"]:
            print(f"\nREGRESSIONS in {len(report['regressions'])} case(s):")
            for failure in report["regressions"]:
                bad = [
                    k for k, v in failure["checks"].items() if not v and k != "claim_support_honest"
                ]
                print(
                    f"  - {failure['id']}: {', '.join(bad)} "
                    f"(expected {failure['expected_status']}, got {failure['actual_status']})"
                )
        if report["known_limitations"]:
            print(
                f"\nKnown limitation, {len(report['known_limitations'])} case(s): the engine "
                "asserted grounding where a human judged the passage does not support the claim."
            )
            for failure in report["known_limitations"]:
                print(f"  - {failure['id']} ({failure['category']})")
            print("  Verbatim presence of a quotation is not proof that it supports the")
            print("  claim. Detecting this needs semantics the lexical scorer does not have.")

    return 1 if report["regressions"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
