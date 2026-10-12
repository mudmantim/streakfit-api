# Step 2 — the local day: architecture and implementation plan

**Status: design only, not approved, not started.** No Step 2 application code
or migration exists. Written 2026-10-12 against production `6b6e1cd`
(Step 1 live, Alembic head `d4f1b6e2a9c3`).

**What Step 2 is:** each person's mission day runs from 04:00 to 04:00 in
the IANA time zone they chose. Today it runs from 00:00 to 00:00 UTC for
everyone.

**Inputs:**

- `e2e-campaign/evidence/d6_d50_d59/DESIGN_REPORT_2026-10-11.md` (the problem,
  D6 and the others).
- `.../DESIGN_REFINEMENT_2026-10-11.md`: the invariant, the floor, the migration
  bridge and the 04:00 analysis. Re-used here, not repeated in full.
- Step 1 **as shipped**. Where it differs from the refinement (§1), the shipped
  behaviour wins.

Step 2 is a **one-way door for day semantics**. Once anyone's day is local,
going back to a build that only knows UTC days is unsafe (§12). It needs its
own owner go-ahead, separate from approving this design.

---

## 1. Where Step 1 differs from the refinement

| Refinement said | Step 1 shipped (authoritative) |
|---|---|
| Raising effort after the first exercise → 409 `already_started` | Kept for the next mission day (`DailyMission.next_effort`), answered 200 `applies: "tomorrow"` |
| Tier change applies tomorrow | Before the first exercise, a skill change rebuilds today's five. After it, the change applies from the next mission. |
| Brain Boost stored as `brain_boost_item_id` | The whole question is stored as a snapshot (`daily_mission.brain_boost` JSON) |
| `day_ends_at` (UTC instant) | `day_ends_in_s` (seconds left, server-measured); no instant |
| `revision` sent with completions | Not sent; a key outside today's five → 409 `mission_changed` |
| DST: midnight unsafe in 13 zones, 03:00 in 40 | Re-measured 2026-10-12 (tzdata 2026c, 598 zones, 2024–2030): midnight **10**, 03:00 **20**, 04:00 **0**, 05:00 **0**. The original design report also says 10 and 20, so the refinement's figures were a transcription error. 04:00 is clean either way. |

Audit findings that Step 2 must absorb (`STEP2_READINESS` report, 2026-10-12):

- **D1:** `PUT /api/daily/effort` and the skill change in `PATCH /api/me` do
  not name a mission day. A page left open from yesterday can rebuild or ease
  *today's* five (reproduced). Under Step 2 the day moves per person, so every
  per-day write must name its day (rule R2, §3). This is fixed in stage 2a,
  before anything else.
- **D4:** the client still reads the device clock for display. Memory Book
  "today" and "this week", the challenge "done today" and "at risk" badges, and
  the once-a-day completion notification all use it. Wrong labels only today;
  under local days they would visibly disagree with the server. Replaced in
  stage 2d.
- **Witness** (`_witness_for_ids`) judges every member against one
  `server_today()`. Under Step 2, teammates in different zones have different
  days.
- **tzdata is not pinned.** Python's `zoneinfo` reads the operating system's
  database, so production, CI and laptops can disagree about a boundary.

---

## 2. Day model

Carried from the refinement §1, unchanged:

- `R = 4` (the rollover hour), global. There is no per-user setting in Step 2.
- `B(D, z)` is the **first instant** whose wall time in zone `z` is at or
  after `D 04:00`. This is defined robustly so the day can never run backwards.
- `sfdate(t, z)` is the largest `D` with `B(D, z) ≤ t`.
- **`today(u, t) = max(F_u, sfdate(t, z_u))`**, where `F_u` is the person's
  floor (a date).
- **`z_u` NULL ⇒ the UTC date of `t`, exactly Step 1's rule.** Every existing
  account starts here, and nothing changes for it until it adopts a zone.

`mission_date` keeps its name and its contract: it is "this person's mission
day". Its meaning was already "whatever the server said today was"; Step 2
changes how the server decides, not what the field is.

---

## 3. Authoritative time and the write rules

- **One instant per request**, unchanged: `ServerClock.now()` through
  `_clock_snapshot()`.
- New `user_today(user)` computes `today(u, t)` from that instant and the
  person's zone and floor. It is the **only** source of a per-person day.
  - `server_today()` stays for genuinely UTC things (admin "today" windows) and
    is renamed `utc_today()`, so a call site cannot pick the wrong one by
    accident.
  - The AST guard in `tests/test_day_seams.py` is extended to enforce this.
