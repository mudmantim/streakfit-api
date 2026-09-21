# Two things to switch on, and exactly how

Both are infrastructure. Neither can be done from this repository, and both
are currently the reason a Mudman Command verification run against a
production-configured StreakFit comes back **FAIL / "roll back"**.

The application side of both is finished, committed and tested. What follows
is the part that needs your hands and, for one of them, your money.

**Nothing here has been done.** No Render resource was created, no environment
variable was set, and no cost was incurred.

---

## 1. Shared rate-limit storage

### Why it matters

Rate limiting is the control in front of two things that matter:

- **`/api/login`** — brute-force guessing. Now throttled at five failed
  attempts a minute and thirty an hour (commit `d7f1721`).
- **`/api/teams/lookup/<code>`** — invite-code enumeration. The route carries
  a comment recording a **measured 321 probes/second** oracle. In a product
  where a team invite is how an adult reaches a child, this is a child-safety
  control.

With the default `memory://`, those counters live in one process. **They reset
on every deploy and every restart, and each worker keeps its own** — so with
N workers the effective limit is N times what it says.

### What was prepared

| | |
|---|---|
| `redis==5.0.8` pinned in `requirements.txt` | **The driver was missing.** Setting `RATELIMIT_STORAGE_URI=redis://…` raised `ConfigurationError` at import and the app did not start — so the configuration everyone assumed was available was, in fact, impossible to apply. |
| `swallow_errors=True` on the limiter | Measured: with the driver present and the backend refused, **every login returned 500**. A rate limiter must not be able to take the application down. |
| `ratelimit.shared_storage` self-check | FAILs as critical in production for `memory://` **and** for a configured-but-unreachable backend. |

### The residual risk, stated plainly

`swallow_errors=True` means that **while the backend is unreachable, no rate
limiting is applied at all** — not on login, not on invite lookup. The app
stays up and the self-check reports FAIL, so it is visible rather than silent,
but it is a real trade and it is yours to accept or reverse.

If you would rather fail closed — 500s instead of an unlimited window — remove
`swallow_errors=True`. I would not: a habit app that nobody can log into is a
worse outcome than a visible, reported gap, and the people it locks out are
the ones whose streaks depend on showing up.

### Exact production configuration

