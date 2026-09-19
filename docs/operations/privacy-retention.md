# Conversation retention — what is actually guaranteed

StreakFit's data export tells a user `coach_conversation_days: 30`. This file is
the accounting behind that sentence: what deletes their conversation, when, and
under what conditions it does **not**.

Written because the promise was, for a while, only true for people who kept
using the app — which is the opposite of who a retention window is for.

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

**The cron in `render.yaml` is not live.** That file is marked PROPOSED: Render
applies a Blueprint only when the service is created from or linked to one, and
StreakFit's live service is configured in the dashboard. **Until somebody links
the Blueprint or adds the cron job by hand, layer 4 does not exist**, and
retention depends on the web service staying up.

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
