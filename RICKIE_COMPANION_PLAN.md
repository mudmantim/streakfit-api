# Rickie → Companion: Audit & Engineering Plan

*Principal-engineer audit of the current Coach, and the plan to make Rickie a companion users come back for.
Reuses the existing Coach UI and `/api/coach` endpoint — no new UI, no redesign. Respects the Rickie Character
Bible (companion, not chatbot/therapist; never "I missed you"; anti-shame; scoped topics).*

## Audit — how Rickie works today

| Area | Current state | Verdict |
|---|---|---|
| **Conversation memory (in-session)** | `/api/coach` sends `messages=[{role:'user', content: message}]` — only the current turn. Frontend keeps a visible thread but never sends it back. | ❌ Stateless. Cannot follow up. **Root cause of "not conversational."** |
| **Cross-session memory** | None. Memory Book stores *events*, not chats. | ❌ Absent. |
| **Context-awareness** | System prompt never receives name, streak, level, or progress. `@jwt_required` means the server *could* derive it, but doesn't. | ❌ Rickie is blind to who he's talking to. |
| **Arithmetic** | Haiku would have to both know the numbers (it doesn't) and compute them in-prompt (LLMs err). | ❌ Two failure points. |
| **Personality / voice** | Strong, detailed system prompt (warm raccoon, anti-shame, joke formatting, brevity caps). | ✅ Good — but undermined by having no memory to be consistent *with*. |
| **Encouragement** | Generic (no real context to reference). | ⚠️ Risks sounding fake. |
| **Scope** | Deliberately narrow (StreakFit + Insight + jokes; refuses medical/nutrition/Teams). | ✅ Keep — per Character Bible. Make him *warmer within scope*, don't blow it open. |
| **Model / cost** | Haiku 4.5, 512 tokens, 10/day + 3/min limits, graceful 503. | ✅ Sound. |

**One-line diagnosis:** Rickie has a great voice and no memory or awareness. He's a well-written narrator, not yet a
companion. The fix is almost entirely **plumbing already-available data into the request** — high impact, low risk,
no redesign.

---

## Plan — Phase 1 (this session): the companion core

All three ship as one coherent change to `/api/coach` + the frontend + the prompt. This delivers ~80% of the
"companion" feeling.

### 1. In-session conversational memory  → *conversational, natural follow-ups, consistency*
- Frontend `_sendCoachMessage` sends the recent thread (role/content pairs) as `history`.
- Backend builds a proper multi-turn `messages` array (history + current turn).
- **Cap** history to the last ~8 turns (token/cost control). Server ignores/validates client roles.

### 2. Server-derived context + pre-computed arithmetic  → *context-aware, correct math, real encouragement*
- Backend derives, from the JWT identity (never trusting client numbers), via existing helpers
  (`get_user_stats`, `xp_to_level`): **name, current streak, best streak, total missions, level + title.**
- Backend **pre-computes** the arithmetic-prone facts so Rickie never calculates: e.g. *days to the next milestone*
  (7/14/30/100), *days to beat your best*, whether today's mission is done.
- Inject a compact **"What you know about this user right now"** block into the system prompt, plus the rule:
  *"For any number about the user's progress, use ONLY the facts above. Never calculate or estimate a number."*

### 3. Prompt refinements  → *encouraging without sounding fake, consistent personality*
- Greet/refer to the user by name occasionally (not every line).
- Encouragement must reference something real from the context ("six days straight — that's real") rather than
  generic praise. Keep all existing anti-shame / anti-childish / joke rules.
- Bible guardrails restated: never imply emotional dependency, never "I missed you," never fake continuity.

**Testing (no API key in dev):** monkeypatch the Anthropic client to capture the `system` + `messages` actually
sent, and assert (a) history is threaded, (b) the context block + pre-computed facts are present and correct, (c)
the graceful 503 path still fires without a key. Plus a pure unit test of the fact-computation (deterministic
arithmetic). This verifies everything short of live model output.

---

## Plan — Phase 2 (next, needs the live API key to validate): cross-session memory

- Persist a **capped, rolling** record of recent coach turns per user (small table or a JSON column), loaded when
  the Coach opens, so Rickie can pick up "where we left off" **when appropriate**.
- Respect the Bible: summarize facts ("last time you asked about streaks"), **never** manufacture emotional
  continuity. Add a quiet "Clear chat" control for user control/privacy.
- Estimate: ~1 table + load/save + prune, ~4–6 h. Deferred until Phase 1 is validated against the real model.

## Explicitly NOT doing (finish, don't expand)
- No new Coach UI, no personality redesign, no scope expansion into fitness/nutrition/medical.
- No raising rate limits unilaterally (cost/product decision — flagged, not changed).

## Execution order
1. ✅ Backend: context + pre-computed facts helper (+ unit test).
2. ✅ Backend: accept `history`, build multi-turn messages, inject context block, prompt rules.
3. ✅ Frontend: send recent thread as `history`.
4. ✅ Tests (monkeypatched client) + verify 503 path.
5. ⏭ Phase 2 cross-session memory (deferred, needs API key).
