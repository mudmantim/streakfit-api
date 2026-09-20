# Mudman Command qualification — baseline, and what a score actually is

**Date:** 2026-09-20 · **Branch:** `product-completion`, local only, not pushed.
No production change, no Command change, no paid calls.

---

## 1. The headline finding, before anything else

**The Mudman Command "score" is not produced by any assessment.** It is a
weighted mean of five category scores that a human types into the Command
console. `src/lib/scoring.ts` computes the mean;
`src/server/project-actions.ts:417` writes the numbers, from `formData`, behind
a session check. **Nothing automated writes them.** There is no path from the
verification runner to the score.

So improving StreakFit's code cannot raise the number. Only a person
re-scoring the five categories can. What code can do is make that re-score
*justified*, and provide the evidence for it — which is what the rest of this
report is.

## 2. The "68/100" — not what the evidence says

I could not find 68 anywhere. What I found instead, recovered from Command's
own database backup (`backups/mudman_command-20260727T140411Z-pre-pgp-closeout.dump`,
read offline — no local Postgres is running and I did not touch production):

| Category | Score | Weight | Rationale recorded at the time |
|---|---:|---:|---|
| Product completeness | 75 | 3 | "Core loop, teams, coach, and admin all work end to end. Held back by four half-built features that are neither finished nor removed." |
| Production readiness | 80 | 3 | "Migration bootstrap fixed and proven from empty on Postgres; app refuses to boot off-head. **Rate-limit storage is still memory:// and resets per deploy**" |
| Visual polish | 70 | 2 | "First-minute polish shipped 2026-07-24… Still one 61KB hand-written stylesheet." |
| Testing confidence | 55 | 2 | "**69 tests** plus a verification suite that runs safely against production… several subsystems still have none." |
| Business readiness | 20 | 1 | "Plus tier exists in the product with no billing behind it. No pricing, no payments, no support process." |

```
(75×3 + 80×3 + 70×2 + 55×2 + 20×1) ÷ 11  =  735 ÷ 11  =  66.8  →  67
```

- **The score was 67, not 68**, at the time of that backup.
- **Assessed 2026-07-25** — every category carries that timestamp.
- **Target:** the deployed app, `https://streakfit.pro` (the project's stored
  links), not this branch.
- **It was a human judgement**, not an execution. Rationale prose, entered in
  a form.
- **StreakFit has zero `VerificationRun` rows.** It has never been assessed by
  the runner at all.

The live database may hold 68 today — two months have passed and the score is
editable. **I could not check**: no local Postgres is running, and production
requires authentication I have not been authorised to use. If 68 is the
current number, something was re-scored after 2026-07-27 and this report's
arithmetic is one edit stale; the structural findings are unaffected.

## 3. A reproducible baseline cannot be produced today

`verifyApp(app: TargetApp)` only accepts apps registered in
`src/lib/verification/apps.ts`, which today lists **`porchlight` and
`the-strange-file` only**. (`npm run verify` is Command's own CI — lint,
typecheck, test, build — not an app assessment.)

Three blockers, all on Command's side, all re-verified in its code today
rather than taken from StreakFit's existing note:

1. **StreakFit is not registered** in `VERIFIED_APPS`.
2. **The auth-boundary probe is hardcoded to `/api/projects`** (`runner.ts:255`)
   — a PorchLight route.
3. **The login-throttle probe is hardcoded to `/api/auth/login`**
   (`runner.ts:325`) — StreakFit's login is `/api/login`.

**Registering StreakFit anyway would produce a false pass, not a baseline.**
The runner's own comment at the auth probe says a 401 there "is also what an
unknown path returns". StreakFit returns 401 for unknown `/api` paths, so the
probe would go green against a route StreakFit does not have — a check that
verifies nothing, which is worse than a missing one. I did not do it, and I
did not modify Command.

## 4. What StreakFit's own side reports — real output, not an estimate

This is the half I can evidence. A production-configured build, migrated
database, key present, `GET /api/verification/self`:

```
PASS     VERIFIED   db.reachable              query returned, 1119 accounts          critical
PASS     VERIFIED   db.migrations             stamped at w7x8y9z0a1b2, 19 revisions  critical
UNKNOWN  UNKNOWN    retention.recent          no sweep has ever been recorded        critical
PASS     VERIFIED   content.loaded            440 accepted items, 798 in the store   critical
PASS     OBSERVED   coach.configured          a key is configured
PASS     VERIFIED   exercises.loaded          90 exercises across 3 tiers            critical
FAIL     VERIFIED   ratelimit.shared_storage  storage is 'memory://'                 critical
```

`GET /api/build-identity`: 15/15 contract fields, `migration.state = ok`.

**Command's roll-up is "weakest link across critical checks — never an
average."** So on these numbers a verification run would come back **FAIL**,
and the five passes would not soften it. The two reasons are:

- **`ratelimit.shared_storage` — FAIL.** The exact defect the July rationale
  named, still true.
