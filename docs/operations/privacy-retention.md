# Retention — what is actually guaranteed

Two different promises live in this file, and they are kept by different code on
different clocks:

1. **Conversation retention** — `coach_conversation_days: 30` in a user's data
   export. This is the original subject of the file and everything up to
   "Moderation evidence" is about it.
2. **Moderation evidence retention** — what happens to a reported message,
   caption or photo after a report is dealt with. Added when the moderation
   operations milestone shipped; it has its own section at the end.

Both sections answer the same three questions in the same order: what is stored,
what deletes it, and under what conditions deletion does **not** happen.

Written because the conversation promise was, for a while, only true for people
who kept using the app — which is the opposite of who a retention window is for.

---

# Conversation retention

## What is stored

| Row | What it is | Lifetime |
|---|---|---|
| `coach_turn` | The literal text of a message and Rickie's reply | Two bounds: the last **10 turns** per user are what Rickie can see, and **30 days** (`_COACH_TURN_MAX_AGE_DAYS`) is how long any of it is kept at all |
| `coach_note` | Canonical tokens from a closed vocabulary — `walking`, `morning`, `short`. **Never the user's words** | Until the user clears them |

The 10-turn window and the 30-day age limit answer different questions. Ten
turns is *how much context Rickie gets*. Thirty days is *how long anything
survives*, and it is the one that is a promise to a person.

## The four things that delete an expired turn

**1. The user's own activity** — `_expire_old_coach_turns(user_id)` runs on that
user's read and that user's write.

**2. Somebody else's activity** — `_sweep_expired_coach_turns()` runs on the
request path, at most hourly, and deletes **every** user's expired rows, not
just the caller's. This is what covers the person who said something difficult
and never came back: they are not reading or writing, so only a sweep on
somebody else's behalf will ever reach them.

**3. The in-process sweeper thread** — enabled by
`STREAKFIT_RETENTION_SWEEPER=1` on the serving command, it runs the same sweep
on a timer with no request involved. This is what makes retention independent of
whether the app is busy.

**4. `flask coach-prune`** — the same sweep as a one-shot command, for a
scheduler. `render.yaml` declares a daily cron that runs it.

Layers 2 and 3 are in-process and share the hourly rate limit. The DELETE is
idempotent, so overlapping sweeps are harmless.

## What is NOT guaranteed — read this part

**The thread dies with the process.** If the web service is down, redeploying,
or crash-looping, the thread is not running and nothing is being swept. The cron
exists precisely for that case — but see the next point.

**The cron in `render.yaml` is NOT live, and cannot become live by editing that
file.** Confirmed against `docs/operations/setup.md`: the production service is
**configured in the Render dashboard and git-linked to `main`** — `git push
origin main` builds and deploys it. It was never created from a Blueprint, so
Render does not read `render.yaml` at all. The cron declared there is
documentation of intent, nothing more.

**Until the cron job below is created, layer 4 does not exist** and retention
depends on the web service staying up.

### Creating it (needs dashboard access — not doable from this repo)

A Render **Cron Job** is a separate service from the web service. In the Render
dashboard:

1. **New → Cron Job**, connected to the same repository, branch `main`.
2. Runtime **Python** (it picks up `runtime.txt` → 3.12.7).
3. **Build command:** `pip install -r requirements.txt`
4. **Command:** `flask coach-prune`
5. **Schedule:** `17 3 * * *` (daily, 03:17 UTC — an odd minute to avoid the
   top-of-hour crowd).
6. **Environment:** `DATABASE_URL` pointing at the *same* database as the web
   service, plus `SECRET_KEY`, `JWT_SECRET_KEY` (app.py raises at import
   without them) and `FLASK_APP=app`. It does **not** need
   `ANTHROPIC_API_KEY` — the command makes no model calls.

Verified locally that this exact command works standalone with no web process
running and no user request: it deleted the expired turn, left the fresh one,
was idempotent on a second run, and exited 0.

### Knowing whether it is actually running

A scheduled sweep that silently stops looks identical to one that runs and finds
nothing expired — both print nothing and change nothing. So every sweep writes a
`retention_run` row (even a sweep that deleted nothing), and
`GET /api/verification/self` reports `retention.recent`:

