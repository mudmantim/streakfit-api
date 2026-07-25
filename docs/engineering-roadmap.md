# StreakFit Engineering Roadmap

A genuine engineering audit of the StreakFit backend, aimed at **reducing future engineering cost**. Every item is grounded in the code, the subsystem maps, or measurement — not speculation. Nothing here is implemented; this is the plan.

**Legend** — Effort: S (<½ day) · M (½–2 days) · L (>2 days). Risk: how likely a change is to break something. Priority: P0 (do next sprint) · P1 (this year) · P2 (when it matters).

Contents: [Phase 3 · Technical Debt](#phase-3--technical-debt-backlog) · [Phase 4 · Testing Roadmap](#phase-4--testing-roadmap) · [Phase 5 · Performance](#phase-5--performance-measured) · [Phase 6 → operations/production-readiness.md](operations/production-readiness.md)

---

## Executive summary

The backend is in **good health**: one well-tested file (155 pytest + an end-to-end suite), a defensible architecture (server-owned truth, fail-closed integrations, migrations-as-truth with a boot guard), and a clean recent hardening pass. The debt is mostly **operational reproducibility** and **scale-readiness**, not correctness. The three things worth doing first, because they're cheap and de-risk everything else:

1. **Make the deploy declarative** — the Start Command and Render config live only in the dashboard; a new owner can't rebuild prod from the repo.
2. **Pin `anthropic` (and `alembic`)** — the only unpinned dependency is the one that talks to a paid, changing API.
3. **Fix the stale `memory_pipeline.md`** and extract the coach model string to config — tiny, removes two "why doesn't this match the code?" traps.

The largest *latent* risk is that several correctness guarantees (rate limiting, weather cache, deletion integrity) hold **only because there is a single gunicorn worker**. That's fine now and documented; it's the first thing to revisit before scaling — see [operations/production-readiness.md](operations/production-readiness.md).

---

## Phase 3 · Technical Debt Backlog

### Immediate (worth fixing next sprint)

| # | Item | Benefit / Impact | Effort | Risk | Dependencies |
|---|---|---|---|---|---|
| I1 | **Deploy config not in repo** — no `render.yaml`/`Procfile`/`wsgi.py`; Start Command only in comments + dashboard | High — a new owner can reconstruct and review the deploy; disaster-recovery becomes possible from git alone | S | Low | Confirm live Render settings first |
| I2 | **Pin `anthropic`** (currently `>=0.40.0`) and add explicit `alembic` pin | Medium-High — build reproducibility; a silent SDK bump can't change coach behavior at deploy | S | Low | Verify current installed version |
| I3 | **Stale `memory_pipeline.md`** — documents the pre-atomic persist path (`_record_coach_exchange`/`_update_coach_note`) instead of `_persist_coach_interaction` | Medium — removes a real "docs contradict code" trap in the trickiest subsystem | S | None | — |
| I4 | **Coach model/params are inline literals** (`'claude-sonnet-5'`, `max_tokens=768`, thinking) at ~3880 | Medium — a model bump becomes a config edit, not a code hunt; enables per-env overrides | S | Low | — |
| I5 | **No JSON handler for 413 / admin 403** — both return Flask default HTML, inconsistent with the API's `{"error":...}` | Low-Medium — consistent client error handling | S | Low | — |
| I6 | **`_persist_coach_interaction` failure is swallowed silently** beyond a log line — no metric | Low-Medium — memory-loss is currently invisible unless someone greps logs | S | Observability (I: logging exists) |

### Medium (worth fixing this year)

| # | Item | Benefit / Impact | Effort | Risk | Dependencies |
|---|---|---|---|---|---|
| M1 | **Rate-limit storage is `memory://`** — per-worker, resets every deploy | High at scale — real abuse protection needs shared storage (Redis); today limits are soft and per-process | M | Medium (new infra dependency) | Redis/Valkey instance |
| M2 | **Remaining N+1 reads** — `get_memory_book` (measured **9 queries**), `admin_stats`, `list_teams` still do per-row work | Medium — latency + DB load as data grows; pattern already solved by `_usernames_for_ids` | M | Low (tests exist for the pattern) | Extend `test_query_counts.py` |
| M3 | **DB has no `ON DELETE` constraints** — integrity enforced only in `delete_user_account` | Medium — defense-in-depth; a future raw delete or second code path could orphan/FK-error | M | Medium (schema migration on prod) | `db_integrity_matrix.md` migration plan; `test_migrations.py` |
| M4 | **`progress_event.team_id` is not a FK** (bare Integer) | Medium — dangling team refs in the audit log; joins there are unsafe | M | Medium (backfill/validate before adding FK) | M3 |
| M5 | **Repeated ownership/membership checks** in every team + resource handler | Medium — a missed check is an authz hole; a `@team_member_required`/`@resource_owner` decorator centralizes it | M | Medium (touches many handlers) | Good test coverage first |
| M6 | **No refresh token** — 1h expiry forces a hard re-login mid-session | Medium — UX; also enables shorter access-token lifetimes for security | M | Low | Frontend change too |
| M7 | **Verification-suite gaps** — `brainboost`, 1:1 `coach`, `notifications`, `pwa` uncovered end-to-end | Medium — these ship without the "reachable through the real surface" guarantee | M | Low | Suite module contract |
| M8 | **Lazy-import `anthropic`** to cut ~473 ms (53%) off cold start | Medium — faster deploys/restarts, lower cold-start user-visible latency | S-M | Medium (coupled to test monkeypatch of the client) | Adjust `test_coach.py` fake-client injection |

### Long term (architectural)

| # | Item | Benefit / Impact | Effort | Risk | Dependencies |
|---|---|---|---|---|---|
| L1 | **Single-file monolith → blueprints** (auth / game / teams / coach / admin) | High for a growing team — navigability, parallel work, smaller test-import surface; but only once boundaries are *learned*, not guessed | L | Medium (large mechanical move; keep behavior identical) | Strong test suite (have it) |
| L2 | **Extract a service layer** for the write-heavy game logic (award/progress/campfire propagation) out of route handlers | Medium-High — testable domain logic, reusable across endpoints and CLI | L | Medium | L1 |
| L3 | **Password reset / account recovery** flow (none exists today) | Medium (product) — currently a lost password = lost account | L | Medium (email provider, tokens) | Email service |
| L4 | **Frontend build/minify step** — 204 KB unminified single JS file, 74 KB is modal-only data parsed on load | Medium — first-load performance; out of backend scope but tracked | M-L | Low | — |
| L5 | **Move denormalized stats** (`user.xp_total`, `acorns_total`) toward derived/materialized views if they ever disagree with `progress_event` | Low-Medium — single source of truth for progress | L | Medium | Reconciliation audit |

---

## Phase 4 · Testing Roadmap

**Do not add tests broadly.** The suite is healthy (155, meaningful, recently strengthened). The goal is *shape*, not *count*.

### Where coverage is genuinely weak
- **`auth` beyond happy path** — pytest covers register/login/duplicate; thin on token expiry, malformed-header, and the timing-equalization path (only one 401 test). *Add a few targeted tests, don't sprawl.*
- **Game logic** — `daily complete`, `brain-boost`, and XP/level math have **no dedicated pytest file**; they're exercised only indirectly (campfire tests) and by the end-to-end suite. This is the highest-value gap: the award/threshold logic is subtle (first-ever, crossing 5, stage crossings) and currently unguarded at the unit level.
- **End-to-end** — `brainboost`, 1:1 `coach`, `notifications`, `pwa` verification modules are stubs (M7).

### Where tests duplicate / could consolidate
- **Team setup is repeated across six files** (`test_teams`, `test_team_chat`, `test_team_moments`, `test_remove_member`, `test_rickie_team_reactions`, `test_campfire`), each hand-building a team + members. A shared `team_factory` fixture (owner + N members + membership) in `conftest.py` would cut setup boilerplate and make intent clearer. **This is the single best test-health investment.**
- **`test_coach.py` (18) and `test_coach_memory.py` (27)** overlap on memory threading. Keep both, but draw a clear line: `test_coach` = request/prompt behavior, `test_coach_memory` = persistence/extraction. Some memory-threading assertions in `test_coach` are redundant with `test_coach_memory`.

### Where tests are brittle
- Tests asserting on **exact user-facing strings** (error copy like "That username and password don’t match.") break on copy edits. Prefer asserting status + a stable `error` code where one exists; treat human copy as non-contractual. (Contract tests already do this well — extend the pattern.)
- **Weather cache timing tests** force-expire by mutating `expires_at` — fine, but they depend on `datetime.utcnow()` monkeypatch-ability; the repo-wide `utcnow` deprecation (Python 3.12+) will eventually force a move to timezone-aware `datetime.now(UTC)`, touching these.

### Where integration tests would replace dozens of unit tests
- The **daily-complete → campfire → moment → Rickie-post** chain is currently tested in fragments. One integration test that drives a full "5 completions across a 2-member team, assert campfire counter + moments + Rickie welcome" would cover the interaction that unit tests only touch in isolation.

### Recommended sequence
1. Add the shared `team_factory` fixture; refactor the six team files onto it (no new assertions — pure consolidation). **P0-ish for test health.**
2. Add a `test_game_progress.py` for award/threshold/level math (the real gap). **P1.**
3. Fill the end-to-end `brainboost`/`coach` verification modules. **P1.**
4. Migrate `utcnow` usages when the deprecation forces it; update the timing tests then. **P2.**

| Recommendation | Benefit | Effort | Risk | Priority |
|---|---|---|---|---|
| Shared team fixture | Less brittle setup, clearer intent | M | Low | P0 |
| Game/progress unit tests | Guards the subtlest logic | M | Low | P1 |
| E2E brainboost/coach modules | Closes the "reachable" gap | M | Low | P1 |
| Prefer codes over copy in asserts | Fewer false failures | S | Low | P1 |

---

## Phase 5 · Performance (measured)

All numbers are **measured**, not guessed. Method: fresh-process import timing (best of 3); `test_client` latency (median of 20) with a SQLAlchemy `after_cursor_execute` query counter; SQLite in-memory. **Query counts transfer to Postgres; absolute ms do not** (Postgres over the network will be slower per query, which makes query *count* the number that matters).

### Startup
| Import | Time | Share |
|---|---|---|
| `import app` (total cold start) | **885 ms** | 100% |
| ↳ `import anthropic` | **473 ms** | **53%** |
| ↳ `import sqlalchemy` | 125 ms | 14% |
| ↳ `import flask` | 97 ms | 11% |

**Finding:** the Anthropic SDK dominates cold start and is only needed when a user chats. **Lazy-importing it (M8) would roughly halve cold start** — the one clearly worthwhile, measurable startup optimization. Everything else in startup is irreducible framework cost.

### Endpoint query counts + latency
| Endpoint / path | Queries | Median (SQLite) | Verdict |
|---|---|---|---|
| `POST /api/login` | 1 | **81 ms** | Hash-bound (pbkdf2), intentional — **do not optimize** |
| `GET /api/me` | 3 | 1.2 ms | Fine |
| `GET /api/daily` | 4 | 1.4 ms | Fine |
| `GET /api/challenges` | 1 | 0.7 ms | Fine |
| `GET /api/memory-book` | **9** | 2.2 ms | **N+1 candidate (M2)** — highest read query count |
| `GET /api/teams/{id}` (30 members) | 7 | 2.1 ms | Good — WS3 batch working |
| `GET .../messages` (50) | 2 | 1.2 ms | Good |
| `GET .../moments` (50) | 3 | 1.5 ms | Good |
| `_load_coach_messages` | 1 | 0.4 ms | Optimal |
| `_persist_coach_interaction` | **9** | 1.4 ms | Heaviest write; acceptable now, watch at scale |

### Findings & measurable recommendations
1. **`get_memory_book` = 9 queries** — the one read endpoint doing per-row work. Apply the `_usernames_for_ids`/batch pattern; guard with a `test_query_counts` case. *Measurable: expect ≤4 queries after.* (M2, P1)
2. **`_persist_coach_interaction` = 9 statements/coach-turn** — inherent to the atomic design (2 inserts + prune select/deletes + note get/create/merge + commit). Fine at 10 coach msgs/user/day. *Only revisit if coach volume 10×'s; the prune could batch its deletes into one statement.* (P2)
3. **Login 81 ms is pbkdf2, by design** — this is the login latency floor and a security feature, not debt. Left as-is.
4. **Rate limiter tripped at 10/min during profiling** — confirms the limit is active and low; combined with `memory://` (M1), a shared-IP/NAT population could hit false positives while a distributed attacker across IPs isn't well-contained. Real fix is shared storage (M1), not raising limits.

**Explicitly did NOT find:** no slow queries (all indexed hot paths), no obvious expensive allocation or repeated serialization in the read paths profiled. The N+1 in `get_memory_book` is the only measured query-count smell.
