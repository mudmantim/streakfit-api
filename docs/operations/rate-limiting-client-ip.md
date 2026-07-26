# Rate limiting: the client IP problem

**Status:** **implemented** — `ProxyFix(x_for=2)` in `app.py`, regression-gated by
`scripts/post_deploy_check.py`. Storage (`memory://`) deliberately unchanged; see roadmap **M1b**.
**Date:** 2026-07-26. **Measured against:** production, commit `8da9053`.

## Summary

Rate limits were enforced correctly *per bucket*, but the bucket was keyed on a Render-internal
address rather than the client, so **effective capacity was ~6–7× every configured limit.** The
limiter code was right; the key was wrong. Fixed by `ProxyFix(x_for=2)`.

An earlier note claimed limiting was "not enforced at all." That was wrong — see
[Why the first conclusion was wrong](#why-the-first-conclusion-was-wrong).

## What the limiter keyed on before the fix

`app.py` constructs the limiter as:

```python
limiter = Limiter(get_remote_address, app=app, default_limits=[],
                  storage_uri=os.environ.get("RATELIMIT_STORAGE_URI", "memory://"))
```

`get_remote_address()` in Flask-Limiter 3.5.0 is literally `return request.remote_addr or "127.0.0.1"`.
Before this change there was **no `ProxyFix`**, so the key was gunicorn's TCP peer.

## Measured evidence

### The header chain in production

From the (now removed) `/api/admin/forwarded-chain` diagnostic:

| Header | Observed value | Attribution |
|---|---|---|
| `remote_addr` | `10.26.173.131` | RFC1918 — Render internal. **This was the limiter key.** |
| `X-Forwarded-For` | `74.220.50.219, 104.23.243.118` | `[client, Cloudflare]` |
| `CF-Connecting-IP` | `74.220.50.219` | client |
| `True-Client-IP` | `74.220.50.219` | client |

`104.23.243.118` is inside Cloudflare's published `104.16.0.0/13`. `74.220.50.219` reverse-resolves
to `ip-74-220-50-219.ohio-egress.render.com` — that call was made from a **Render shell**, so the
"client" was Render's own Ohio egress. That does not affect the *structure* of the chain, which is
what matters here.

So the path is: **client → Cloudflare → Render internal → gunicorn**, and `X-Forwarded-For` carries
exactly two entries, the second being Cloudflare's address.

### The effect on limits

Probing `/api/register` (configured `5 per minute`) with a payload rejected at validation, before
any DB query:

| Probe | Result |
|---|---|
| 12 POSTs over a **single keep-alive TCP connection** | `400 ×5` then `429` — **exactly the configured limit** |
| 50 POSTs, new connection each | 30 allowed / 20 limited, first 429 at #13 |
| 60 POSTs, new connection each | 37 allowed / 23 limited, first 429 at #21 |

One connection → one bucket → the limit is enforced precisely. Spread across connections, capacity
multiplies by the number of distinct internal addresses seen: **~6–7 buckets**, reproducibly.

Practical effect: register `5/min → ~35/min`; coach `3/min → ~20/min` and `10/day → ~70/day`. The
coach endpoint has **no application-level cap** behind flask-limiter (verified — no counter in the
handler), so that is live spend exposure on a paid API.

### Attempting to forge `CF-Connecting-IP`

Sending a `CF-Connecting-IP` header produced **Cloudflare error 1000**; the request was rejected at
the edge and never reached the application. That header therefore cannot be forged through
Cloudflare.

## What the platform documentation establishes

Enough to decide **without further production diagnostics**:

1. **Render:** "Because traffic passes through Cloudflare and Render's load balancers, your app sees
   the proxy's IP by default. To get the real client IP, read the `x-forwarded-for` header."
   ([Render, DDoS protection](https://render.com/articles/how-render-handles-ddos-attacks)) —
   `X-Forwarded-For` is the **documented, supported** contract.
2. **Cloudflare:** "If there was no existing X-Forwarded-For header in the request sent to
   Cloudflare, X-Forwarded-For has an identical value to the CF-Connecting-IP header"; when one
   already exists, "Cloudflare will append the IP address of the HTTP proxy connecting to
   Cloudflare to the header."
   ([Cloudflare HTTP headers](https://developers.cloudflare.com/fundamentals/reference/http-headers/))
3. **Cloudflare** always sets `CF-Connecting-IP` for proxied traffic (same source).

Point 2 exactly predicts the two-entry chain we measured, and it is what makes the fix provably
safe — see below.

## ⚠️ Render's own guidance is unsafe if taken literally

Render's [feature-request thread](https://feedback.render.com/features/p/send-the-correct-xforwardedfor)
contains a statement from Render's CEO that they "set the first IP in the list to the real client
IP", while a user in the same thread reports Render "does not clear or reset any passed-in
X-Forwarded-For header (it only appends to it)."

**Trusting the *first* (leftmost) entry would be spoofable.** Per Cloudflare's documented append
behaviour, a client that sends its own `X-Forwarded-For: 1.2.3.4` produces
`1.2.3.4, <real client>, <Cloudflare>` at the origin — leftmost is attacker-controlled, so a
leftmost-trusting implementation lets any caller choose its own rate-limit bucket and evade limits
entirely.

The two statements only agree when the client sends no `X-Forwarded-For` — which is precisely not
the attack case. **Count from the right.**

## Determination

**Trust `X-Forwarded-For`, second entry from the right — i.e. `ProxyFix(x_for=2)`.**

Two trusted appenders sit between client and app: Cloudflare contributes the connecting IP, and
Render's layer appends Cloudflare's. So the last two entries are always infrastructure-written:

| Client sends | Chain at origin | `x_for=2` selects |
|---|---|---|
| nothing | `<client>, <CF>` | `<client>` ✅ |
| `X-Forwarded-For: 1.2.3.4` | `1.2.3.4, <client>, <CF>` | `<client>` ✅ |

Prepended junk only moves further left. **`x_for=2` is correct under both of Cloudflare's documented
behaviours (append and set-if-absent), so it is not spoofable.**

### Why `X-Forwarded-For` over `CF-Connecting-IP`

`CF-Connecting-IP` is empirically unforgeable here and needs no hop arithmetic, which is genuinely
attractive. It loses on one point: **Render does not document it.** Cloudflare is Render's
implementation detail — Render documents `X-Forwarded-For` (and `CF-Ray`) but makes no commitment
about `CF-Connecting-IP`, so a CDN change would remove it silently. `ProxyFix` also repairs
`request.remote_addr` process-wide, so logging and any future consumer see the real client too.

### The known fragility, and how to catch it

`x_for=2` encodes a hop count, so it breaks if the topology gains or loses a hop — notably if a
customer-owned Cloudflare zone is ever put in front of Render, which would insert a third entry.
That failure is silent by nature, so it should be caught by a gate rather than by hope:

**`scripts/post_deploy_check.py` asserts that `/api/register` allows exactly 5 requests per minute
from one client.** Before the fix that probe yielded ~35; it must now yield exactly 5, so any future
topology drift fails on the next deploy. This is also why the fix needs **no
diagnostic endpoint to verify** — the capacity probe is the end-to-end proof, requires no admin
secret, and is stronger than echoing headers.

## Storage: a separate, smaller issue

`memory://` is not the primary cause. It is still wrong in two ways: it resets on every deploy (so
the coach's `10 per day` silently restarts), and it partitions per process the moment a second
worker or instance exists. Fixing the key first is essential — **shared storage alone would be
worse than today**, collapsing every user onto one proxy-keyed bucket so a single heavy user could
429 strangers. Redis (`RATELIMIT_STORAGE_URI`, ~$10/mo) becomes worthwhile only after the key is
correct and when scaling past one worker.

## Why the first conclusion was wrong

The initial probe sent 8 requests against a limit whose effective ceiling was ~35, and concluded
"rate limiting is not enforced." A probe must exceed the ceiling it is testing to say anything at
all about it; that one could not have produced a 429 under any hypothesis. The single keep-alive
connection test — 5 then 429 — is what actually isolated the mechanism, by holding the key constant
instead of varying it unknowingly.

## Recommendation (not yet implemented)

1. `app.wsgi_app = ProxyFix(app.wsgi_app, x_for=2, x_proto=1)` — zero cost, no new service.
2. Add the exactly-5 capacity assertion to `post_deploy_check.py`.
3. Consider a DB-backed daily cap on `/api/coach` as defence in depth, since spend is the exposure.
4. Defer Redis until scaling past one worker, or until the per-deploy reset becomes unacceptable.

Also observed: `render.yaml` says `region: oregon`, but the egress resolves to **Ohio**. The file
already flags the region as unverified; this is evidence it is wrong.
