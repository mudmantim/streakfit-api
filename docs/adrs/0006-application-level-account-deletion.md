# ADR-0006: Application-level account deletion policy
- **Status:** Accepted
- **Date:** 2026-07-25
- **Deciders:** StreakFit engineering

## Context / Problem
Deleting a user is not a single `DELETE FROM user`. A user's rows fall into three
policy classes: **private** data that should vanish with them (challenges,
completions, brain-boost answers, progress events, their own team memberships,
coach turns and notes); **shared** team content they authored or are the subject
of (team messages, team moments) that other members should keep seeing; and
**team ownership**, where a team is shared data whose other members must not have
their team silently torn down. Critically, **no foreign key in the database has
an `ON DELETE` action** (see [`../db_integrity_matrix.md`](../db_integrity_matrix.md)),
so a raw user delete would either FK-error (Postgres) or orphan children (SQLite
without FK enforcement). The policy has to be enforced *somewhere* deliberate.

## Decision
Enforce the deletion policy in the **application layer**, in one reusable
transactional service: `delete_user_account(user_id, allow_team_owner=False,
dry_run=True)`.

- **Private data → CASCADE-delete.** The `_USER_PRIVATE_DELETES` table lists the
  seven private child models; each is deleted by `user_id`, then the user row.
- **Shared team data → SET NULL (anonymize).** `team_message.sender_user_id` and
  `team_moment.subject_user_id` are nulled, so the message/moment survives in the
  team's history with the author/subject link dropped. (Both columns are nullable
  by design — Rickie messages already have no sender.)
- **Team ownership → BLOCK.** If the user owns any team, deletion is refused
  unless `allow_team_owner=True` is passed as an explicit policy (the caller
  having already dealt with the owned teams). The service never tears down a
  shared team on its own.
- **One transaction; idempotent; dry-run by default.** All writes are in a single
  transaction that rolls back and re-raises on any failure. `dry_run=True`
  (default) reports the plan and counts without changing anything; a missing user
  returns `found: False` rather than erroring.

The DB-level `ondelete` constraints that mirror this policy are a **deferred,
belt-and-suspenders** follow-up migration — the app layer already enforces the
policy safely, so the schema change is defense-in-depth, not a correctness
blocker (see [`../db_integrity_matrix.md`](../db_integrity_matrix.md)).

## Alternatives considered
- **DB-level `ON DELETE` constraints as the enforcement mechanism.** Why it lost
  *as the primary control*: it's a schema migration against production data
  (SQLite can't `ALTER` a constraint in place; it needs a batch table-rebuild),
  and it can't express the "block on team ownership" rule — that's an
  application policy, not a cascade. It remains the recommended defense-in-depth
  layer, deferred, not the source of truth today.
- **Raw `DELETE FROM user`.** Why it lost: with no `ON DELETE` actions it orphans
  or FK-errors, and it can't distinguish private from shared data — it would take
  other members' team history down with the user.
- **Hard-delete everything the user touched** (including shared messages/moments).
  Why it lost: destroys other people's shared team record; the correct treatment
  of shared authored content is anonymize (SET NULL), not delete.

## Why the current solution won
The policy has three genuinely different classes of data and one of them (team
ownership) requires a *judgment* the database can't make, so the enforcement
belongs in code where all three can be expressed together, tested, and reused. A
single transaction gives all-or-nothing safety; dry-run-by-default makes the
service safe to call for planning; and centralizing it in one function means the
CLI cleanup ([ADR-0007](0007-qa-cleanup-safe-vs-blocked.md)) and any future
self-serve deletion endpoint share exactly one audited implementation.

## Consequences & future tradeoffs
- **Makes easy:** correct, testable deletion that protects shared data by
  construction; one reusable service for scripts and a future endpoint; safe
  planning via dry-run and read-only dependency counts.
- **Makes hard:** the guarantee lives in code, so any *new* table referencing a
  user must be added to `_USER_PRIVATE_DELETES` (or the shared handling) or its
  rows will be missed — the DB won't catch the omission because there are no
  `ON DELETE` constraints yet. The parallel policy in code and the (deferred) DB
  migration must be kept in sync when that migration lands.
- **When we'd revisit:** ship the deferred `ondelete` migration for
  belt-and-suspenders; if a self-serve account-deletion endpoint is added, wire
  it to this same service with an explicit team-owner policy step.

## Code references
- `app.py:3540-3548` — `_USER_PRIVATE_DELETES` (the seven private child models).
- `app.py:3551-3561` — `_account_dependent_counts` (read-only plan, including
  `team_message_authored`, `team_moment_subject`, `team_owned`).
- `app.py:3564-3612` — `delete_user_account`: dry-run default, team-owner
  blocker, SET NULL of shared links, private deletes, single transaction with
  rollback+re-raise, idempotent `found: False`.
- `app.py:3596-3600` — the SET NULL updates for `team_message.sender_user_id`
  and `team_moment.subject_user_id`.
- See [`../db_integrity_matrix.md`](../db_integrity_matrix.md) for the full FK
  matrix and the deferred migration, and
  [`../architecture/data-model.md`](../architecture/data-model.md).