- **PASS** — a sweep is recorded within the last 48 hours, naming its source
  (`cron`, `thread` or `request`)
- **FAIL** — nothing has swept for over 48 hours; the 30-day claim is not being
  honored
- **UNKNOWN** — nothing has ever swept

48 hours because a daily cron plus an hourly in-process sweep means two full
cycles of silence, which is a signal rather than a blip.

**Deletion does not reach backups.** Deleting a row removes it from the live
database. A backup taken beforehand still contains it until that backup ages
out. The provider's backup retention is an unconfirmed P0 in
`docs/operations/production-readiness.md`; until it is confirmed and a restore
is tested, the honest answer is that we do not know the window, not a number
somebody guessed. The export says this in those terms.

**The window is wall-clock, not exact.** A turn is deleted on the first sweep
after it passes 30 days, so in practice something lives 30 days plus up to an
hour (thread interval) or up to a day (cron cadence). The export rounds to
"30 days"; nobody is served by "30 days and up to 24 hours".

## What Rickie is allowed to say

His prompt describes retention in the same terms as the implementation: roughly
ten turns, carried between sessions, plus a few plain notes, older turns falling
away. He is told explicitly that **"every conversation starts fresh for me" is
false** — he said it in the September evaluation, and understating retention to
somebody deciding what to tell him is the worst direction to be wrong in.

If a change here makes any of that untrue, the prompt changes in the same commit.

## Verifying it

```bash
.venv/bin/pytest tests/test_coach_memory.py -q     # includes:
#   an inactive user's rows are swept by ANOTHER user's activity
#   the background thread deletes with no request at all
#   the sweeper is off unless explicitly enabled
#   Forget Conversations clears both turns and notes, caller-scoped
```

To check a live database by hand:

```bash
flask coach-prune          # prints how many it deleted
```

---

# Moderation evidence retention

The second promise. When somebody reports a message, a caption or a photo, the
app keeps a copy so an operator can decide what happened. That copy is private
content belonging to people who did not choose to hand it over, so it is kept on
a clock and deleted on a schedule.

Everything in this section is **verified locally and not yet running in
production** — see "What is NOT guaranteed" below before relying on any of it.

## What is stored, and for how long

| Row | What it is | Clock |
|---|---|---|
| `photo_evidence.ciphertext` | An encrypted copy of the reported image, sealed with Fernet under `STREAKFIT_EVIDENCE_KEY` | **30 days from capture** (`PHOTO_EVIDENCE_MAX_AGE_DAYS`), absolute |
| `report_evidence.content_text` / `context_json` | The reported words and the surrounding context | **30 days after the report is closed** (`EVIDENCE_RETENTION_DAYS_AFTER_CLOSURE`) |
| `report.note` | The reporter's own description of what they were reporting | Deleted with the text evidence, on the same clock |

**The two clocks are deliberately different, and the difference is the point.**

Text and caption evidence runs from **closure**, so a report still being worked
on keeps what a reviewer needs to decide it. Photo bytes run from **capture**,
full stop — an absolute cap that does not stretch because a workflow stalled. A
photo of a child does not become safer to keep because nobody got round to
reviewing the report.

`expires_at` is written on the photo row at capture time and never recomputed.

## What deletes it

One function, `_sweep_moderation_evidence()`, reached two ways:

**1. The in-process sweeper thread** — the same thread that sweeps coach turns,
enabled by `STREAKFIT_RETENTION_SWEEPER=1`, running hourly. The moderation sweep
sits in its own `try` block so that a failure in one retention promise cannot
cancel the other.

**2. `flask moderation-prune`** — the same sweep as a one-shot command, for a
scheduler. `render.yaml` declares a daily cron that runs it. **That cron is
inert** for exactly the reason the coach cron is: Render does not read
`render.yaml` for this service.

There is deliberately **no request-path sweep** for moderation evidence, unlike
layer 2 of the conversation story. Evidence deletion is not something to do a
little of on somebody's page load.

### Reading is gated separately from sweeping

A detail worth stating, because it changes what the sweep's timing means: the
operator route refuses evidence whose `expires_at` has passed **even if the
sweep has not yet run**, and records the refused access as `purged`.

So a delay in the sweep leaves bytes in the database longer than 30 days, but it
does **not** extend the window in which anybody can look at them. The sweep's
cadence decides when the row goes; it does not decide whether the promise holds
for readers.