- **The zone and floor are read from the user row locked by `_lock_user`.** A
  concurrent zone change therefore serialises with every per-day write. The
  read-only `GET /api/daily` reads them unlocked; the R1 floor (below) still
  makes the day monotone.
- **R1, changing zone** (refinement §1): under the lock, first
  `F ← max(F, today(u, t))`, then `z ← z'`, then log the change. A zone change
  can never re-open a day.
- **R2, every per-day write names its day.** This covers completion, Brain
  Boost, effort, skill-before-first-exercise and challenge check-in.
  - A missing date → 400 `mission_date_required`.
  - A past date → 409 `day_changed`, nothing written.
  - A future date → 400.
  - Completion and Brain Boost do this today. Effort and skill do not (D1).
    Challenge check-in is per challenge and keeps its own rule (§9).
- **R3, one reward per (user, day)**, unchanged: the primary keys and unique
  constraints already exist.

---

## 4. Choosing and validating the zone

- **The client reports; the person confirms.**
  `Intl.DateTimeFormat().resolvedOptions().timeZone` gives a name. If it
  differs from the stored zone (or none is stored), the page shows: "Your day
  starts at 4am in *Europe/London* — use this? / Not now."
  - Nothing changes without a tap. A zone is never derived from an IP address.
  - A report of `UTC` or `Etc/UTC` is treated as **unknown**. Privacy browsers
    (Firefox with fingerprinting resistance, Tor) report UTC whatever the real
    zone is. UTC can still be chosen deliberately in Settings.
- **Endpoint:** `PUT /api/me/time-zone {time_zone, mission_date}`, with the
  person locked.
  - Validation:
    - ASCII, at most 64 characters;
    - a member of the pinned `zoneinfo.available_timezones()`;
    - not `localtime` or `Factory`.
  - Links (aliases) are accepted and stored as given; they behave identically.
  - Rate-limited (e.g. 10/day). Integrity does not depend on the limit
    (property P2); it limits noise in the change log.
- **Pinned tzdata:** add `tzdata==<version>` to `requirements.txt` and call
  `zoneinfo.reset_tzpath(to=())` at import.
  - This makes the pinned package the only source in production, CI and
    development.
  - `/api/verification/self` reports the version.
  - Upgrading tzdata becomes a reviewed change with a test: §13 row T14
    re-runs the 04:00 gap/fold scan.
- Settings shows the zone and a picker (search over the pinned list). Choosing
  there is the same R1 change.

---

## 5. Schema (stage 2b; additive, nullable, no backfill)

| Change | Purpose |
|---|---|
| `user.time_zone` VARCHAR(64) NULL | The chosen zone. NULL = the UTC day (Step 1 behaviour). |
| `user.day_floor` DATE NULL | `F`. NULL = no floor. |
| `user.legacy_through` DATE NULL | `F0` at adoption; never changes afterwards. |
| `user.legacy_bridge` BOOLEAN NOT NULL default false | The one-date bridge (refinement §2). |
| `daily_mission.time_zone` VARCHAR(64) NULL | The zone the day was decided under, so a stored day explains its own boundary and `day_ends_*`. NULL = UTC. |
| New `user_time_zone_change` (`id`, `user_id` FK, `changed_at`, `from_zone`, `to_zone`, `floor_after`) | Audit of R1. Purged after 90 days by the retention sweeper. Gets a deletion rule (the FK-classification test forces this). Included in the export. |

**Migration rules**, learned from the Step 1 rollback audit:

- `SET LOCAL lock_timeout = '5s'` in **both** `upgrade()` and `downgrade()`.
  Adding a foreign key to `user`, or dropping a table that has one, takes
  locks on `user`. A drop takes **ACCESS EXCLUSIVE**: reproduced 2026-10-12, it
  stalls every signed-in request behind any open transaction.
- **The downgrade refuses once any `user.time_zone` is non-NULL.** That is the
  one-way door, enforced in the migration itself, with a test. There is a
  precedent: `test_the_downgrade_refuses_once_a_reporter_has_left`.
- `tests/test_migrations.py`, plus `test_migrations_postgres.py` on the CI
  proposal branch, must pass.

---

## 6. Adoption: the move from UTC days to local days

Carried from the refinement §2 (the floor and the bridge), with two
precisions.

