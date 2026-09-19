# Mudman Command qualification — StreakFit

**Status: NOT QUALIFIED.** Run 2026-09-19 against `http://localhost:5000`.
7 checks pass, 2 fail, 1 unknown. The two failures are Mudman Command probing
paths that belong to PorchLight; resolving them is a change to Command and
therefore Tim's call.

---

## What Mudman Command actually is

`~/projects/mudman-command` — a Next.js operations console. Its README is
explicit: *"it **describes** the portfolio; it never reaches into another
project's repository."* It cannot run StreakFit's pytest, `uicheck.py` or
`verify_all.py`. It probes a **running application over HTTP**, and the three
endpoints below are the entire surface it sees.

Registration is gated: `src/lib/verification/apps.ts` says an app appears in
`VERIFIED_APPS` *"only once it implements the build identity contract"*.
StreakFit now implements it.

## The run

Command's own `verifyApp()` was invoked against StreakFit — its runner, not a
local imitation of it — from a script outside its repository, writing nothing
into it. Reproduce with:

```bash
make run                       # StreakFit on :5000
cd ~/projects/mudman-command
./node_modules/.bin/tsx /path/to/streakfit/scripts/qualify_with_mudman_command.mts
```

```
streakfit — FAIL (VERIFIED)
recommendation: roll back   cost: 0
build: streakfit 0.9.0 66b05cd · migrations 17 @ u5v6w7x8y9z0

! PASS    VERIFIED build.identity          streakfit development · 66b05cd · 17 migrations
! PASS    VERIFIED liveness                HTTP 200 in 10ms
! FAIL    VERIFIED auth.boundary           HTTP 404
! FAIL    VERIFIED security.login-throttle 404 ×7 — 0 allowed, then no refusal
! PASS    VERIFIED self.db.reachable       query returned, 582 accounts
! PASS    VERIFIED self.db.migrations      stamped at u5v6w7x8y9z0, 17 revisions
! PASS    VERIFIED self.content.loaded     614 accepted items, 614 in the store
  UNKNOWN UNKNOWN  self.coach.configured   no key configured
! PASS    VERIFIED self.exercises.loaded   90 exercises across 3 tiers
```

`self.coach.configured` is UNKNOWN because there is no `ANTHROPIC_API_KEY`. That
is the honest answer and it is non-critical by design: a key being present would
not establish that it works, and proving that costs money.

## The two failures are probe mismatches, and here is the evidence

Command's `checkAuthBoundary` requests **`/api/projects`** and
`checkLoginThrottle` posts to **`/api/auth/login`**. Both are PorchLight's
paths, hardcoded in `runner.ts`. Neither exists in StreakFit, so both 404 — and
a 404 is read as "the boundary did not hold".

StreakFit has both properties. Measured against the running app:

```
anonymous GET /api/me          -> 401
anonymous GET /api/daily       -> 401
anonymous GET /api/teams       -> 401
anonymous GET /api/challenges  -> 401
anonymous GET /api/projects    -> 404   (the path Command probes; no such route)

POST /api/login  x12 with a bad password:
401 401 429 429 429 429 429 429 429 429 429 429
```

The throttle is `10 per minute` on `/api/login`. Command sends seven, which is
under that ceiling — the run above shows 429 from the third attempt only because
an earlier probe had already consumed the window.

## What is NOT being done, and why

**`hasAuthentication` stays `true`.** Command skips both checks when an app
declares it `false`, which would turn this report green today. Its own framework
says *"silence must never reduce scrutiny"*, and StreakFit plainly has
authentication. Declaring otherwise would be dodging the check rather than
passing it, and it would remove the scrutiny permanently for a product used by
children.

**Command has not been modified.** Making it probe the right paths means editing
another project.

## Proposed change to Mudman Command — for Tim to approve or refuse

Two probe paths become per-app configuration, exactly as `healthPath` already
is. In `src/lib/verification/runner.ts`'s `TargetApp`:

```ts
  /** Route that must refuse an anonymous caller. Defaults to PorchLight's. */
  authProbePath?: string;
  /** Login endpoint, for the throttle ladder. Defaults to PorchLight's. */
  loginPath?: string;
```

…defaulting to the current values so PorchLight and The Strange File are
unaffected, and then in `apps.ts`:

```ts
  {
    id: "streakfit",
    label: "StreakFit",
    baseUrl: process.env.STREAKFIT_BASE_URL ?? "https://streakfit.pro",
    authProbePath: "/api/me",
    loginPath: "/api/login",
  },
```

This weakens nothing: every app still gets both checks, against a route that
exists. It is a change to another project's source, so it waits for you.

**A second question worth your attention:** `10 per minute` on login is 14,400
attempts per IP per day. It is not wrong, and it is looser than a framework
expecting refusal within seven attempts implies. Tightening it is a StreakFit
change and belongs in the Phase 2 security review rather than here.

## Not qualified

Seven of nine checks pass and the other two cannot run against this app yet.
Nothing here should be read as StreakFit having been qualified.
