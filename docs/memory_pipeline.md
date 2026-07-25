# Rickie Memory & Weather Pipeline

How Rickie (the `/api/coach` endpoint) remembers a user across sessions, answers
weather questions, and what happens to that data. Written so a new engineer can
follow the whole flow. All code is in `app.py`; the character is defined in
`docs/rickie_character_bible.md` (the prompt derives from it and is frozen).

Model: **Claude Sonnet 5** (`claude-sonnet-5`), `max_tokens=768`, thinking disabled,
rate-limited **10/day + 3/min** per user, graceful `503` when no API key.

---

## Data model (two tables)

**`coach_turn`** — a rolling window of the last **10** conversation turns per user.
Server-owned; the client never supplies history that reaches the model.
`id, user_id (FK, indexed), role ('user'|'assistant'), content (Text), created_at`.

**`coach_note`** — **one row per user** (`user_id` unique). Small, factual,
structured info the user stated explicitly. Three JSON-list-in-Text columns:
`goals, preferences, notes` (ongoing context), plus `updated_at`. Never holds
emotional interpretations, diagnoses, or speculation.

Migration: `migrations/versions/q1r2s3t4u5v6_add_coach_memory_tables.py`. Neither
table has `ON DELETE CASCADE` (see *Deletion* — cleanup is explicit).

---

## The per-request pipeline (`coach()`)

1. **Validate** message + context; return `503` if the Anthropic key is unset.
2. **Build the system prompt** = frozen `_COACH_SYSTEM_PROMPT`
   + `_build_rickie_context(user)` (server-computed streak snapshot — every number
     computed here, never by the model)
   + **Coach Notes background block** from `_load_coach_note_block(user_id)` — only
     if non-empty. Carries the non-recitation instruction (*"never say I remember,
     never list these back"*) so it lives outside the frozen personality prompt.
   + optional Insight context / joke seed.
3. **Load conversation history** from the DB via `_load_coach_messages(user_id)`:
   the last 10 turns, made alternation-safe (drops a leading assistant turn,
   collapses consecutive same-role), content capped to 600 chars. **Client-sent
   `history` is deliberately ignored** — the server is the single source of truth
   (this is also what stops repeated phrasing across similar prompts: Rickie sees
   his own recent replies).
4. **Call Sonnet 5** in a bounded tool-use loop (initial call + up to 2 tool
   rounds) with the single `get_weather` tool (see *Weather*).
5. **Persist** (only on a non-empty reply; wrapped so a memory hiccup never takes
   the reply down):
   - `_record_coach_exchange(user_id, message, reply)` — store the user turn + the
     final text reply (never tool scaffolding), then prune to the last 10.
   - `_update_coach_note(user_id, message)` — deterministic extraction (below).

---

## Coach Notes extraction (deterministic, never the model)

`_coach_note_extract(message)` runs **high-precision regex on the USER's message
only** — never on Rickie's output, never inferred. Patterns (see
`_GOAL_PATTERNS` / `_PREFERENCE_PATTERNS` / `_NOTE_PATTERNS`):

- **goals**: `my goal is …`, `I'm training for …`, `I want to be able to …`
- **preferences**: `I prefer …`, `I'd rather …`
- **notes**: `remember that …`, `just so you know …`

Each captured fact is cleaned to its first clause, length-capped (140), and merged
case-insensitively into the user's `coach_note`, keeping the **most recent 5 per
category** (`_merge_note_list`). This is intentionally **low recall** — it stores a
few accurate facts rather than speculating. Compound sentences can over-capture a
goal; acceptable because it's background context, not shown verbatim.

---

## Weather (Rickie's one tool)

`get_weather(city)` → Open-Meteo (no API key, stdlib `urllib`). **No stored
location**: the city must come from the user; if none is given the tool
description tells Rickie to ask. Two outbound calls per uncached lookup:
`_geocode_city` (city → coords) then `_forecast(lat, lon)`.

**In-process cache** (best-effort, per gunicorn worker, cleared on restart — no
Redis):
- geocode cached **30 days** by normalized city name;
- forecast cached **10 minutes** by `(lat, lon)`;
- each cache capped at **512** entries (`_cache_evict_one`: drop oldest expired,
  else oldest inserted).

**Graceful failure**: `_weather_tool_result` never raises — unknown city, missing
data, and network/HTTP errors all return `is_error` with a friendly string Rickie
relays in character; it **never invents a forecast**. The failure path logs a
`warning` so intermittent provider issues stay visible.

> **Known issue:** Open-Meteo's free tier is rate-limited **per IP** (600/min,
> 10k/day). On Render's shared egress IP this can intermittently `429` even though
> our own usage is tiny. The cache reduces our share; if 429s persist, escalate to
> a keyed provider (Open-Meteo API key with a per-account quota).

---

## Deletion / "Forget our conversations"

`DELETE /api/coach/memory` (JWT) → `_forget_coach_memory(user_id)` deletes the
caller's `coach_turn` rows **and** `coach_note`, in one commit, strictly scoped to
the token's own user; idempotent. The frontend exposes a **"Forget our
conversations"** control in Settings (registered-only, confirm-guarded).

---

## Observability

Structured `event=… key=value` INFO logs (app logger is set to INFO):
`event=login`, `event=coach_memory_inject`, `event=coach_note_extract`,
`event=coach_turn_saved`, `event=weather_cache kind=… result=hit|miss`. Low-volume
(rate-limited endpoints), greppable, no logging dependency.

---

## Maintenance: smoke-account cleanup

`scripts/cleanup_qa_smoke.py` deletes `qa_smoke_`-prefixed test accounts and their
dependent rows. **Dry-run by default**; `--execute` deletes only the "safe" group
(accounts owning no team) in one transaction and reports both groups. Exact Python
prefix match (never a SQL `LIKE`). Run where the target `DATABASE_URL` is set.

---

## Privacy & security notes

- Memory is per-user, server-side, capped, and user-clearable. It seeds Rickie
  only; it is never shared and never used to fabricate emotional continuity.
- Recent turns are stored **verbatim** (capped, max 10) until pruned or forgotten —
  inherent to conversation memory; the Forget control is the escape hatch.
- Client-supplied conversation history is ignored (can't be trusted).
- Coach Notes never store inferred/sensitive judgments — only explicit user
  statements matched by the deterministic patterns above.

---

## Code map

| Concern | Functions (`app.py`) |
|---|---|
| History load / save / prune | `_load_coach_messages`, `_record_coach_exchange` |
| Coach Notes | `_coach_note_extract`, `_merge_note_list`, `_update_coach_note`, `_load_coach_note_block` |
| Deletion | `_forget_coach_memory`, route `forget_coach_memory` |
| Weather | `_WEATHER_TOOL`, `_geocode_city`, `_forecast`, `_weather_tool_result`, `_http_get_json` |
| Cache | `_cache_get`, `_cache_put`, `_cache_evict_one` |
| Endpoint | `coach()` |

Tests: `tests/test_coach.py`, `tests/test_coach_memory.py`,
`tests/test_weather_cache.py`, `tests/test_cleanup_qa_smoke.py`.
Live conversation-quality eval: `scripts/rickie_conversation_eval.py`.
