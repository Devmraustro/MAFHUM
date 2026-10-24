# Source deduplication policy

Status: implemented in Phase 4 completion work (revision `0003_source_dedup`).

## The rule

Two `source_records` rows belonging to the **same owner** are the same source if
and only if their URLs canonicalize to the same string. Nothing else is used.
Titles, authors, DOIs, and publication years are **never** used to decide
identity.

Two deliberate consequences of that:

* Two different papers whose titles differ by one character stay separate. There
  is no fuzzy or similarity matching anywhere in this path.
* The same paper catalogued by two different students produces two independent
  records. Deduplication is scoped per owner; merging across owners would expose
  one user's catalog to another.

## Enforcement

Uniqueness is a **partial unique index** on `(owner_id, url_normalized) WHERE
url_normalized IS NOT NULL`, not an application-level pre-insert lookup. A lookup
cannot survive two concurrent inserts — both transactions can pass the check
before either commits. The index can, and when a concurrent request loses the
race the service resolves the clash to the record that won and returns it, so the
caller's intent is satisfied rather than reported as an error.

`test_concurrent_insert_of_the_same_url_yields_one_record` drives six parallel
inserts of the same URL and asserts exactly one source exists afterwards. It is
the test that would fail if enforcement were ever moved back to a lookup.

## What canonicalization does and does not do

See `services/sources/canonical.py` for the implementation and the reasoning per
component. In brief:

**Normalized** (equivalence is provable): fragment dropped; scheme and host
lowercased; trailing dot on the host removed; `:80`/`:443` dropped for their own
schemes.

**Not normalized** (equivalence is *not* provable): `http` vs `https`; the `www.`
prefix; path case; the query string; a trailing slash; non-default ports.

A URL that cannot be parsed, is not `http`/`https`, carries credentials, or
exceeds the length bound canonicalizes to `NULL`.

## Known false negatives — duplicates that are NOT caught

These are accepted, because catching them would risk merging things that are not
the same:

1. **Redirects and canonical URLs.** `…/paper` and `…/paper/` are distinct
   resources on many servers, so they are not merged even when one redirects to
   the other. Detecting this safely requires an actual request, and the retrieval
   path is opt-in and offline by default.
2. **Tracked query parameters.** `…/paper?utm_source=x` and `…/paper` are treated
   as different. Dropping parameters is a common cause of false merges, and some
   queries are signed.
3. **`www.` variants, http/https variants, and differently cased paths.**
4. **Unparseable or credential-bearing URLs**, which have no identity at all.
5. **The same paper at two different publishers or repositories.** Different URLs
   are different sources here even when the underlying work is identical; DOI is
   recorded as metadata but is not an identity key.

## Known false positives — merges that could be wrong

The normalization is deliberately narrow, so the surface is small:

1. **Fragments.** If a server ever used a fragment to select content server-side
   (non-standard, but possible in a single-page repository), dropping it would
   merge two genuinely different views.
2. **Default ports.** If `https://host` and `https://host:443` were ever served
   by different listeners on the same host, they would be merged. This contradicts
   RFC 3986, so it is treated as not a real case.

Neither is known to occur on the sources MAFHUM lists.

## Data preservation

Deduplication never rewrites what the user submitted. When a duplicate is
detected, the **first** record is returned unchanged — its original URL, title,
provenance, and verification status are all preserved. `url_normalized` is a
derived column; it can be dropped and rebuilt from `url` at any time. Provenance
and publication metadata are not part of the identity and are never merged or
overwritten.
