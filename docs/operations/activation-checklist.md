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

   Expect `"status": "PASS"` and `"shared backend reachable (redis)"`. If it
   still says `memory://`, the variable did not reach the process.

### Cost — check this, do not take it from me

Render's Key Value has historically offered a free tier (small, no
persistence) and paid tiers from roughly a few dollars a month for the
smallest persistent instance.

**I have not verified current pricing and you should not treat these figures
as quotes.** Check the Render pricing page before provisioning. What I can say
with confidence is the *shape* of the requirement: rate-limit counters are
small and short-lived, so the smallest available instance is sufficient —
this is not a sizing problem.

A free, non-persistent tier would still be a large improvement over
`memory://`, because it is shared across workers even if it does not survive
a restart of the store itself.

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
`shared backend reachable`. A configuration that was applied is not the same
claim as a control that is working, and both of those endpoints exist
precisely so the difference is checkable.
