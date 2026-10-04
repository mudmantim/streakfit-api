# Rate limiting: what happens when the shared backend goes away

**Status:** **superseded in part (2026-10-04, D7 round 2).** The outage
*policy* below ("Option B": stand the limiter down) has been replaced; read
[the last section](#one-authority-through-a-redis-failure-d7-round-2-2026-10-04)
first. The history above it is kept because the measurements still explain
why the code looks the way it does.
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

## Behaviour during an outage, in order (Option B — REPLACED, see the last section)

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

- ~~`limits` is not pinned~~ — pinned at **5.8.0** in D7 round 2, because the
  gate subclasses its `Storage` and drives its `RedisStorage` directly.

## A backend that hangs instead of refusing (D7, 2026-10-04)

Everything above concerns a backend that **refuses** connections, which fails
fast. A backend that **accepts and never answers**, or a host that never
answers a SYN, was a different and far worse failure. redis-py's defaults are
`socket_timeout=None` and `socket_connect_timeout=None`, and production runs
**one sync worker**, so a stuck call held the whole application:

| real Valkey, gunicorn `--preload`, 1 worker | 6541cae (before) | with bounded I/O |
|---|---|---|
| hung (`podman pause`) | limited requests 30–31 s, then worker killed; `/health` queued 45–90 s; 10 worker kills in 2 min | worst request 1.1 s (login); `/health` ≤ 0.51 s; 0 kills |
| unreachable host (SYN unanswered) | every request including `/health` ≥ 30 s, abandoned connections queue → total outage; 23 kills | worst 0.71 s (self-check); `/health` ≤ 0.51 s; 0 kills |
| slow (`CLIENT PAUSE 2500`) | +2–2.5 s per Redis call, reported PASS | treated as unavailable: ≤ 1.2 s, reported FAIL |
| refused / DNS failure / OOM | fast, reported FAIL | unchanged: fast, reported FAIL |

**What changed:**
- **Bounded options.** `storage_options` passes `socket_connect_timeout=0.5`, `socket_timeout=0.5`, `retry_on_timeout=False` and an explicit zero-retry policy for `redis://`, `rediss://` and `redis+unix://`. 0.5 s is two orders of magnitude above in-region latency and survives one lost segment. **What is bounded is each socket wait** (connect and every read): a backend that STOPS answering costs a request a few waits, not 30 s. A peer that keeps trickling bytes is not bounded.
- *(Round 1 also added a two-failure stand-down and a cached self-check
  verdict. Both were removed in round 2 with the policy they served.)*
- **Own probe.** The app's probe pings itself under `except Exception`. The `limits` check wrapped PING in a bare `except:`, which swallowed gunicorn's SystemExit when it aborted a worker stuck on that call.
- **New check.** `ratelimit.bounded_io` reads the **effective** timeouts and retry count from the live connection pool. A URI query such as `?socket_timeout=9` or `?retry_on_timeout=true` silently overrides the constructor's options, and the check FAILs if that happens. It passes as not-applicable only for `memory://`; a network backend whose settings cannot be read FAILs.

**Residual risks (bounded I/O does not cover them):**
- A peer that trickles bytes slowly is not bounded: each read succeeds within the timeout, so a command can take arbitrarily long. This needs a misbehaving peer, not an outage.
- DNS resolution is not bounded by either timeout.
- A hostname with k addresses multiplies the connect bound by k.
- A backend slower than ~0.4 s per command stays "healthy" and slows the one worker. Login can pay several calls, ~2.4 s.
- Flask-Limiter's own recovery check still pings through `limits`' bare `except:`. It is now bounded to 0.5 s, so a worker abort there is no longer reachable in practice.
- A pooled connection that the network drops silently costs one 0.5 s timeout and one spurious fallback.

## One authority through a Redis failure (D7 round 2, 2026-10-04)

**Owner decision, 2026-10-04:** replace Option B. Its stand-down made every route except login and invite lookup **unlimited** during an outage. Measured on 6541cae: 12 of 12 registrations against 5/minute; in the lab matrix below, 27.

### Policy (binding)

1. A Redis failure **never disables rate limiting**. Every route keeps its own limits, enforced from this process's memory. With the required topology (one instance, one sync worker) that in-process count is the authority.
2. **Login** goes to the strict cap (3 attempts/min per IP) on the **first** failure, on top of its ordinary limits.
3. **Invite lookup** returns 503 on the **first** failure.
4. No allowance is split across a transition, in either direction, however often Redis flaps.
5. One probe in flight at a time. Bounded I/O: 0.5 s per socket wait, zero retries.

### How (`_RateLimitGate`, app.py)

A `redis://`, `rediss://` or `redis+unix://` URI is served through the gate: the Limiter is given `streakfit+redis://…` and the gate wraps `limits`' own `RedisStorage`. Every count is kept twice, in Redis and in a process-local mirror (`_LocalWindows`), on every hit.

| state | every hit | transitions |
|---|---|---|
| HEALTHY | mirror + Redis. The answer is **max(redis, mirror)**. If Redis is behind (it was away, or lost data), it is brought up to the mirror's count within the mirror's window. | **Any** exception from any Redis call → DEGRADED, immediately. |
| DEGRADED | Mirror only; requests never touch Redis. | At most one probe every 5 s, single-flight, made by whichever limited request arrives first: one counted write. Success: `successes += 1`. Failure: `successes = 0`. **3 consecutive successes → HEALTHY.** A probe that overran its bounds (DNS) spaces the next by 9× its duration, capped at 120 s. A success from a probe that began before a failure seen elsewhere is discarded. |

**Why switching cannot hand out an allowance:**
- At healthy → degraded, the mirror already holds every hit, so the count carries on. Flask-Limiter's own fallback started from **zero**, which is why round 1 was weaker.
- At degraded → healthy, `max()` still includes the outage's hits.

Security therefore does not depend on *when* the switch happens, and the recovery parameters are availability choices:

- **Probe cost.** A probe is one bounded call: ≤0.5 s of the only worker every 5 s, so ≤10% under saturating traffic, and one request in five seconds waits. At a 2 s interval it would be 25%.
- **Flapping.** Redis must stay writable for ≥10 s (≥15 s after the last failure seen) before traffic goes back to it. Shorter on/off cycles keep every limit on the in-process authority for the whole episode.
- **Cost of a long tail.** While degraded, lookup refuses and login is strict, so recovery is not slower than it needs to be: 15–20 s after Redis is stable.

The gate **never raises**, so Flask-Limiter's fallback and its after-request `deduct_when` error path are never engaged. If anything ever did escape, the fallback counts from zero; the app then treats itself as degraded and `ratelimit.outage_policy` FAILs.

### Monitoring

| check | healthy | degraded |
|---|---|---|
| `ratelimit.shared_storage` | PASS, "shared backend is the authority (mirrored in process)". A counted write through the gate. | FAIL, "in-process authority for Ns after <Error>; recovery k/3 qualifying probes". **No I/O.** |
| `ratelimit.outage_policy` (new) | PASS: limiter enabled and gated | PASS (the policy is in force). FAIL if the limiter is disabled, a network backend is not gated, or Flask-Limiter's fallback is engaged. |
| `ratelimit.bounded_io` | effective timeouts and retries, from the live pool | unchanged |

Verification suite v11 asserts that `outage_policy` is PASS.

### Measured: real Valkey 8, gunicorn `--preload`, 1 sync worker, PostgreSQL

Harness: `e2e-campaign/evidence/d7/d7r2_matrix.py`. Each mode runs as follows, all inside one minute of the first counted hit:
- a healthy warm-up;
- the fault for 22 s;
- recovery under continued attack for 22 s;
- a `/health` poller throughout.

"Accepted" means the limiter let it through.

Candidate: every route at or under its **own** limit in **all 12 modes**:
- healthy, refused (restart empty), hung, black-holed, DNS failure, slow, OOM, read-only replica, paused writes, 1.5 s blip, flapping 2/2 s, flapping 1/12 s;
- after the fault, login ≤ 3 and lookup 0 (503);
- 0 worker kills, worst request 0.73 s, `/health` ≤ 0.58 s;
- every runtime fault recovered to PASS within the run.

The full per-route table and the 6541cae / b045172 comparison are in `e2e-campaign/evidence/d7/round2/`.

### What this is not (residual risks)

- **The mirror is per process.** A worker restart **during** an outage loses the counts Redis never saw. Bounded I/O is what keeps gunicorn from causing those restarts, except for the DNS and trickle cases below. With more than one worker or instance, each would keep its own mirror. **One worker is a requirement**, not a tuning choice.
- **DNS is not bounded.** With a dead resolver, glibc measured 20 s (one nameserver plus a search domain) to 40 s (two nameservers). Probes then hold the worker ~10% of the time with the proportional backoff. A request on the HEALTHY path that has to resolve a new connection can still exceed gunicorn's 30 s and get the worker killed.
  - Render's internal Key Value hostnames rely on the search list.
  - An optional mitigation that is infrastructure, not code: `RES_OPTIONS="timeout:1 attempts:1"` on the service measured 40 s → 4 s. **Owner decision.**
- **A peer that trickles bytes** is not bounded (needs a misbehaving peer, not an outage).
- **A backend slower than ~0.45 s per reply** never degrades and slows every limited request; login can pay ~5 round trips.
- **The connect timeout (0.5 s) is shorter than Linux's initial SYN retransmit (1 s).** One lost SYN while opening a new connection is a false failure: ≥15 s degraded (lookup 503, strict login), with no security cost. The pool keeps one long-lived connection, so this is rare. Raising it to 1.0 s doubles a black-holed probe's cost. **Owner decision; not changed.**
- **Mirror memory grows with distinct keys**, about 216 B each. Keys live as long as their window (up to a day for coach). It is the same order as Flask-Limiter's own fallback would hold.
- **Eviction policies** (`allkeys-lru`) can drop Redis keys silently. With one worker, `max()` with the mirror covers it.
