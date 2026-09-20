# Mudman Command qualification — what StreakFit needs

Mudman Command (`~/projects/mudman-command`, production
`https://command.mudmantimsapps.com`) describes the portfolio and verifies apps
by probing a **running app over HTTP**. It never reaches into a repository, so
it cannot run StreakFit's pytest, `uicheck` or `verify_all`.

**Current status: StreakFit cannot be qualified by Command today.** Not because
StreakFit is missing anything — its side is complete — but because of two
things in Command itself. Those are recorded here and **not changed**, because
changing Command is not this repository's call.

> **Updated 2026-09-20.** A seventh self-check was added,
> `ratelimit.shared_storage`, and on a production-configured build it **FAILS**
> — see `docs/reports/2026-09-20-mudman-command-qualification.md`. Command's
> roll-up is the weakest critical check, so StreakFit would not pass a
> verification run today even once registered. The blockers below are still
> accurate and were re-verified in Command's code on that date.

## StreakFit's side — complete and verified locally

| Requirement | Status |
|---|---|
| `GET /api/build-identity` — the 15-field `BuildIdentity` contract | **Done.** All 15 fields emitted: schemaVersion, application, version, gitSha, gitBranch, buildDate, environment, deploymentId, instanceId, migration `{latest, appliedCount, state}`, storageProvider, featureFlags, apiVersion, verificationFrameworkVersion, healthTimestamp |
| `GET /api/verification/self` — the app's own checks as JSON | **Done.** Returns `{application, checks, generatedAt}` with 5 checks. The runner reads `payload.checks` only; a top-level `status` is **not** required (`runner.ts` line ~391) |
| `GET /api/health` | **Done.** `/health` also still works; `healthPath` is per-app configurable |
| Covered by tests | `tests/test_qualification_endpoints.py` (10 tests) |

### The self-checks and what they report

Against a database built by `flask db upgrade`, with an API key present:

```
PASS  db.reachable      query returned, N accounts
PASS  db.migrations     stamped at <rev>, 18 revisions in the chain
PASS  content.loaded    440 accepted items, 798 in the store
PASS  coach.configured  key present
PASS  exercises.loaded  90 exercises across 3 tiers
```

Two of these report `UNKNOWN` in conditions that are **correct, not broken**,
and it is worth writing down because both look like defects at a glance:

- **`db.migrations` is UNKNOWN on a `create_all()` database.** Such a database
  carries no Alembic stamp, so its schema currency genuinely is unknown. Every
  pytest fixture builds that way. A probe run against one will report UNKNOWN,
  and that is the honest answer — `_migration_state` is written never to guess.
  Both halves are pinned by
  `tests/test_migrations.py::test_self_check_reports_schema_currency_only_when_it_really_knows`.
- **`coach.configured` is UNKNOWN with no `ANTHROPIC_API_KEY`.** The UI harness
  deliberately runs without one so browser work costs nothing.

Command's roll-up turns UNKNOWN into "investigate", so **a qualification run
must target a migrated database with the key present** — i.e. production, or a
local server started the way production starts.

## What is blocking, and it is all on Command's side

**1. StreakFit is not registered.** `src/lib/verification/apps.ts` lists
`porchlight` and `the-strange-file` only. Command's own note says an app appears
"only once it implements the build identity contract" — StreakFit now does, so
this is a registration entry, not a capability gap.

**2. The auth-boundary probe is hardcoded to `/api/projects`** (`runner.ts`
~line 255). That is a PorchLight route. StreakFit has no such path.

**3. The login-throttle probe is hardcoded to `/api/auth/login`** (~line 325).
StreakFit's login is `/api/login`.

`healthPath` is already per-app configurable; these two are not.

### Why running it anyway would be worse than not running it

The runner's own comment at the auth probe says: *"A 401 here is also what an
unknown path returns, so this proves the boundary holds, not that the route
exists."* StreakFit returns 401 for unknown `/api` paths — so the auth-boundary
probe would **pass against a route StreakFit does not have**. That is a green
check that verifies nothing, which is worse than a missing one, and it is
exactly the kind of false assurance the framework's own "silence must never
reduce scrutiny" line is about.

Declaring `hasAuthentication: false` would dodge both probes and is the wrong
answer for the same reason.

## What it would take — a decision for the owner

This needs a change to `~/projects/mudman-command`, which is out of scope here
without explicit approval:

1. Add `authProbePath` and `throttlePath` to the `TargetApp` type, defaulting to
   the current PorchLight values so nothing else changes.
2. Read them in `runner.ts` in place of the two literals.
3. Register StreakFit in `apps.ts` with `baseUrl` from an env var (the existing
   pattern), `healthPath: "/api/health"`, `authProbePath: "/api/me"`,
   `throttlePath: "/api/login"`.

`baseUrl` comes from an env var, so a qualification run can target
`http://localhost:5000` without deploying anything.

## Do not claim qualification without a run

There is no evidence of Command having verified StreakFit, because it has not.
Until there is a run with output, the accurate statement is: *"StreakFit
implements the contract; it has not been qualified."*
