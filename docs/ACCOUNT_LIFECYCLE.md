# Account lifecycle — reset, verification, password change, sessions

This document describes what happens when a student loses their password, and
what the system does *not* claim. It is written to be read next to the code:
`api/v1/account_lifecycle.py`, `services/identity/tokens.py`,
`services/notifications/email.py`, and migration `0011_account_lifecycle`.

## The gap this closed

Before this work an account had no recovery path. There was no password reset,
no address verification, no way to change a password from inside the product, and
no way to see or end an active session. Losing the password meant losing the
documents with it, permanently. That is a product-blocking defect, not a missing
nicety.

## Endpoints

| Method | Path | Auth | CSRF |
|---|---|---|---|
| POST | `/auth/password-reset/request` | none | origin + double-submit |
| POST | `/auth/password-reset/confirm` | none | origin + double-submit |
| POST | `/auth/email-verification/request` | session | full |
| POST | `/auth/email-verification/confirm` | none | origin + double-submit |
| POST | `/auth/password/change` | session | full |
| GET | `/auth/sessions` | session | — |
| DELETE | `/auth/sessions/{id}` | session | full |
| GET | `/auth/account-state` | session | — |
| GET | `/auth/csrf` | none | — (issues the cookie) |

The two recovery flows are deliberately reachable **without a session**: the user
cannot sign in, which is the entire reason they are there. They are still
protected by the Origin/Referer allowlist and, whenever the browser holds a CSRF
cookie, by the double-submit token.

> A live run of the preview stack found that the confirm endpoint required a
> session. The cookie-carrying test suite never noticed, because every test was
> already signed in. `TestSignedOutRecovery` now pins the signed-out path, and
> `GET /auth/csrf` exists so those pages can obtain a token without a session.

## Tokens

- 32 random bytes (`secrets.token_urlsafe`), 43 characters.
- Only a SHA-256 digest is stored. The first 8 characters are stored beside it so
  the API can distinguish "already used" from "never existed": the first is
  actionable ("request a new link"), the second is not.
- Single use. Redemption takes a row lock and sets `consumed_at` in the same
  transaction, so two simultaneous submissions cannot both succeed.
- Issuing a token invalidates every live token of the same purpose for that user,
  so only the newest link works — what users expect from "resend".
- TTL: 60 minutes for reset, 3 days for verification (both configurable).

## Email delivery is opt-in and honest

`EMAIL_DELIVERY` has three values:

- `disabled` (default) — the endpoints answer `503 email_delivery_unavailable`
  with a message saying so. Nothing pretends to have sent mail.
- `file` — development. Each message is written as a `0600` `.eml` in
  `EMAIL_FILE_DIR` (gitignored). Production **refuses to boot** with this value.
- `smtp` — real delivery over STARTTLS (default), implicit TLS, or a trusted
  local relay.

The log is never used as a delivery channel. The application's log formatter
redacts anything shaped like a token, so a reset link routed through the log would
either be scrubbed (useless) or the redaction would have to be weakened (worse).

Reset links are built from `PUBLIC_WEB_URL`. They are never built from the
request's `Host` or `X-Forwarded-Host`, both of which an attacker controls and
which would let them receive the victim's token.

## Guarantees

- **No account enumeration.** The reset request returns the same status and body
  for a registered and an unknown address.
- **A reset revokes every session**, including the caller's. The usual reason to
  reset is that the account is no longer exclusively yours.
- **A password change requires the current password** and revokes every *other*
  session, keeping the device performing the change signed in.
- **Verification state is derived**, not stored: it is true exactly when a
  consumed `email_verification` token exists. There is no boolean to drift.
- **Revoking another user's session is a 404**, identical to a nonexistent id.
- Audit events are written for every one of these actions
  (`password_reset_requested`, `password_reset_completed`, `email_verified`,
  `password_changed`, `session_revoked`, …).

## What is still not true

- No test suite exercises a real SMTP handshake. `SmtpEmailSender` is covered by
  construction and by the disabled/file paths; a deployment using `smtp` should
  send itself one reset before announcing password recovery to users.
- Verification is advisory. It gates no read path: an unverified address can
  still sign in and read its own documents. That is deliberate — withholding a
  student's data over an unconfirmed address would be worse than the risk.
- Out-of-band mail (e.g. "your password was changed") is not sent yet.

## Operator checklist

1. Set `PUBLIC_WEB_URL` to the public origin, exactly.
2. Choose `EMAIL_DELIVERY=smtp` and fill the `SMTP_*` values.
3. Request one reset yourself and complete it end to end.
4. Only then tell users that password recovery works.