- **`F0` comes from writes only:** completions, Brain Boost answers, daily
  effort rows, mission awards and challenge check-ins.
  - It does **not** come from untouched `daily_mission` rows. Merely opening
    the app late in the evening west of UTC creates tomorrow's UTC mission.
    Counting that view would move the floor and skip the rest of the person's
    local day.
  - An untouched row for a date the person has not reached yet is simply used
    when that date arrives. Its five were decided by the same date seed, and
    nothing was completed on it.
- **Today's frozen mission at adoption:**
  - If local today equals the date of an existing mission row (any
    completions), that row **is** today's mission: same five, same
    completions, bonus paid at most once (R3).
  - If local today is earlier than that row's date (west of UTC, late
    evening), the person continues the earlier day. If they finished it, they
    see "already done", which is the refinement's NY 21:00 example. The later
    row waits for its date.
- **When adoption happens:** on the person's first confirmed zone (§4), or at
  registration for new accounts (owner decision O3). Not on a silent device
  report.

The streak is one consolidated walk (Step 0 merged the copies). It crosses
`F0` into the old-rule history exactly as the refinement §2 describes,
including the one-date bridge for accounts east of UTC whose streak is alive.

---

## 7. DST and the shape of a day

- Boundaries are instants, so local days are 23, 24 or 25 hours long (24.5 on
  Lord Howe). A countdown is always computed by the server.
  - `day_ends_in_s` is kept.
  - `day_ends_at` (UTC ISO instant) is added, so a client that slept can
    re-derive the countdown without a request.
- 04:00 is clean in every zone from 2024 to 2030 (§1). The robust `B`
  definition covers a future tzdata that puts a transition at 04:00: the day
  starts at the first instant at or after 04:00, and is never skipped or
  repeated.
- The client never does date arithmetic with zones. It shows what the server
  sends.

---

## 8. Travel, and changing zone after completing a day

All through R1.

- **Eastward** (NY → Tokyo): the floor holds yesterday's date. Today becomes
  the Tokyo date, and the new day opens sooner. An unfinished NY day closes;
  the prompt says so first ("Switch now — closes today at 3/5 — or after I
  finish", owner decision O2).
- **Westward** (Tokyo → Honolulu): the same date again. The floor makes it
  "already done", nothing is paid twice, and the next day opens at 04:00
  Honolulu time.
- **Rescue attempts** (switching west to re-open a missed day) fail: the floor
  is set before the zone moves (property P2).
- **Two deliberate hops in one sitting** can credit at most three dates
  (accepted residual). Each still needs five real exercises, and the change
  log shows it.
- **Devices disagreeing** (phone in NY, laptop privacy browser reporting UTC):
  the stored zone decides for every device. A device report only ever triggers
  the prompt.

---

## 9. Every other place that has a "today"

The call sites of `server_today()` in `6b6e1cd` (17 functions, including the clock helpers themselves), by kind:

- **Per-person, switch to `user_today(user)`:**
  - `get_daily`, `complete_daily_exercise`, `answer_brain_boost`,
    `set_daily_effort` and `update_me`;
  - `get_daily5_streak`, `get_recent_week`, `get_user_stats`,
    `_completion_milestones`, `_repeat_completion_response`,
    `_coach_insight_for`;
  - challenge `check_in`. A challenge's `last_check_in` is a date; it moves
    to the owner's local day, and a legacy `last_check_in` later than today
    counts as today (refinement §5).
- **Many people at once:** `_witness_for_ids`.
  - It needs each member's own today. Batch by zone (members of one team
    share few zones) and compute `today` per group.
  - Teammates are shown only "moved today" and the streak, **never** a zone
    or a local time.
- **Genuinely UTC:** `admin_stats` "today" windows stay UTC and are labelled
  "UTC day".
- **No user:** `get_demo_daily` (guest) keeps the UTC day. The guest's zone
  is not sent and not stored (owner decision O7 if the guest should get a
  local day: that would need the zone on every guest request).

---

## 10. Stale clients, offline devices, backward compatibility

- **Step 1 clients already send `mission_date`.** The check is equality
  against the server's today, so a tab open across the Step 2 deploy (or
  across an adoption) gets 409 `day_changed` and the explicit new-day screen.
  No silent misfile is possible.
- **Pre-Step-1 clients** (no `mission_date`) are already refused with 400
  `refresh_required`.
- **Effort and skill** start sending `mission_date` in stage 2a.
  - The server accepts its absence for one release (logging
    `event=undated_day_write`), then requires it. That keeps one deploy's
    worth of open tabs working.
  - Owner decision O5: require it immediately instead, as completions did.
