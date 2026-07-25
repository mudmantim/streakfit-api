# ADR-0002: Server-owned conversation history
- **Status:** Accepted
- **Date:** 2026-07-25
- **Deciders:** StreakFit engineering

## Context / Problem
Rickie (the `/api/coach` endpoint) is a conversational fitness coach backed by
Claude Sonnet 5. A conversational model needs prior turns to stay coherent
across a session and across devices. The obvious approach — have the PWA send
its local chat history in the request body — hands control of the model's
context to the client, which is both untrustworthy (a client can send anything)
and inconsistent (a user on two devices, or with cleared local storage, has
divergent history). It also caused a concrete bug: across similar prompts Rickie
repeated near-identical phrasing, because each request rebuilt context from
scratch and he never saw his own recent replies.

## Decision
The server owns conversation history. `coach()` **ignores any `history` the
client sends** and instead loads this user's rolling window of the last
`_COACH_MEMORY_WINDOW` (= 10) turns from the `coach_turn` table via
`_load_coach_messages(user_id)`, appends the current user message, and sends
that to the model. Stored turns are capped at `_COACH_TURN_PROMPT_LEN` (= 600)
characters when threaded into the prompt. The window is made alternation-safe
before it reaches the model: rows with an invalid role or empty content are
dropped, consecutive same-role turns are collapsed, and any leading assistant
turn is trimmed so the sequence always starts with a user turn.

## Alternatives considered
- **Client-sent history.** What it was: the PWA posts its local transcript with
  each request. Why it lost: untrusted input straight into the model's context
  (a client could forge "you promised me X"), and it desynchronizes across
  devices and storage clears. It cannot be the source of truth.
- **No history at all** (stateless single-turn). What it was: every call sees
  only the current message plus the server-computed streak snapshot. Why it
  lost: no cross-turn continuity, and it *caused* the repeated-phrasing problem —
  with no memory of his own last reply, Rickie re-derived the same lines.
- **Full history** (store and replay every turn ever). Why it lost: unbounded
  prompt growth (latency, token cost, and eventually context overflow) for no
  coaching benefit; a coach needs recent continuity, not a lifetime transcript.
  A fixed 10-turn window bounds cost while preserving the continuity that
  matters, and pairs with a user-facing "Forget our conversations" control.

## Why the current solution won
Making the server the single source of truth simultaneously fixes trust
(nothing the client says reaches the model as "history"), consistency (the same
window follows the user across devices), and the repeated-phrasing bug (Rickie
sees his own recent replies). The 10-turn cap keeps the prompt bounded, and the
alternation-safety pass guarantees the message list the model receives is always
well-formed regardless of what got stored.

## Consequences & future tradeoffs
- **Makes easy:** consistent continuity across devices, a clean tamper boundary,
  and bounded prompt size and cost. History load is a single indexed
  newest-first `LIMIT 10` query, not a full-history scan.
- **Makes hard:** memory is server-side state that must be stored, pruned, and
  made user-clearable (see the "Forget our conversations" control in
  [ADR-0003](0003-coach-notes-deterministic-extraction.md) and the deletion
  path). A window of exactly 10 turns can clip a genuinely long single-session
  exchange; that is an accepted trade for bounded cost.
- **When we'd revisit:** if coaching quality needs longer continuity, the window
  size is a single constant; a summarization step could compress older turns
  rather than dropping them — but only with the same server-owned discipline.

## Code references
- `app.py:3308,3310` — `_COACH_MEMORY_WINDOW = 10`, `_COACH_TURN_PROMPT_LEN = 600`.
- `app.py:3450-3468` — `_load_coach_messages`: newest-first `LIMIT 10`, reverse
  to chronological, drop invalid/empty roles, collapse consecutive same-role,
  trim a leading assistant turn, cap content to 600 chars.
- `app.py:3864-3870` — `coach()` loads the server window and appends the current
  message; the comment states client-sent history is deliberately ignored.
- `app.py:1267-1278` — `CoachTurn` model (`user_id` indexed, `role`, `content`).
- See [`../architecture/coach-subsystem.md`](../architecture/coach-subsystem.md)
  for the full per-request coach pipeline.
