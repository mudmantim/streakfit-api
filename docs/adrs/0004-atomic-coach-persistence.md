# ADR-0004: Atomic coach persistence
- **Status:** Accepted
- **Date:** 2026-07-25
- **Deciders:** StreakFit engineering

## Context / Problem
Persisting one coach interaction touches three things: the new user+assistant
turn pair, the prune that trims the window back to the last 10 turns
([ADR-0002](0002-server-owned-conversation-history.md)), and any Coach Notes
update extracted from the user's message ([ADR-0003](0003-coach-notes-deterministic-extraction.md)).
The earlier design did these as separate self-committing steps —
`_record_coach_exchange` committed the turns, then `_update_coach_note`
committed the note. Separate commits mean a failure between them leaves partial
state: turns saved but the note write lost, or (with a mid-prune failure)
duplicate/over-long windows. On the coach's hot path that partial state is
silent corruption of the user's memory.

## Decision
Persist the whole interaction in **one transaction with a single commit**.
`_persist_coach_interaction(user_id, user_msg, reply)` stages the turn pair and
prune (`_stage_coach_exchange`, flush-only), then stages the note update
(`_stage_coach_note`, flush-only) if extraction found anything, then commits
once. On any exception it rolls back and re-raises. Extraction is treated as
best-effort *enrichment*: if the pure-Python `_coach_note_extract` itself raises,
that is caught, logged, and the turns are still persisted. But a **database**
failure at commit is never swallowed — it rolls the whole thing back and
re-raises, so we never leave a half-written state or hide an integrity error.
The `coach()` route calls this inside its own guard, so even a re-raised persist
failure returns the reply to the user (rollback has already happened) while
being logged rather than silently eaten.

## Alternatives considered
- **Separate commits per concern** — the earlier `_record_coach_exchange` +
  `_update_coach_note` approach. What it was: commit the turns, then commit the
  note, each in its own transaction. Why it lost: it allows partial state
  (turns without their note, or a failed prune) whenever a step in between
  fails. It is retained only as CLI/test convenience wrappers (self-committing),
  never used on the request path.
- **Swallow all persistence errors** (best-effort everything). Why it lost: a
  DB/integrity error is a real signal — swallowing it hides corruption and makes
  the memory silently unreliable. Only the *extraction* step is allowed to fail
  soft; the *database* write is not.
- **Two-phase / compensating writes.** Why it lost: massive overkill for three
  writes against one database — a single local transaction gives exactly the
  atomicity needed with none of the machinery.

## Why the current solution won
One transaction is the simplest thing that makes the invariant true: after a
coach call, either all of {turns, prune, note} are persisted or none are. The
best-effort boundary is drawn in exactly the right place — around the fallible,
non-critical extraction, not around the database write — so memory enrichment
can degrade without ever producing partial or corrupt state, and a genuine DB
error still surfaces in logs.

## Consequences & future tradeoffs
- **Makes easy:** reasoning about coach memory — it is always internally
  consistent; no turns-without-note, no duplicate turns, no half-pruned window.
- **Makes hard:** the staging discipline must be honored — the `_stage_*`
  helpers must only flush, never commit, or the atomicity guarantee breaks (see
  [ADR-0009](0009-transaction-boundaries.md)). Two commit paths now exist for
  the same data (the atomic one for requests, the self-committing wrappers for
  CLI/tests); they must not be mixed within one request.
- **When we'd revisit:** if note extraction ever grows into something that can
  partially succeed in a way worth persisting independently, the single-commit
  boundary would need rethinking — but today extraction is all-or-nothing Python.

## Code references
- `app.py:3471-3485` — `_stage_coach_exchange`: stage the turn pair + prune,
  flush only, return pruned count (the caller owns the commit).
- `app.py:3488-3493` — `_record_coach_exchange`: self-committing wrapper, marked
  for direct/CLI/test use only.
- `app.py:3413-3424` — `_update_coach_note`: self-committing note wrapper, same
  CLI/test-only role.
- `app.py:3496-3522` — `_persist_coach_interaction`: single-transaction
  turns+prune+note, extraction failure swallowed, DB failure rolled back and
  re-raised.
- `app.py:3915-3921` — `coach()` calls `_persist_coach_interaction` inside a
  guard so a re-raised persist failure still returns the reply and is logged.
- See [`../architecture/coach-subsystem.md`](../architecture/coach-subsystem.md).