- **Offline:** device time is never used for crediting.
  - A queued tap carries its `mission_date`. If the day ended while offline,
    the replay gets an honest 409.
  - Queue storage (memory vs browser storage) is refinement decision 7,
    still open.
- **API changes are additive:** `time_zone` (own only) in `/api/me`, and
  `day_ends_at` in `/api/daily`. Nothing existing changes shape.
- **NULL zone is exactly Step 1.** Until the adoption flag (§14, stage 2e) is
  on, Step 2 code serves every account exactly as today. The PostgreSQL
  regression suite proves this.

---

## 11. Data export, deletion, retention

- **Export:** `time_zone`, `day_floor`, `legacy_through`, and the zone-change
  log.
- **Account deletion:**
  - the columns go with the row;
  - the change log is added to `_USER_PRIVATE_DELETES`;
  - `test_every_foreign_key_into_user_has_a_deletion_rule` fails until it is.
- **Retention:** the change log is purged at 90 days in the existing sweeper,
  as a fifth step with its own `_record_retention_run` kind.

---

## 12. Rollback

The hazard: after adoption, a build that only knows UTC days ignores the
floor and the zone. *Example:* a Honolulu user finishes local Tue at 20:00
HST (= Wed 06:00 UTC). On the old build, today is UTC Wed: a fresh mission,
earnable again the same evening. That is two days in one sitting, and one of
them is a day that isn't over. So rollback is tiered.

| Situation | Rollback |
|---|---|
| Stages 2a–2d, adoption flag off (no zone stored) | Ordinary Step 1-style rollback. Downgrade first (it passes its own guard because no zone is set), **with `PGOPTIONS='-c lock_timeout=5s'`**, then deploy the previous build. |
| Adoption on, problem in the zone UI or the adoption flow | **Freeze**: `STREAKFIT_FREEZE_TIME_ZONES=1` refuses new adoptions and changes; stored zones keep working. No deploy needed. |
| Local days themselves are wrong | **Return everyone to UTC through R1**: a CLI under the per-person lock sets `F ← max(F, today)`, then `z ← NULL`, then logs. Monotone by P1, so nobody gets a day back or a day twice. Then fix forward. |
| Code rollback to the Step 1 build after adoption | **Not allowed.** The migration downgrade refuses (§5). Use the row above first; once no zone is stored, the downgrade guard passes. |

---

## 13. Test matrix (pytest with a pinned clock unless noted)

| # | Case | Expectation |
|---|---|---|
| T1 | NULL zone, every Step 1 test | Identical results (the whole Step 1 suite runs unchanged) |
| T2 | 03:59:59 / 04:00:00 local, zones NY, London, Tokyo, Kolkata (+05:30), Kathmandu (+05:45), Chatham (+12:45), Kiritimati (+14), Pago Pago (−11) | Day changes exactly at `B`; `day_ends_in_s` = 1 at 03:59:59 |
| T3 | Mission days containing a DST change (computed 2026-10-12, tzdata 2026c): NY 2026-03-07 = 23 h, 10-31 = 25 h; London 03-28 = 23 h, 10-24 = 25 h; Lord Howe 04-04 = 24.5 h, 10-03 = 23.5 h; Santiago 04-04 = 25 h, 09-05 = 23 h. With a 04:00 rollover the long or short day is the one **before** the change's calendar date | Exactly those lengths; nothing skipped or repeated |
| T4 | UTC midnight inside a local day (any non-UTC zone) | Same mission, same five, no rollover |
| T5 | R1 east (NY→Tokyo), west (Tokyo→Honolulu), rescue (NY→Honolulu after a missed day) | Floor holds; no reopened day; no double pay |
| T6 | Zone change after 5/5, after 3/5, before any exercise | Completed day keeps its award; partial day closes; nothing moves |
| T7 | Adoption: east/west, streak alive/dead, mid-mission, untouched future UTC row | Refinement §2 worked examples, plus §6 precision 1 |
| T8 | Stale tab across a local 04:00: completion, Brain Boost, effort, skill | 409 `day_changed`, nothing written (effort/skill: D1 fixed) |
| T9 | Zone change concurrent with completion (PostgreSQL, two connections) | Serialised by the user lock; the completion lands on the day valid at its lock time |
| T10 | Two devices, same exercise, across a zone change (PostgreSQL) | One row, one bonus |
| T11 | Witness: teammates in NY and Tokyo | Each judged on their own day; no zone in the response |
| T12 | Account deletion during a zone change (PostgreSQL) | 404, never 500; change-log rows gone |
| T13 | Validation: `localtime`, `Factory`, 65 characters, non-ASCII, `../etc/passwd`, `UTC` from a device report | 400 / treated as unknown |
| T14 | tzdata scan: every zone, 2024–2035, 04:00 gap/fold | 0 (fails loudly on a tzdata upgrade that breaks it) |
| T15 | Downgrade with a zone set | Refused; database unchanged |
| T16 | Migration chain on SQLite and PostgreSQL | Schema equals models |
| T17 | `verify_all` module `user_day.py` (deterministic, production-safe) | Yesterday's `mission_date` → 409; five stable across reads; `day_ends_at` consistent with the stored zone |
| T18 | `make uicheck` with the device clock and zone shifted against the server | Labels follow the server day (D4 fixed) |

