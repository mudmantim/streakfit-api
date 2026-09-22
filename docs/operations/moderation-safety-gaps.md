# Moderation safety gaps — measured 2026-09-22

Found by filing one real synthetic child-safety report through the production
UI and following it to the end. Every one of these was invisible to the test
suite, which was green throughout.

None is a defect in what shipped. They are tolerances chosen before the
24-hour child-safety promise existed, and they have not been revisited since.

---

## 1. The operator interface did not exist — FIXED

**What happened.** The report could be filed, generated an alert, and paged a
human — who then had no way to act on it. `/admin` served a dashboard calling
six endpoints, none of them the reports API. The alert email said *"Open
&lt;url&gt;/admin to review it"*, and that was a dead end. The report was
closed by assembling HTTP requests with a secret in a shell.

**Why nothing caught it.** Every API test passed. `scripts/verification/moderation.py`
checks the honesty of the monitoring; `tests/test_moderation*.py` check the
endpoints. Nothing asked *"can a person reach this?"* — the same failure this
project shipped in the R2 team layer, where features worked and were
unreachable.

**Fixed:** the queue now lives inside `/admin`, covered by
`tests/test_moderation_operator_ui.py`, of which **21 of 23 fail against the
old page**.

---

## 2. A 57-minute silent window between filing and notice

**Measured, not estimated:**

| | |
|---|---|
| Report filed | `17:58:45Z` |
| Notice created and delivered | `18:55:37Z` |
| **Silent window** | **56m 52s** |

`_generate_moderation_notices()` runs inside the worker pass, on
`_RETENTION_THREAD_INTERVAL_S = 3600`. A report filed just after a pass waits
almost a full hour before anything notices it exists.

Throughout that window `moderation.notices_delivered` reported **PASS — "no
urgent notice is waiting"**. Technically true and materially misleading: there
was no *notice*, because none had been generated. There was a *report*, with a
24-hour deadline already running.

**Proposed fix, needing approval.** Generate the notice **at filing time**, in
the same request that creates the report, and leave delivery on the worker.
Generation is a cheap insert against a uniqueness constraint; delivery is the
part that needs to be retryable and unattended. That removes the window
entirely rather than shortening it. Roughly 4% of a 24-hour budget is being
spent on an interval that exists for delivery, not for noticing.

---

## 3. The monitoring reads notices, not reports

`moderation.notices_delivered` asks *"is an urgent notice waiting
undelivered?"*. It cannot distinguish:

- nothing has happened, and
- a child-safety report was filed 55 minutes ago and has not been picked up.

Both render as **PASS**.

**Proposed fix.** Add a check on **pending reports whose deadline is
approaching**, independent of whether a notice exists. The data is already
there — `Report.due_at` and `_review_queue_counts()`. Fixing gap 2 narrows
this, but does not close it: a report can also sit *noticed and undelivered*
through a provider outage, which this check would see and the current one
would not.

---

## 4. A 48-hour staleness threshold against a 24-hour promise

`RETENTION_STALE_AFTER_HOURS = 48` governs `moderation.delivery_worker`. It
answers *"has a worker run recently"* on a two-day horizon.

Against a **24-hour** review deadline, a worker that dies immediately after a
pass keeps reporting **PASS for twice the window it protects**. By the time
monitoring admits a problem, the deadline it exists to defend has already
passed.

**Proposed fix.** A separate, shorter threshold for the moderation worker —
two or three missed hourly passes, not 48 hours. The retention sweep can keep
48: nothing about a retention promise breaks in an afternoon.

**Observed during this exercise:** at 18:35 the last pass read `0.6h ago` and
I raised the possibility the worker had stalled. It had not — the next pass
ran on schedule at 18:55. That was my error, and it illustrates the real
problem from the other side: with no explicit expected-interval in the check,
neither a human nor the dashboard can tell early from late.

---

## 5. No reporter note, and no reviewer annotation

`POST /api/reports` takes a category and a subject. The filing UI offers six
buttons and no free text. So:

- **A reporter cannot say what they saw.** A child who noticed something has
  one of six words to describe it.
- **A reviewer cannot annotate before deciding.** The only note field is on
  the closing action, so context can be recorded at disposition and nowhere
  else. The synthetic report carried `note: present=False` for exactly this
  reason.

**Proposed fix, needing approval and care.** An optional free-text field on a
report is a new place for personal data — a child's own words, about
something distressing. It would need the same treatment as coach turns:
retention window, export inclusion, deletion on account removal, and a clear
decision about whether a reviewer's annotation is visible to the reported
person on appeal. **This is a design question, not a form field.**

---

## Is an independent scheduled backstop necessary?

**Yes, and this exercise is the argument for it.**

The delivery worker is a thread inside gunicorn. It does not run while the
service is deploying, restarting, crash-looping, or suspended — and we
suspended production for eleven minutes during this very deployment. A report
filed in that window waits for the next boot, and the boot guard means a
failed deploy leaves *no* process running at all.

`render.yaml` already specifies `streakfit-moderation-notify` as an hourly
cron: **$1/month, unapproved**. The `source` field distinguishes `cron` from
`thread` precisely so either satisfies monitoring and a manual run never does,
and `tests/test_unattended_operation.py` asserts that running both is safe —
delivery takes a durable five-minute lease, so two runners produce exactly one
send.

**Recommendation: provision it.** One dollar a month removes the single point
of failure from a child-safety alert path. It is the cheapest item in this
document and the only one that survives the app being down.

---

## Priority

| | Gap | Cost | Blocks a child using this? |
|---|---|---|---|
| 1 | Operator UI | **done** | was **yes** |
| 2 | Cron backstop | $1/mo | **yes** — nothing delivers while deploying |
| 3 | Notice at filing time | small | no, but it spends 4% of every deadline |
| 4 | Pending-report monitoring | small | no |
| 5 | Worker staleness threshold | trivial | no |
| 6 | Reporter note field | **design** | no — but it limits what a child can say |
