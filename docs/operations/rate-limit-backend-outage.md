# Rate limiting: what happens when the shared backend goes away

**Status:** **implemented** — `in_memory_fallback_enabled=True` in `app.py`,
regression-gated by `tests/test_ratelimit_storage_outage.py`.
**Date:** 2026-09-21. **Measured against:** the deployed stack (Python 3.12.7,
Flask-Limiter 3.5.0, `limits` 5.8.0) driving a real **Valkey 8** container —
the engine Render Key Value runs — stopped and restarted under a running app.

Companion to [rate-limiting-client-ip.md](rate-limiting-client-ip.md), which
covers who a limit is counted against. This one covers what happens when the
thing doing the counting disappears.

## Summary

Provisioning shared rate-limit storage closes roadmap **M1b** (`memory://` is
per-worker and resets on deploy). Before adopting it, the failure mode was
measured rather than assumed — and it was worse than the configuration
suggested: **stopping the backend under a running app returned 500 from every
throttled route for the next fifteen seconds**, including the health endpoint
Render polls and the self-check whose job is to report this exact condition.

Fixed by enabling Flask-Limiter's in-memory fallback. The distinction from
`swallow_errors` is the whole point and is asserted in tests: the fallback
keeps limiting in the worker's memory; `swallow_errors` would serve the
request **unlimited**.

## What was measured

Same app, same test client, backend stopped at t+0:

| Time | `/api/health` | `/api/login` | |
|---|---|---|---|
| t+0.3s | 200 | 401 | backend up |
| t+0.8s | **500** | **500** | backend killed |
| t+12.8s | **500** | **500** | still failing |
| t+16.1s | 200 | 401 | degradation finally engages |
| t+23.1s | 200 | 401 | backend restarted, recovered |

Fifteen seconds is not a coincidence — it is `_SHARED_RL_PROBE_TTL`.

## Why it happened

`_degrade_limiter_when_shared_storage_is_down` is correct and was never the
problem. It stands the limiter down when the backend is unreachable, but it
reads a health probe **cached for 15 seconds**. For one TTL after the backend
died, it kept serving the "healthy" it had recorded moments earlier, so it left
`limiter.enabled` True and the limit raised — straight through Flask-Limiter's
**route decorator**, which is the path that matters here, not the
`before_request` hook.

A cache that reports a state the process has already observed to be false is
the same defect as a label that outruns its observation.

Two things made fifteen seconds worse than it sounds:

- **`/api/health` is what Render polls for liveness.** A backend blip became a
  failing health check on the web service, which is a way for a rate-limiter
  dependency to take the product down.
- **`/api/verification/self` is the endpoint that reports "shared rate-limit
  storage is unreachable."** It was taken out by the precise condition it
  exists to report. A check that cannot run during the failure it detects is
  not a check.

This is not a rare event on the plan under consideration. Render's **free** Key
Value tier is in-memory only and states that all data is lost whenever an
instance restarts — so the outage path is the documented behaviour, not an
edge case.

## The fix

```python
in_memory_fallback_enabled=True,   # NOT swallow_errors
```

`swallow_errors` would have removed the 500s too, by dropping the limit and
serving the request unlimited — turning a security control off during exactly
the window an attacker would most like it off. The fallback instead keeps
counting, in this worker's memory, until the shared backend answers again.

Measured after the fix, same timeline: `/api/health` **200 throughout**,
`/api/login` normal, recovery clean.

## Behaviour during an outage, in order

1. **First seconds (stale cache).** Flask-Limiter's fallback absorbs the
   storage error. Requests are limited from in-process counters using the
   normal limits. No 500s.
2. **After the probe expires (~15s).** `_degrade_limiter_when_shared_storage_is_down`
   stands the limiter down and the per-route policies take over:
   `sensitive_when_degraded("refuse")` returns 503 for invite-code lookup,
   `"strict"` puts login on a tighter per-process cap
   (`_DEGRADED_LOGIN_LIMIT = 3`, verified: 3 allowed, then 429).
3. **Throughout.** `/api/verification/self` answers 200 and reports
   `ratelimit.shared_storage = FAIL`, critical, observed `DEGRADED — backend
   configured but could not record a count`. Mudman Command reads that as FAIL.
4. **On recovery.** Automatic, no redeploy. Counters return to the shared
   backend; verified that a second, separate process immediately sees the
   first process's counts.

## A backend that is up but full

A second failure mode, found while testing the free tier's memory cap and
worth knowing before choosing a plan. Valkey 8 with `maxmemory` exceeded and
`noeviction`:

```
PING          -> PONG
SET anything  -> OOM command not allowed when used memory > 'maxmemory'
```

The app stayed up, because the in-memory fallback absorbed the write errors —
and that is precisely what made it invisible. `ratelimit.shared_storage`
reported **PASS, "shared backend reachable (redis)"**, while the backend could
not record a single count and every limit had silently reverted to per-worker.

The probe was a ping. It now **increments a key and reads the result back**,
so the check exercises the thing it is claiming works. The same state now
reports FAIL with `could not record a count (OutOfMemoryError)`.

**What it still cannot see:** a backend configured to *evict* rather than
refuse (`allkeys-lru`) accepts the write and may drop the key moments later.
The probe succeeds; the counters erode anyway. No endpoint can detect that
from the inside — it is a property of the plan, and it belongs in the
provisioning decision rather than in monitoring.

## What the degraded state is not

The fallback counter is **per process**. With N workers an attacker gets N
times the stated allowance, and it resets when a worker restarts. It is a
floor, not shared rate limiting, and nothing reports it as equivalent — the
self-check says FAIL for the duration.

## Verified properties of shared storage itself

The reason for provisioning it at all, measured the same way — two separate
processes against one backend, six failed logins then a fresh process:

| Storage | worker-1 | worker-2 (fresh process) |
|---|---|---|
| `redis://` (shared) | `401 401 401 401 401 429` | `429 429` |
| `memory://` (today) | `401 401 401 401 401 429` | `401 401` |

With `memory://` the second worker starts from zero: the effective limit is
multiplied by the worker count. With shared storage it does not.

## Regression gate

`tests/test_ratelimit_storage_outage.py` — 9 tests, no container required.
They point the app at a refused port and **prime the cached probe to
"healthy"**, which is what actually reproduces the defect; a backend that is
dead from the start does not, because the degrade hook catches it before
anything can raise. Confirmed to fail without the fix (6 of 7) and pass with
it. The two ping-versus-write tests were confirmed the same way, by reverting
the probe to a bare ping and watching both fail.

## Sequencing consequence

The deployed build reads `RATELIMIT_STORAGE_URI` and has **none** of the
protections in this document — no degrade hook, no `sensitive_when_degraded`,
no in-memory fallback. Provisioning Key Value and pointing the *current*
production app at it would therefore introduce exactly the outage described
above, with no recovery path, on a plan that "might restart at any time".

**Shared storage must be provisioned after this code is live, never before.**
See [deployment-sequence.md](deployment-sequence.md).

## Open

- `limits` (the library Flask-Limiter delegates storage to, and therefore the
  component whose behaviour this document describes) is **not pinned** in
  `requirements.txt`. Tracked under the transitive-lockfile item in
  [reproducibility.md](reproducibility.md); this is one more reason to close it.