## Legal holds

A legal hold is the **only** thing that suppresses either clock. It is never an
implicit skip — nothing else in the sweep quietly passes over a row.

- **Set and released** through `POST /api/admin/reports/<id>/legal-hold` with
  `{"hold": true|false}`.
- **Authorization** is the operator boundary and nothing else: the same
  `X-Admin-Secret` gate as every other `/api/admin/*` route. There is no
  separate legal-hold credential and no per-user permission.
- **Setting one requires a written reason.** A request with `hold: true` and no
  reason is refused with 400. Releasing does not require a reason.
- **Both directions are audited.** Setting writes a `legal_hold_set`
  `ModerationAction`, releasing writes `legal_hold_released`, each carrying the
  reason given.
- **Releasing restores the schedule rather than granting an extension.**
  Evidence that passed its deadline while held is deleted by the next sweep
  after release, not 30 days later.

A held row is counted and reported by the sweep (`held_by_legal_hold`) rather
than silently skipped, so "we kept everything forever" cannot happen by
accident.

## What survives deletion

Deletion removes the content and keeps the accounting. After a sweep:

- `photo_evidence` — `ciphertext` is `NULL`, `byte_size` is `0`, `purged_at` is
  set, and `unavailable_reason` is `retention_expired`.
- `report_evidence` — `content_text` and `context_json` are `NULL`, `purged_at`
  is set.
- `report` — keeps that a report existed, its category, when it was filed and
  closed, and what was decided. `evidence_purged_at` is set.
- A `ModerationAction` row is written by `system`: `photo_evidence_purged` or
  `evidence_purged`, noting the rule that fired.

**The `unavailable_reason` field exists so that an empty record reads as
"deleted on schedule" rather than "never captured".** Those are very different
facts about a report, and a reviewer looking at an empty evidence list is
entitled to know which one they are seeing.

## When somebody in a report deletes their account

Account deletion (`DELETE /api/me`, `delete_user_account`) does not delete
moderation records, and it does not shorten or stretch the evidence clock.
It removes the **person**, not the report:

| Their role | What changes at deletion | What stays |
|---|---|---|
| Reporter | `report.reporter_user_id` → `NULL`. The operator view shows `reporter_account_deleted: true` | The report, its category, dates, deadline, status and outcome. A pending report stays in the review queue: the obligation did not leave with the reporter |
| Reported person | `report.reported_user_id`, `report_evidence.author_user_id`, `moderation_action.target_user_id` → `NULL` | Everything else |
| Appellant | `appeal.user_id`, `appeal.reason` (their words) and `appeal.outcome_note` (the note addressed to them) → `NULL`. An **open** appeal is closed with outcome `withdrawn` and a `system` `appeal_withdrawn` action; it can no longer be decided | That an appeal was filed, against which decision, when, and how it ended. The operator's reasoning stays on the `appeal_upheld` / `appeal_overturned` action |

The reporter's `note` — the only free text in a report that the reporter
wrote — goes **at deletion** if the report is closed, with a `system`
`reporter_note_removed` action on the trail. On a **pending** report it stays,
because an investigator still has to decide it and the note is often the only
explanation; it then leaves on the ordinary clock, 30 days after closure. A
legal hold keeps it either way. Evidence is the *reported* content, not the
reporter's, and is never touched by account deletion: it leaves on its own
clock (text 30 days after closure, photo bytes 30 days from capture).

### What "no longer identifies the reporter" does and does not mean

**The claim:** after deletion, no column in the database links a report,
appeal, notice or moderation action to the reporter's (former) account, and no
text the reporter wrote remains on a closed report.

**It is not a claim of anonymity.** What remains can still narrow down who
filed a report, for anyone who can read the operator view or the database:

| What stays | Why it stays | What it can reveal |
|---|---|---|
| `report.team_id` | The review needs to know where it happened | The reporter was a member of that team. In a family team of three, that is nearly a name |
| `report.subject_type` / `subject_ref`, and evidence `context_json` | The reviewer reads the reported message or photo | Who could see that content — the same membership inference |
| `report.created_at`, `due_at` | The minimal record keeps dates | Correlates with a member leaving the team or deleting their account around then (team messages keep their rows, sender cut) |
| A pending report's `note` | The investigator needs it | The reporter's own words, which may name themselves, until 30 days after closure |
| Evidence text / photo bytes | Moderation clock | The reported person's content — not the reporter's, but it shows what they saw |