---

## 14. Implementation stages

Each stage is its own release, meets the four-part Verification Standard, and
is reviewable alone.

| Stage | Content | Behaviour change | Reversible |
|---|---|---|---|
| **2a — prerequisites** | (1) `mission_date` on effort PUT and skill PATCH (D1), client and server, one-release tolerance. (2) Pin tzdata + `reset_tzpath` + self-check field. (3) `user_today(user)` seam returning the UTC date; `server_today` → `utc_today`; AST guard. (4) Per-person witness API shape. (5) CI PostgreSQL job (`proposal/ci-postgres`). | Only D1 (a stale tab's effort/skill gets 409) | Yes |
| **2b — schema** | §5 migration; models; deletion rule; export; nothing reads the columns | None | Yes (guarded downgrade) |
| **2c — day computation** | `today(u,t)`, `B`, floor, R1, adoption algorithm, streak crossing; behind `STREAKFIT_LOCAL_DAY` (default off); T1–T16 | None (flag off; all zones NULL) | Yes |
| **2d — client** | Zone prompt and Settings picker (no-op while the flag is off); `day_ends_at`; device-clock displays replaced by server fields (D4); new-day copy "resets at 4am" | Display only | Yes |
| **2e — enable adoption** | Flag on; the first adoptions happen. **One-way door, own owner go-ahead.** Watch: adoption count, `event=day_changed` rate, 03–05h local completion share. | Yes: local days | Only via §12 tiers |
| **2f — follow-ups** | Change-log purge verified in production, D6-victim audit, the 03–05h metric review, rollover setting only if the metric shows a need | — | — |

---

## 15. Privacy and data minimisation

- **A time zone is coarse location** (country or region). The design keeps it
  at that:
  - only the IANA name is stored;
  - no UTC offset history, no IP geolocation, no device locale.
- **It is never shown to teammates**, and never sent to the AI provider.
  - If Rickie ever needs it, send only the part of the day ("morning"), and
    only with owner approval: O6.
- **Logs:** `event=time_zone_changed user_id=…` with no zone names. A zone in
  a log line is location data, and logs are kept on Render, outside the
  export and deletion paths.
- **Covered by the data export and by account deletion; the change log is
  purged at 90 days.** Encrypted backups hold it like every other column,
  under the 14-day backup rule.
- **Child accounts:** a time zone is personal information. The age gate is
  still unwired in production (`docs/child-safety/architecture.md`). Storing
  a zone for an unknown-age account is the same kind of collection as storing
  a username today. It does not change the child-safety position, but the
  minors-and-consent document should list it.

---

## 16. Owner decisions

| # | Decision | Recommendation |
|---|---|---|
| O1 | Approve this design as the Step 2 plan (stages 2a–2f) | Approve 2a now; 2b–2d after review; 2e separately |
| O2 | Eastward change with an unfinished day: choice in the prompt vs server-side deferral | Choice in the prompt (refinement decision 2) |
| O3 | Adopt at registration for new accounts (zone confirmed on the welcome screen) | Yes |
| O4 | Accepted residual: ≤3 dates per sitting with a deliberate double hop | Approve (refinement decision 1) |
| O5 | Effort/skill without `mission_date`: one release of tolerance, or refuse at once | One release of tolerance, logged |
| O6 | Rickie and the zone: never, or "part of day" only | Never in Step 2 |
| O7 | Guest day: UTC (no zone sent) or local (zone on each guest request, not stored) | UTC for Step 2 |
| O8 | tzdata pin version and upgrade cadence | Current release; upgrade on demand with T14 |
| O9 | Rollback policy after 2e: the §12 tiers instead of a code rollback | Approve |