1. Render dashboard → **New → Key Value** (Render's Redis-compatible offering).
2. Same region as the StreakFit web service.
3. Eviction policy: **`noeviction`**. An LRU policy can silently drop rate-limit
   counters, which reopens the window it exists to close.

   Now measured, and it settles the trade-off rather than leaving it to
   judgement. With `noeviction`, a full instance refuses writes and the
   self-check reports FAIL — loud, and you find out. With `allkeys-lru` the
   writes succeed and the counters simply erode, which **nothing can detect
   from inside the app**. Prefer the failure you can see. See
   [rate-limit-backend-outage.md](rate-limit-backend-outage.md).
4. Copy the **internal** connection URL.
5. StreakFit web service → Environment → add:

   ```
   RATELIMIT_STORAGE_URI = redis://<internal-url-from-step-4>
   ```

6. Redeploy. Confirm with:

   ```
   curl -s https://streakfit.pro/api/verification/self \
     | jq '.checks[] | select(.id=="ratelimit.shared_storage")'
   ```

   Expect `"status": "PASS"` and `"shared backend counting (redis)"`. If it
   still says `memory://`, the variable did not reach the process.

   "Counting", not "reachable", is deliberate: the check increments a probe
   key rather than sending a ping, because a memory-capped plan that is full
   answers a ping and refuses every write. Measured — that state used to
   report PASS while no limit could be recorded at all.

### Plan — FREE, decided 2026-09-21

**Decision: the free Key Value plan**, with `noeviction`. Paid is not needed
and is not to be provisioned without separate approval.

Verified against Render's documentation (2026-09-21), not inferred:

| | Free Key Value |
|---|---|
| Memory | **25 MB** |
| Persistence | **None.** "whenever an instance restarts, all of its data is lost" |
| Restarts | Render "might restart a Free Render Key Value instance at any time (thereby deleting its data)" |
| Idle spin-down | **No.** Unlike free web services, free Key Value has no 15-minute idle spin-down |
| Instances | **One free instance per workspace** — check nothing else is using it |
| Maxmemory policy | Selected at creation, changeable later; `noeviction` is offered. The docs state this generally with no free-plan exception |

**Why free is sufficient here.** The security gap this closes is that
`memory://` is per-worker, so every limit is multiplied by the worker count in
front of an invite-code lookup with a measured 321 probes/second enumeration
oracle. The free plan is *shared*, which closes exactly that. It does not
persist across a restart — but neither does `memory://`, which loses its
counters on **every deploy** and partitions them besides. Free is strictly
better on both axes, at no cost.

**What free does not buy, stated plainly.** Counters are cleared whenever
Render restarts the instance, at a time you do not control and will not be
told about. An attacker cannot *cause* that restart, but one that happens
mid-attack hands back a fresh budget. Durable throttling across restarts is a
paid-plan property, and if that ever becomes the requirement it is a
deliberate upgrade decision — note that upgrading free→paid also loses the
data in transit.

**25 MB with `noeviction`.** Rate-limit keys are small and expire on their
own, so ordinary use is nowhere near the cap. A sustained flood of distinct
keys could reach it, and then writes are refused rather than counters silently
dropped — which `ratelimit.shared_storage` now detects and reports as FAIL,
degrading to the per-process cap. Loud, and that is the point of `noeviction`
over `allkeys-lru`.

**Confirm at creation:** that the Maxmemory Policy dropdown offers
`noeviction`, and that the workspace's one free instance is not already
spoken for. If either is not true, stop — do not upgrade to a paid plan to
work around it without asking.

The restart behaviour is safe to rely on rather than something to hope about:
an instance going away degrades instead of erroring, and recovers on its own
without a redeploy. Measured against a real Valkey 8, the engine Render Key
Value runs — see [rate-limit-backend-outage.md](rate-limit-backend-outage.md).
That measurement is what makes the free plan's "restarts at any time" an
acceptable property rather than an unknown one.

---

## 2. The conversation-retention cron

### Why it matters

`_COACH_TURN_MAX_AGE_DAYS` is 30. That is the promise the data export makes
about how long a child's Ask Rickie conversations are kept. Nothing enforces
it on a schedule today.

### What was verified, today, locally

The command runs **with the web service stopped**, which is the whole point of
moving it out of the request path:

```
$ (web server stopped)
$ flask coach-prune
deleted 0 coach turns older than 30 days
```

And it records the run, so the self-check can see it:

```
before:  UNKNOWN  retention.recent  no sweep has ever been recorded
after:   PASS     retention.recent  last swept 0.0h ago via cron, 0 deleted
```

**That is the whole chain proven end to end** — the command, the recording,
and the check that reads it. The only missing piece is a scheduler calling it.

### Exact activation steps

`render.yaml` is **inert** — the service is dashboard-configured and never
reads that file. The cron must be created in the dashboard.

1. Render dashboard → **New → Cron Job**.
2. Name: `streakfit-coach-prune`.
3. Same region and same environment as the web service.
4. Link the same repository and branch.
5. Build command: `pip install -r requirements.txt`
6. Command:

   ```
   flask coach-prune
   ```

7. Schedule: `17 3 * * *` (03:17 UTC daily — off the hour, so it does not
   contend with everything else that runs at midnight).
8. Environment: the cron needs the **same `DATABASE_URL`, `SECRET_KEY` and
   `JWT_SECRET_KEY`** as the web service. It does **not** need
   `ANTHROPIC_API_KEY` — it never calls the model.

### How to verify it actually ran

Do not trust the dashboard's green tick alone. The application records every
sweep in `retention_run`, and the self-check reads it:

```
curl -s https://streakfit.pro/api/verification/self \
  | jq '.checks[] | select(.id=="retention.recent")'
```

- `PASS` with `last swept Nh ago via cron` — working.
- `FAIL` — it ran once and then stopped. This is the case a dashboard tick
  will not tell you about, and it is why the check exists.
- `UNKNOWN` with `no sweep has ever been recorded` — the job has never run.

### Cost

Render cron jobs are billed by run time. This one is a single indexed DELETE
and exits immediately. **Negligible, but confirm on the pricing page** rather
than taking my word for it.

---

## After both are done

Re-run a Mudman Command verification. On the current build every other check
already passes, so these two are what stand between StreakFit and a clean run.

**Do not mark retention "operational" until the self-check has reported PASS
from production**, and do not mark rate limiting done until it reports
`shared backend counting`. A configuration that was applied is not the same
claim as a control that is working, and both of those endpoints exist
precisely so the difference is checkable.