- **`retention.recent` — UNKNOWN.** The retention cron is not live (it is
  declared in `render.yaml`, which the dashboard-configured service never
  reads, and is marked INERT in the file itself).

Both are infrastructure decisions, not code defects, and both have been
flagged in earlier reports. What is new is that **they are now the thing
standing between StreakFit and a passing verification run**, which is a much
more concrete reason to resolve them.

## 5. Improvement implemented this phase

**`ratelimit.shared_storage`, a seventh self-check** — commit `0aac18e`.

The July rationale said rate-limit storage was `memory://`. It still is, and
**nothing anywhere surfaced it**, so it survived two months of work on
everything around it — including work on the subsystem it protects.

It is a security control here, not a politeness feature. The invite-code
lookup carries a comment recording a **measured 321 probes/second enumeration
oracle**, and the limiter is what stands in front of it. With `memory://` the
counters live in one process: they reset on every deploy and restart, and with
more than one worker each keeps its own, so the effective limit is silently
multiplied by the worker count.

**Reported, not fixed** — fixing it means provisioning shared storage, which
is infrastructure and the owner's call. The check FAILs as critical in
production and passes quietly in development, because a check that cries wolf
locally is one people learn to ignore. Where a shared backend *is* configured
it is exercised, not believed: a URI in an environment variable says nothing
about whether anything is listening.

*Writing the test found a weakness in my first version: `limiter.storage` is a
read-only property, so the unreachable branch could not be executed at all. A
check whose failure path has never run is one nobody should trust. The call
now goes through a single overridable seam and both branches are tested.*

## 6. Each July rationale, checked against today

Evidence a re-score can be based on. **These are not new scores** — I am not
authorised to set them and would not be the right one to.

| Category | What the rationale said | True today? |
|---|---|---|
| Product completeness | "four half-built features that are neither finished nor removed" | **Materially changed.** Since then: the acorn loop closed end to end, Side Quests gained rename/delete, display names shipped, the guest promise was made true, milestones made reachable. The specific four were not named in the rationale, so I cannot tick them off individually. |
| Production readiness | "rate-limit storage is still memory://" | **Still true.** Now visible in the self-check rather than invisible. |
| Visual polish | "one 61KB hand-written stylesheet" | **Worse by that measure** — `style.css` is now 109KB. Honest to report: the polish improved, the stylesheet grew. |
| Testing confidence | "69 tests… several subsystems still have none" | **596 tests**, plus 157 real-UI checks and 112 end-to-end checks that run safely against production. The largest genuine change of the five. |
| Business readiness | "no billing" | **Unchanged, deliberately.** You have instructed no billing be implemented. |

## 7. Remaining blockers and the approvals needed

| # | Blocker | Whose | Approval needed |
|---|---|---|---|
| 1 | StreakFit not registered in Command | Command | Three small edits to `~/projects/mudman-command`: add `authProbePath`/`throttlePath` to `TargetApp` **defaulting to today's PorchLight values so nothing else changes**, read them in `runner.ts` in place of the two literals, and register StreakFit with `healthPath: /api/health`, `authProbePath: /api/me`, `throttlePath: /api/login`. **Your call — I did not touch it.** |
| 2 | Shared rate-limit storage | Infrastructure | Provision Redis (or equivalent) and set `RATELIMIT_STORAGE_URI`. Until then a production verification run fails on a critical check. |
| 3 | Retention cron not live | Infrastructure | Create the Render Cron Job. Until then `retention.recent` is UNKNOWN, which Command's roll-up treats as "investigate". |
| 4 | Re-scoring the five categories | Human judgement | Nobody can do this from code. §6 is the evidence. |
| 5 | Assessment target | Deployment | The July assessment targeted `streakfit.pro`. This branch is not deployed, so even a registered runner would assess the *old* build until you push — which you have not authorised. |

**Separate from the score: check the qualification gates.** Command stores a
`recommendation` and a weakest-link `status`/`level` per run, deliberately not
an average. A higher score would not make StreakFit "qualified" while a
critical check fails. Blockers 2 and 3 are that gate.

## 8. How this fits the child-safe launch

The two blockers are the *same two items* that have been open in the
child-safety work, which is convenient rather than coincidental:

- **Shared rate-limit storage** is the control in front of invite-code
  enumeration. In a product where a team invite is how an adult reaches a
  child, a rate limiter that resets on every deploy is a child-safety control,
  not only a qualification checkbox.
- **The retention cron** is what actually deletes children's conversation
  turns. "Retention is implemented" and "retention runs" are different claims,
  and only the second one protects anybody.

Neither can be resolved inside this repository. Both are worth doing for the
launch regardless of what Command scores.

---

**No score is claimed anywhere in this report.** The baseline is 67 as of
2026-07-25 from a database backup; no current score was obtainable; and no
reassessment was run, because running one today would produce a false pass
rather than a measurement.
