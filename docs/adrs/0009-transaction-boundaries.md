# ADR-0009: Transaction boundaries — stage vs commit ownership
- **Status:** Accepted
- **Date:** 2026-07-25
- **Deciders:** StreakFit engineering

## Context / Problem
The atomic coach persistence in [ADR-0004](0004-atomic-coach-persistence.md)
requires composing several writes — the turn pair, the prune, and the note
update — into one transaction with one commit. That only works if the individual
write helpers do **not** commit on their own; a helper that self-commits would
split the "one transaction" into several and defeat the atomicity. But the same
helpers are also convenient to call standalone from the CLI and tests, where a
self-committing wrapper is exactly what you want. The codebase needs a consistent
rule for who owns the commit, plus a way to handle the one place where a nested
write can legitimately fail (the CoachNote get-or-create race) without poisoning
the surrounding transaction.

## Decision
Adopt an explicit convention:

- **`_stage_*` helpers flush, never commit.** `_stage_coach_exchange` and
  `_stage_coach_note` add/delete rows and call `db.session.flush()` only. They
  document that "the caller owns the commit." This makes them freely composable
  into a single larger transaction.
- **The caller owns the single commit.** `_persist_coach_interaction` calls the
  stagers and then commits exactly once, rolling back and re-raising on failure.
  The self-committing wrappers (`_record_coach_exchange`, `_update_coach_note`)
  exist only for direct/CLI/test use and are kept clearly separate.
- **`begin_nested()` (SAVEPOINT) only for the CoachNote get-or-create race.**
  `_get_or_create_coach_note` wraps its speculative `INSERT` in
  `db.session.begin_nested()` so that if a concurrent request already created the
  row, the resulting `IntegrityError` rolls back only the SAVEPOINT — not the
  outer transaction — and the loser recovers the winner's row. This is the sole
  place a SAVEPOINT is used; everywhere else is flat flush-then-commit.

## Alternatives considered
- **Every helper self-commits.** Why it lost: it makes atomic composition
  impossible — you could not persist turns+prune+note as one transaction, which
  is the whole point of [ADR-0004](0004-atomic-coach-persistence.md). Partial
  state on failure would be back.
- **No flush in the stagers** (let commit flush everything). Why it lost: the
  prune step needs the just-added turns to be visible to its ordering/offset
  query, and `_stage_coach_note` needs the get-or-create row to exist; an
  explicit `flush()` makes staged rows queryable within the transaction without
  committing them.
- **Wrap the whole coach persist in one big `begin_nested()`** instead of
  targeting the race. Why it lost: SAVEPOINTs add overhead and complexity; only
  the get-or-create insert can raise an `IntegrityError` that must be contained,
  so that's the only place that needs one. A flat transaction is simpler for the
  rest.

## Why the current solution won
A single, stated rule — stagers flush, the caller commits — makes it obvious how
to combine writes atomically and where a transaction begins and ends, which is
precisely what the atomic-persistence guarantee depends on. Confining
`begin_nested()` to the one genuine concurrency race keeps the common path flat
and cheap while still making the get-or-create safe against the first-write
collision. The dual wrappers (staging vs self-committing) let scripts and tests
stay ergonomic without tempting the request path into multiple commits.

## Consequences & future tradeoffs
- **Makes easy:** composing multiple writes into one transaction; reading a call
  site and knowing where the commit is; safe concurrent CoachNote creation.
- **Makes hard:** the convention is a discipline the compiler can't enforce — a
  new `_stage_*` helper that accidentally commits, or a request path that calls a
  self-committing wrapper mid-transaction, silently breaks atomicity. Naming
  (`_stage_` vs the self-committing wrappers) is the only guard.
- **When we'd revisit:** if more get-or-create races appear, a small shared
  SAVEPOINT-based upsert helper would beat repeating the pattern; if the staging
  convention proves error-prone, a context-manager that owns the commit
  explicitly could make the boundary structural rather than by-convention.

## Code references
- `app.py:3471-3485` — `_stage_coach_exchange`: flush-only, "caller owns the
  commit."
- `app.py:3403-3410` — `_stage_coach_note`: flush-only.
- `app.py:3384-3400` — `_get_or_create_coach_note`: `db.session.begin_nested()`
  SAVEPOINT, `IntegrityError` recovery — the only SAVEPOINT in the module.
- `app.py:3496-3518` — `_persist_coach_interaction`: the caller that owns the
  single commit and the rollback+re-raise.
- `app.py:3488-3493`, `3413-3424` — the self-committing CLI/test wrappers, kept
  separate from the request path.
- See [ADR-0004](0004-atomic-coach-persistence.md),
  [`../architecture/coach-subsystem.md`](../architecture/coach-subsystem.md), and
  [`../architecture/data-model.md`](../architecture/data-model.md).
