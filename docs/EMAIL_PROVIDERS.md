# Transactional email: configuration and free-tier providers

MAFHUM needs exactly two kinds of mail: password reset and address verification.
Both are transactional, low-volume, and must arrive quickly or not at all — a
reset link that appears ten minutes late is a support ticket, and one that
silently fails is a locked-out student.

The transport is SMTP, configured entirely through environment variables. No
provider SDK is imported, so switching providers is a change of four environment
values and nothing else.

## Configuration

| Variable | Meaning | Example |
|---|---|---|
| `EMAIL_DELIVERY` | `disabled` (default) \| `file` \| `smtp` | `smtp` |
| `SMTP_HOST` | provider hostname | `smtp.resend.com` |
| `SMTP_PORT` | usually 587 (STARTTLS) or 465 (implicit TLS) | `587` |
| `SMTP_SECURITY` | `starttls` \| `ssl` \| `plain` | `starttls` |
| `SMTP_USERNAME` | provider account / API key id | `resend` |
| `SMTP_PASSWORD` | provider API key or password | *(secret)* |
| `SMTP_FROM_ADDRESS` | envelope sender; **must be a verified domain** | `no-reply@yourdomain.tld` |
| `SMTP_TIMEOUT_SECONDS` | connect/send timeout | `15` |
| `PUBLIC_WEB_URL` | origin used to build reset links | `https://app.yourdomain.tld` |

Behavior by value:

- **`disabled`** — the reset and verification endpoints answer `503
  email_delivery_unavailable` with an honest message. Nothing pretends to send.
- **`file`** — writes `0600` `.eml` files for development. **Production refuses
  to boot** with this value.
- **`smtp`** — real delivery. Production also refuses to boot with `smtp` and an
  empty `SMTP_HOST`.

`PUBLIC_WEB_URL` is never derived from the request: `Host` and
`X-Forwarded-Host` are attacker-controlled, and a reset link built from them
would deliver the victim's token to the attacker.

## Free-tier providers that speak SMTP

Checked against each provider's published documentation in November 2026. Free
tiers change; the plan name is a pointer, not a promise — verify before relying on
one.

| Provider | Free allowance | SMTP host | Port / security | Notes |
|---|---|---|---|---|
| **Brevo** (ex-Sendinblue) | ~300 emails/day | `smtp-relay.brevo.com` | 587 STARTTLS | The most generous daily free tier. Requires domain verification for a custom `From`; sender authentication is via SMTP credentials, not the login password. |
| **Resend** | 3,000/month, 100/day | `smtp.resend.com` | 587 STARTTLS | Username is literally `resend`; password is the API key. Custom domain needs DNS records (SPF + DKIM). |
| **Mailjet** | 6,000/month, 200/day | `in-v3.mailjet.com` | 587 STARTTLS | Username/password are the API key and secret. Free tier adds a Mailjet footer to some plans. |
| **SendGrid** | 100/day (free plan) | `smtp.sendgrid.net` | 587 STARTTLS | Username is literally `apikey`; password is the API key. Historically stricter about new-account review. |
| **Amazon SES** | 3,000 message charges/month (12 mo) | `email-smtp.<region>.amazonaws.com` | 587 STARTTLS | Cheapest at scale, most setup: sandbox mode until you request production access, IAM SMTP credentials, per-region endpoints. |
| **Gmail / Google Workspace** | ~500/day (personal), 2,000 (Workspace) | `smtp.gmail.com` | 587 STARTTLS | Requires an app password and 2FA. **Not recommended**: personal-account sending is rate-limited, can be suspended for automated mail, and puts password recovery on a mailbox the operator also reads. |

### Recommendation for a free-tier deployment

**Brevo**, with a verified domain and SPF/DKIM/DMARC records:

```dotenv
EMAIL_DELIVERY=smtp
SMTP_HOST=smtp-relay.brevo.com
SMTP_PORT=587
SMTP_SECURITY=starttls
SMTP_USERNAME=your-brevo-smtp-login
SMTP_PASSWORD=your-brevo-smtp-key
SMTP_FROM_ADDRESS=no-reply@yourdomain.tld
PUBLIC_WEB_URL=https://app.yourdomain.tld
```

Rationale: the daily cap (300) is far above what password recovery for a
class-sized deployment needs, STARTTLS is standard, and the free tier does not
expire after a trial window the way some alternatives do. Resend is the better
choice if you already manage DNS through a provider it supports, because domain
verification takes minutes.

## What a configured provider does and does not prove

Proven by this repository's test suite, with a real SMTP server on a real port
(`tests/test_smtp_delivery.py`): the client opens a connection, sends a
correctly-encoded multipart message, the Arabic subject survives RFC 2047
encoding, the body is intact, `From` and `Message-ID` are stamped, an unreachable
server raises a bounded delivery error, and a failed AUTH never echoes the
credential.

**Not proven anywhere in this repository:** deliverability. SPF, DKIM, DMARC,
IP reputation and provider-side review are deployment properties. A message can
pass every test here and still land in spam. Before announcing that password
recovery works, send one reset to a real external mailbox (not the operator's own
domain) and confirm it arrives.

## Operational checklist

1. Verify the sending domain with the provider; publish SPF, DKIM and a DMARC
   policy (`p=none` to start, then tighten).
2. Set `PUBLIC_WEB_URL` to the exact public origin, no trailing slash.
3. Set `EMAIL_DELIVERY=smtp` and the `SMTP_*` values in the platform's secret store.
4. Boot the API and request a reset for a real address you control.
5. Confirm arrival, click the link, complete the reset, and sign in with the new
   password.
6. Only then tell users that recovery exists.
7. Watch the outbox: `email_outbox` rows with `status='failed'` and a rising
   `attempts` count mean the provider is refusing mail. The error stored is a
   bounded type name, never the provider's banner or a token.
