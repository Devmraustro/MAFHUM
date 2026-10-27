# SSRF and retrieval security review

Status: **performed 2026-10-23**. This closes the Stage 1D gate that was left open.

## Summary of findings

Three real defects were found and fixed. None was caught by the 68 pre-existing
tests, which is the reason a review was worth doing separately from them.

| # | Defect | Severity | Fixed |
| --- | --- | --- | --- |
| 1 | The validated address was never used; the client re-resolved the hostname | **High** — DNS rebinding bypass | yes |
| 2 | `100.64.0.0/10` (RFC 6598 CGNAT) was not blocked | **Medium** — reachable internal/carrier space | yes |
| 3 | `http://host:0/` and out-of-range ports escaped as an unhandled `ValueError` | **Low** — 500 instead of a clean refusal | yes |

Plus one robustness gap: the response body was buffered whole before the size cap
was applied, so a small gzip expanding to gigabytes would be fully decompressed
into memory first.

## 1. DNS rebinding: the pinning was never wired up

`ssrf.validate_url` resolves the hostname and returns a `SafeTarget` carrying the
address it judged safe. Its docstring said callers "should connect to `address`
rather than re-resolving". **The only caller did not.**

`HttpSourceFetcher.fetch` did:

```python
target = validate_url(url)
...
current = target.url            # the hostname, not target.address
response = client.get(current)  # httpcore resolves it again
```

and on a redirect hop it called `validate_url(current)` and **discarded the
return value**.

Measured before the fix — `socket.getaddrinfo` call count for one fetch of one
hostname:

```
getaddrinfo calls for the validated hostname: 2
```

One call by validation, one by the transport. The address actually dialed was
never the address that was checked. A resolver answering with a public address
during validation and `169.254.169.254` at connection time would have been
obeyed, and `SafeTarget.address` was dead code.

**Fix.** `services/research/pinning.py` adds a custom httpcore network backend
that substitutes the pinned address in `connect_tcp`. The URL host is left
intact, so httpcore still passes the original hostname as `server_hostname` —
SNI and certificate verification keep working. Rewriting the URL to an IP would
have broken TLS validation for every HTTPS source. Each redirect hop adds its own
pin.

After the fix:

```
getaddrinfo calls for the validated hostname: 1   (was 2)
connected_to: [('dial.test', 80, '93.184.216.34')]
```

## 2. Deny-by-default address classification

The old check enumerated bad properties: `is_private or is_loopback or
is_link_local or is_reserved or is_multicast or is_unspecified`. Python's
`is_private` **does not include RFC 6598 shared address space**, so
`100.64.0.0/10` passed. That range is carrier infrastructure and commonly used
for internal services.

The check is now inverted: an address is acceptable only if `is_global` **and**
not in a named special-purpose range. Asking "is this definitely public?" fails
closed when the answer is unknown. `192.88.99.0/24` (deprecated 6to4 relay
anycast) is named explicitly because `is_global` reports it as true.

Verified not to over-block: `8.8.8.8`, `1.1.1.1`, `93.184.216.34`,
`2606:2800:220:1:248:1893:25c8:1946`, `2001:4860:4860::8888`, and the real
resolved addresses of the Algerian sources (`41.106.3.114`, `193.194.66.29`) all
remain allowed.

IPv4-mapped IPv6 is unwrapped and judged as the IPv4 it carries, so
`::ffff:127.0.0.1` is refused and `::ffff:93.184.216.34` allowed — the same
verdicts as the bare addresses. The two spellings cannot diverge.

## 3. Port handling

`parts.port or default` turned `:0` into 80, silently fetching a different port
than the URL named. Now `parts.port if parts.port is not None else default`, and
`urlsplit`'s `ValueError` for out-of-range ports is converted to `SsrfError`
rather than escaping as a 500.

## 4. Response limits

The body is now read through `response.iter_bytes()` and capped on the **decoded**
stream. This bounds both the download and the decompression: previously
`response.content` buffered everything, so a gzip bomb was fully expanded into
memory before `[:max_bytes]` could apply.

## Test tiers

`tests/test_ssrf_review.py` — **118 tests, 0 failures**. Every class states its
tier.

* **Unit** (injected resolver, no socket): public resolution and pinning; all
  blocked address classes as literals and as DNS answers; blocked hostnames
  refused before any lookup; IPv4-mapped and mixed public/private answers;
  unusual integer/hex/octal spellings of loopback (verified against the real
  glibc resolver, which does resolve `2130706433` and `0x7f000001` to
  `127.0.0.1` — they are refused because the *resolved* address is what is
  checked); scheme, credential, port and length rules; disclosure.
* **Controlled integration** (real `HttpSourceFetcher`, socket replaced by
  `httpx.MockTransport`, or the pinning transport with its dial observed):
  rebinding — resolution happens exactly once and a swapped second answer is
  never used; redirects to private addresses, blocked hostnames, and multi-hop
  chains ending in a private address; redirect loops terminating at the cap;
  relative locations; oversized and truncated bodies; custom byte caps; content
  type acceptance and refusal; error statuses; timeouts.
* **Live**: DNS only. See below.

`tests/test_research.py` — the pre-existing 69 tests still pass, 1 skipped. **No
existing security check was weakened or removed.**

## What remains unverified

Measured precisely in this sandbox:

| Capability | Result |
| --- | --- |
| DNS resolution of external hosts | **works** — `www.joradp.dz` → `41.106.3.114`, `41.111.135.101` |
| TCP connect to port 443 | **succeeds** |
| TLS handshake | **fails** — `SSLZeroReturnError`, connection closed during handshake |
| HTTP retrieval of any real page | **impossible** |

So the egress filter accepts the TCP connection and kills it at TLS. This matters
because a passing TCP connect could easily be mistaken for working egress.

Unverified, and stated as such:

1. **No live retrieval against a real publisher.** Nothing was observed on real
   content, so no publisher may be called integrated. Classification stays
   **IMPLEMENTED, FIXTURE-VERIFIED, LIVE-SOURCE-UNVERIFIED**.
2. **No end-to-end test through ports 80/443.** The guard allows only those
   ports and this process cannot bind them (`Permission denied`, uid 1001), so a
   local server cannot stand in for a real target. Redirect and size behaviour is
   verified through `MockTransport`, which replaces only the socket.
3. **Real DNS rebinding was not observed in the wild.** It was reproduced with a
   controlled resolver, which proves the client no longer re-resolves. A
   genuinely rebinding authoritative server was not used.
4. **No cloud metadata service was probed.** Every address in the suite is a
   literal or an injected answer.

## Reproducing

```bash
cd apps/api
pytest tests/test_ssrf_review.py -q     # 118 tests
pytest tests/test_research.py -q        # 69 tests, 1 skipped (opt-in live)
ruff check . && mypy src                # from apps/api, as CI runs it
```
