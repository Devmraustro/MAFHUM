# PR merge-sequence review — 2026-10-31

Prepared for `Devmraustro/MAFHUM` at owner request. **Read-only analysis**: no
merge, no PR-base change, no rebase, no force-push and no topology modification
was performed. The owner decides and executes the sequence.

Refs verified against `origin` on 2026-10-31 (after a successful `git fetch`;
the network to github.com intermittently fails and needed retries — recorded
where it affected a command):

| Ref | Tip | Role |
|---|---|---|
| `main` | `2a15f0c616806cede4ed4a09d6a2c2669313c8f3` | default branch, unchanged since PR #5 |
| `arena/7a7fdcba-mafhum` | `1199178d1b138aeda88b4277176d72044ed271a3` | **PR #6** head — security/privacy/monitoring/deployment hardening |
| `verification/windows-local-2026-10-29` | `26e83d45d791dcff0cb527353176aea2a01af97d` | PR #6 head **+ 1 commit**: `docs/LOCAL_WINDOWS_VERIFICATION_REPORT.md` |
| `remediation/windows-findings-d1-d4` | `9882099df4c62c44aeceedca9caa5060f1256e70` | **PR #7** head — verification branch **+ 5 commits** (D1–D4 + review corrections) |

## 1. Ancestry (measured, not assumed)

```
git merge-base origin/main <branch>             → 2a15f0c…   (for ALL three branches)
git merge-base origin/arena/7a7fdcba-mafhum origin/verification/windows-local-2026-10-29
                                                → 1199178…   (= PR #6 head)
git merge-base origin/verification/windows-local-2026-10-29 origin/remediation/windows-findings-d1-d4
                                                → 26e83d4…   (= verification tip)
```

The four refs form a **strictly linear stack**, each branch built directly on
the tip of the previous one:

```
main 2a15f0c
  └─ PR #6 head 1199178        (15 commits: 1baa884…1199178)
       └─ verification 26e83d4 (1 commit: the Windows verification report)
            └─ remediation 9882099 (5 commits: D1 00cbdd5, D2 c82da89,
                                    D3 798e873, D4 671957f, corrections 9882099)
```

Consequences that follow from the ancestry, each verified by command:

* Every branch can merge into `main` with **zero conflicts** today — `main` is
  the merge-base of all of them and has not moved.
* **PR #7 contains the PR #6 changes by ancestry, not by diff.** PR #7's
  reviewable diff (against its base `verification/windows-local-2026-10-29`)
  is exactly the five remediation commits; merging PR #7 alone would NOT put
  PR #6's content on `main`, because PR #7's base branch is not `main`.
* Merging order therefore matters for both correctness and audit attribution
  (see §3).

## 2. Commits unique to each branch

| Branch | Command used | Unique commits |
|---|---|---|
| PR #6 | `git log --oneline main..arena/7a7fdcba-mafhum` | **15** (`1baa884` → `1199178`; ClamAV wiring + acceptance, export/retention, encryption ADR, metrics token, render gate, Windows URL portability, doc corrections, compose contract) |
| verification | `git log arena/7a7fdcba-mafhum..verification/windows-local-2026-10-29` | **1** — `26e83d4` `docs: record the native Windows local verification pass` |
| remediation (PR #7) | `git log verification/windows-local-2026-10-29..remediation/windows-findings-d1-d4` | **5** — `00cbdd5` D1, `c82da89` D2, `798e873` D3, `671957f` D4, `9882099` review corrections |

## 3. Recommended merge sequence (no base change, no duplicates)

**Option B — recommended** (works entirely with existing PR bases; one new PR
is opened by the owner):

1. **Merge PR #6** (`arena/7a7fdcba-mafhum` → `main`). `main` moves to
   `1199178` (fast-forward-able; GitHub's default creates a merge commit).
2. **Merge PR #7** as-is (`remediation/windows-findings-d1-d4` → its base
   `verification/windows-local-2026-10-29`). The verification branch now
   carries report + remediation (`9882099`).
3. **Open and merge one new PR**: `verification/windows-local-2026-10-29` →
   `main`. Its diff against the post-#6 `main` is exactly **6 commits**
   (the report + the five remediation commits). Nothing is duplicated; every
   commit lands in `main` exactly once; the verification report is preserved.

**Option D — alternative** (more granular PRs, but requires one owner action
this analysis deliberately did not perform): merge PR #6 → `main`; open and
merge a PR `verification…` → `main` (report only); **the owner retargets PR
#7's base to `main`**; merge PR #7 (remediation only). Changing a PR base is
explicitly out of scope here, so Option D is executable only by the owner.

**Hazard to avoid:** do **not** merge a `verification… → main` PR **before**
PR #6. That would make PR #6's head commit reachable from `main`, and GitHub
would auto-mark PR #6 *merged* without its own merge commit or review record —
an audit-trail loss. Merging #6 first is safe in every option.

## 4. Branch protection status (read with an admin credential)

```
GET /repos/Devmraustro/MAFHUM/branches/main/protection → 404 "Branch not protected"
GET /repos/Devmraustro/MAFHUM/rules/branches/main      → [] (no rulesets)
```

`main` currently has **no branch protection, no required status checks and no
rulesets**. The viewer credential used for this read has admin rights on the
repository. Enabling protection with required checks (MAFHUM CI, PPTX render,
production-malware acceptance) before the sequence in §3 runs is an owner
decision this report recommends but did not implement.

## 5. What was deliberately not done

No merge, no PR-base change, no rebase, no force-push, no branch deletion, no
visibility change. The only artefact of this review is this document.
