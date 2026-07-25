# StreakFit Architecture Decision Records

An **Architecture Decision Record (ADR)** captures a single significant
engineering decision: the problem that forced a choice, the option we picked,
the options we rejected and *why*, and the consequences we now live with. It is
written once, at decision time, and left immutable — when a decision changes we
add a new ADR that supersedes the old one rather than editing history. ADRs are
the "why" companion to the architecture docs in [`../architecture/`](../architecture/README.md),
which describe how the system is built *today*.

These records cover the StreakFit backend, which is a single Flask module
(`app.py`, ~4000 lines). Every ADR cites the exact functions and constants in
`app.py` it describes, and each was verified against the code, not the summary
it came from.

## Index

| # | Title | Status | Summary |
|---|---|---|---|
| [0001](0001-single-file-flask-monolith.md) | Single-file Flask monolith | Accepted | The whole backend is one `app.py` module; deliberate for a solo dev with a small surface. |
| [0002](0002-server-owned-conversation-history.md) | Server-owned conversation history | Accepted | The coach ignores client-sent history and loads its own rolling 10-turn window from the DB. |
| [0003](0003-coach-notes-deterministic-extraction.md) | Coach Notes via deterministic extraction | Accepted | Long-term memory is regex-extracted from the user's own words; the model has no memory-write tool. |
| [0004](0004-atomic-coach-persistence.md) | Atomic coach persistence | Accepted | Turns, prune, and note update commit as one transaction; no partial coach state is ever left behind. |
| [0005](0005-in-process-weather-cache.md) | In-process weather cache | Accepted | Per-worker TTL caches cut our own calls to Open-Meteo after Render's shared egress IP hit its 429 limit. |
| [0006](0006-application-level-account-deletion.md) | Application-level account deletion policy | Accepted | Deletion cascades private data, anonymizes shared team data, and blocks on team ownership — enforced in the app, not the DB. |
| [0007](0007-qa-cleanup-safe-vs-blocked.md) | QA cleanup safe-vs-blocked philosophy | Accepted | The smoke-account cleanup uses exact Python prefix matching, dry-run by default, and never deletes a team owner. |
| [0008](0008-security-headers-and-login-timing.md) | Security headers & login-timing hardening | Accepted | Cheap defense-in-depth: security headers, a body-size cap, constant-time admin compare, and constant-time login. |
| [0009](0009-transaction-boundaries.md) | Transaction boundaries — stage vs commit ownership | Accepted | `_stage_*` helpers only flush; the caller owns the single commit; a SAVEPOINT guards one get-or-create race. |
| [0010](0010-migrations-single-source-of-truth.md) | Migrations as the single source of truth + boot guard | Accepted | No `create_all()` on boot; migrations run as a deploy step and the process refuses to start if the DB isn't at head. |

## Template

Every ADR in this directory uses the following structure verbatim:

```
# ADR-NNNN: <Title>
- **Status:** Accepted
- **Date:** 2026-07-25
- **Deciders:** StreakFit engineering

## Context / Problem
## Decision
## Alternatives considered
(for each alternative: what it was, and why it lost)
## Why the current solution won
## Consequences & future tradeoffs
(what this makes easy, what it makes hard, when we'd revisit)
## Code references
(exact functions/lines in app.py)
```
