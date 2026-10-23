# Citation and grounding evaluation

Status: Phase 4 completion gate. Dataset version **1.0.0**, 2026-10-23.

## How to reproduce

```bash
cd apps/api
python tools/evaluate_citation_grounding.py --json /tmp/report.json
echo "exit=$?"
pytest tests/test_grounding_evaluation.py
```

The runner exits `0` when there are no structural regressions. The four
`claim_support_honest` failures are reported as a known limitation and do not
fail the run — see below for why that is the honest treatment rather than a
suppressed failure.

## Dataset

`apps/api/tests/evaluation/citation_grounding_v1.json` — **24 cases**, 12 Arabic
and 12 French, version-controlled.

Every passage is **synthetic**, authored for this evaluation. None is an excerpt
from a real publication, and no title, author, institution, year, DOI or page
number in the file refers to a real document. That is stated inside the file
itself so it cannot be mistaken for a real corpus.

Categories covered: supported claims, citations that exist but do not support the
claim, insufficient evidence, conflicting passages, missing page metadata,
missing publication metadata, mixed-language material (Arabic question against a
passage with embedded Latin terms, and the reverse), prompt injection embedded in
source text, an unsearchable question, a fully vocalized Arabic passage against
an unvocalized question, partial-metadata locators, and a deliberately
cross-language mismatch.

## Scoring rules

| Metric | What it checks | How |
| --- | --- | --- |
| `status_correct` | Engine status equals the expected status | mechanical |
| `citation_resolves` | Citation carries a locator and its quote is a contiguous substring of the cited passage | mechanical |
| `quote_is_verbatim` | Quote copied character-for-character, not paraphrased | mechanical |
| `no_fabrication` | Never reports `grounded` / `evidence_sufficient` where the label says the evidence was absent | mechanical |
| `claim_support_honest` | Did not assert grounding on a case a human labelled `supports_claim: false` | **against a human label** |

`no_fabrication` deliberately allows the engine to return the partially relevant
passages while reporting `insufficient_evidence` with `evidence_sufficient:
false`. Showing the work and declining the claim is the opposite of fabricating
support. An earlier version of this check treated any citation under
`insufficient_evidence` as a fabrication; that was wrong and measured the wrong
thing.

## Results

Measured on 2026-10-23, Python 3.11.2, `apps/api` at the Phase 4 completion work:

| Metric | Score | Rate |
| --- | --- | --- |
| `status_correct` | 24 / 24 | 100.0% |
| `citation_resolves` | 24 / 24 | 100.0% |
| `quote_is_verbatim` | 24 / 24 | 100.0% |
| `no_fabrication` | 24 / 24 | 100.0% |
| `claim_support_honest` | **20 / 24** | **83.3%** |

Runner exit code: `0`. `tests/test_grounding_evaluation.py`: 10 tests, 0 failures.

## The measured limitation, stated plainly

Four cases fail `claim_support_honest`:

* `ar-06` / `fr-05` — *citation exists but does not support*. The question asks
  whether the President may dissolve Parliament. The retrieved passage says the
  President addresses Parliament. Both the subject and the institution match, so
  the passage is retrieved and cited, but it says nothing about dissolution.
* `ar-07` / `fr-06` — *conflicting passages*. Two passages state different
  durations for the same mandate. Citing either without flagging the conflict is
  misleading.

**The quotation appears verbatim in the cited passage in all four cases.** That is
precisely why this metric exists: verbatim presence does not establish that the
passage supports the attributed claim. A score computed from presence alone would
have reported 100% here and would have been false.

The scoring is lexical by construction. Making it detect non-support requires
semantics the current engine does not have. The correct response is to report the
83.3% and to keep the UI from presenting these citations as verified support — not
to delete the cases that expose it, and not to infer support from presence.

## Other limitations

* Coverage is lexical. Semantically equivalent passages sharing no words score as
  unrelated. That is a conservative failure: it yields `insufficient_evidence`,
  never a fabrication.
* 24 cases is enough to catch regressions in status selection and citation
  construction. It is **not** enough to estimate real-world citation accuracy, and
  the 83.3% figure must not be quoted as an accuracy estimate for production use.
* The `supports_claim` labels were written by one person reading both texts. They
  are not an independent annotation and carry no inter-rater reliability measure.
* No paid evaluation service is used, and none is required.

---

## Update (2026-10-23): conflict detection, polar questions, and the v2 dataset

The engine changed after the original measurement. The 24-case baseline file is **unchanged**
(SHA-256 `ae55a569872deead615f35423f531cefba02a704dd05c85e093fb3d7c91aa02b`), so the original
numbers stay reproducible.

| Metric (24 cases) | Original | Now |
|---|---|---|
| status_correct | 24/24 | 20/24 |
| **claim_support_honest** | **20/24 (83.3%)** | **24/24 (100%)** |
| citation_resolves / quote_is_verbatim / no_fabrication | 24/24 | 24/24 |

The `status_correct` drop is confined to `ar-06`, `ar-07`, `fr-05`, `fr-06`, each of which carried
`expected_status="grounded"` together with `supports_claim=false` — the label recorded the defect.

A new dataset, `tests/evaluation/citation_grounding_v2.json` (38 cases, 19 ar / 19 fr), corrects
those four labels and adds topical-similarity, partial-evidence, contradiction, insufficient-evidence,
polar-answerable and grounded controls. Run it with
`python tools/evaluate_citation_grounding.py --dataset tests/evaluation/citation_grounding_v2.json`.

Measured on v2: claim_support_honest **37/38 (97.4%)**, status_correct 33/38, no_fabrication 33/38,
citation_resolves and quote_is_verbatim 38/38, not_over_refusing 38/38, conservative_cost 2.

`not_over_refusing` and `conservative_cost` exist so that refusing more cannot be mistaken for
improvement: the first proves grounded cases are still grounded, the second counts the polar
questions the engine now declines.

**Limitation, unchanged and still true:** the evaluator is lexical. It cannot establish semantic
entailment, and neither verbatim quotation nor citation existence nor lexical overlap is treated as
semantic support.
