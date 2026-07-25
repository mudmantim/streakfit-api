# ADR-0003: Coach Notes via deterministic extraction (never model-written)
- **Status:** Accepted
- **Date:** 2026-07-25
- **Deciders:** StreakFit engineering

## Context / Problem
Beyond the rolling turn window ([ADR-0002](0002-server-owned-conversation-history.md)),
Rickie benefits from a little durable, factual context about a user — their
stated goals, preferences, and things they explicitly asked him to remember — so
he can weave it in across sessions. The dangerous way to build that is to let the
model summarize the conversation into "memory": models hallucinate, and a coach
that confidently "remembers" a goal the user never stated, or infers an emotional
or medical judgment, is a trust and safety problem. Memory that is wrong is worse
than no memory.

## Decision
Long-term memory is **deterministically extracted from the user's own words** and
the model has **no memory-write tool**. `_coach_note_extract(message)` runs
high-precision regex over the USER's message only — never over Rickie's output,
never inferred — matching three categories:

- **goals** (`_GOAL_PATTERNS`): "my goal is …", "I'm training for …", "I want to
  be able to …", "I'd like to be able to …"
- **preferences** (`_PREFERENCE_PATTERNS`): "I prefer …", "I'd rather …"
- **notes** (`_NOTE_PATTERNS`): "remember that …", "just so you know …"

Each match is reduced to its first clause, whitespace-collapsed, and capped at
`_COACH_NOTE_MAX_LEN` (= 140) chars (`_clean_fact`). Facts are stored in one
`CoachNote` row per user (`user_id` unique), as three JSON-list-in-Text columns
(`goals`, `preferences`, `notes`), merged case-insensitively and kept to the most
recent `_COACH_NOTE_MAX_ITEMS` (= 5) per category (`_merge_note_list`). At coach
time the notes are injected via `_load_coach_note_block(user_id)` as a background
block that carries its own non-recitation instruction ("weave in naturally … never
say 'I remember,' never list these back") — so the instruction lives with the data,
outside the frozen personality prompt.

## Alternatives considered
- **Let the model summarize / write memory** (a `save_memory` tool or an
  after-the-fact summarization pass). Why it lost: it reintroduces hallucinated
  memory — the model could store a goal the user never set or an inferred
  judgment — which is exactly the trust failure the feature must avoid. It is
  also non-deterministic and hard to test.
- **Vector store / embeddings retrieval.** Why it lost: heavy infrastructure
  (an embedding model, a vector DB, a new dependency) for a few short factual
  strings per user; retrieval is fuzzy where we want exact, auditable facts; and
  it does nothing to stop the model from writing bad memories in the first place.
- **No long-term memory** (rely only on the 10-turn window). Why it lost: goals
  and preferences stated once should survive past ten turns; the window alone
  forgets them. Deterministic extraction adds that durability without the risk
  of model-written memory.

## Why the current solution won
Determinism buys safety and testability at once: extraction is pure Python over
the user's literal words, so a stored fact can always be traced to a sentence the
user actually typed, the model can never fabricate a memory (it has no write
tool), and the whole thing is unit-testable with fixed inputs. The design is
intentionally **low recall** — it would rather store a few accurate facts than
guess — and every fact is length- and count-bounded so the injected block stays
small. The non-recitation instruction keeps the memory feeling natural rather
than surveillance-like.

## Consequences & future tradeoffs
- **Makes easy:** trustworthy, auditable memory; deterministic tests; a hard
  guarantee that no memory is model-invented; and a small, bounded prompt block.
- **Makes hard:** recall is limited to the fixed phrasings — a goal stated as
  "I've always wanted to run a 5k" won't match "my goal is…" and is missed by
  design. Compound sentences can slightly over-capture into one clause; accepted
  because the block is background context, never shown verbatim to the user.
  Because notes are injected into the user's *own* prompt, a "remember that
  <instruction>" is a self-scoped prompt-injection surface — mitigated by the
  first-clause/140-char cap, newline stripping, the "never recite" label, and the
  single hardcoded-host weather tool (see
  [`../security_review.md`](../security_review.md) finding #6).
- **When we'd revisit:** if low recall proves too limiting, add patterns (cheap)
  before ever considering model-written memory; new categories are additive.

## Code references
- `app.py:3311-3312` — `_COACH_NOTE_MAX_ITEMS = 5`, `_COACH_NOTE_MAX_LEN = 140`.
- `app.py:3317-3335` — `_GOAL_PATTERNS`, `_PREFERENCE_PATTERNS`, `_NOTE_PATTERNS`,
  `_COACH_NOTE_CATEGORIES`.
- `app.py:3347-3366` — `_clean_fact` (first clause, cap) and `_coach_note_extract`
  (user message only).
- `app.py:3369-3377` — `_merge_note_list` (case-insensitive dedup, keep last 5).
- `app.py:3403-3410` — `_stage_coach_note` (merge into the one row, flush only).
- `app.py:3427-3447` — `_load_coach_note_block` (background block + non-recitation
  instruction).
- `app.py:1281-1295` — `CoachNote` model: one unique row per user, three JSON-Text
  columns.
- `app.py:3838-3844` — injection in `coach()`, guarded so a memory hiccup can't
  take the coach down.
- Note: the model has no memory tool — its **only** tool is `get_weather`
  (`app.py:3622-3643`, `3890`).
- See [`../architecture/coach-subsystem.md`](../architecture/coach-subsystem.md)
  and [`../architecture/data-model.md`](../architecture/data-model.md).