**Outside the database, and not changed by deletion:**

- **Encrypted backups** (`~/backups/streakfit/`) and the **Neon snapshot**
  keep every row as it was when taken — including `reporter_user_id` — until
  that file or snapshot is destroyed (see the backup retention dates).
- **Neon history** (point-in-time restore) holds the earlier values for the
  plan's history window — 6 hours on Free.
- **Render request logs** record `POST /api/reports` with time and client IP
  (no user id, no body), for the platform's log retention.
- **Rate-limit keys** in Redis are per user id for `/api/reports` and expire
  within the hour.
- **Email notices** carry a report id and a link, never a person; they are
  unaffected.
- **PostgreSQL** does not overwrite a value on UPDATE: the old row version
  remains on disk until vacuumed. Not reachable through the application.

So the precise statement is: *deletion removes every stored link between the
reporter and their reports, and on closed reports every word they wrote; it
does not stop someone with operator or database access from inferring the
reporter from team membership and timing, and it does not reach backups,
snapshots or logs until those expire.*

Deletion is still **refused** (409, nothing changed) for a team creator and
for anyone with guardian-link, consent or permission-audit rows. How a deleted
child or guardian is recorded is an owner decision that has not been made.

## What is NOT guaranteed — read this part

**None of this is scheduled in production yet.** The in-process thread only runs
where `STREAKFIT_RETENTION_SWEEPER=1` is set on the serving command, and the
`render.yaml` cron is inert. Until one of those is actually live, moderation
evidence retention is **implemented and locally verified, not operating**. It
should not be described to anyone as a retention practice the product currently
follows.

**Nothing records that a moderation sweep ran.** The conversation sweep writes a
`retention_run` row even when it deletes nothing, which is what lets
`GET /api/verification/self` report `retention.recent`. **The moderation sweep
writes no such row.** A moderation sweep that silently stops is therefore
indistinguishable from one that runs and finds nothing expired — the precise
failure mode the conversation section of this file was written about. Closing
that gap is outstanding work, not a solved problem.

**Deletion does not reach backups.** Deleting a row removes it from the live
database. A backup taken beforehand still contains the ciphertext, and for photo
evidence that means an encrypted copy of a reported image persists in that
backup until it ages out. The provider's backup retention is an unconfirmed P0
in `docs/operations/production-readiness.md`; until it is confirmed and a
restore is tested, the honest answer is that we do not know the window.

The same applies to any other copy: a database export taken for debugging, a
local development dump, or a restored staging copy each carry their own evidence
until they are destroyed. **No claim should be made that reported content is
irrecoverably deleted 30 days after capture.** What can be said is that it is
deleted from the live database on that schedule.

**Key loss is not deletion, and key retention is not access.** Losing
`STREAKFIT_EVIDENCE_KEY` makes existing photo evidence permanently
undecryptable, which is not the same as having deleted it — the ciphertext is
still there, and still in backups, and the sweep can still remove it without the
key. Conversely, a backup that contains both the ciphertext and a copy of the
key is a readable copy of reported images.

**The window is wall-clock, not exact.** A row is deleted on the first sweep
after its deadline, so in practice content lives its 30 days plus up to an hour
(thread interval) or up to a day (cron cadence, once one exists). The reader-
facing refusal described above is exact; the deletion is not.

## Verifying it

```bash
.venv/bin/pytest tests/test_moderation_operations.py -q   # includes:
#   photo evidence is purged at thirty days from capture
#   text evidence is purged thirty days after closure
#   an open report keeps its evidence
#   a legal hold suppresses deletion, and releasing it restores the schedule
#   a legal hold requires a written reason
#   the sweep touches no unrelated user data
#   the retention thread sweeps moderation evidence
#   one failing sweep does not cancel the other
```

To check a live database by hand:

```bash
flask moderation-prune     # prints purged text rows, photo rows, and holds
```

It prints how many it skipped under legal hold as well as how many it deleted,
because a sweep that deleted nothing and a sweep that was blocked on everything
are different events.
