# Moderation operations — what runs, what does not, and what a deploy still owes

`app.py` points here from `ModerationNotice` and from `flask moderation-prune`.
This is the operational half of `docs/moderation/policy.md`: the policy says
what the owner promised, this says what has to be true in production for the
promise to hold.

Everything below was verified locally. Nothing in it has been applied to
production, and no key has been generated.

---

## 1. The honest summary

Three moderation obligations run on a clock: evidence has to be deleted on
time, a review deadline has to be noticed, and an appeal has to reach somebody.

Two of the three work today as long as the web service is up. The third —
anyone actually being *told* — does not exist. There is no email, no push, no
pager, and no integration in this application. `flask moderation-notify` prints
to a terminal.

**A notice with a NULL `delivered_at` means nobody has been told.** The table
is built to make that fact visible rather than to imply the opposite.

---

## 2. What runs by itself

`_retention_sweeper_loop` runs in-process, hourly, and does three things in
three separate `try` blocks so no one failure cancels the others:

| Sweep | What it does |
|---|---|
| coach turns | the pre-existing coach retention promise |
| `_sweep_moderation_evidence` | text/caption evidence 30 days after a report **closes**; photo bytes 30 days from **capture**, absolute |
| `_generate_moderation_notices` | writes a notice row the first time a report turns urgent or passes its `due_at`, and when an appeal is filed |

This covers a service that is up. It covers nothing while the service is down,
mid-deploy, or scaled to zero.

Generation is idempotent through a unique constraint on
`(subject_type, subject_ref, kind)`, not through in-memory bookkeeping — so a
restart does not re-notify, and running the sweep every minute for a week still
notices a given report once.

---

## 3. Outstanding deployment requirements

These are the things a deploy still owes. None can be completed from this
repository.

### 3.1 `STREAKFIT_EVIDENCE_KEY` — required for photo evidence

A urlsafe-base64 32-byte Fernet key. The application **never generates one**,
never defaults it, and never stores it where the database or this repository
can reach.

With no key set, `_evidence_cipher()` returns `None` and **nothing is
captured** — the reviewer is shown `no_evidence_key` rather than an empty
record that looks like "there was no photo". That is the deliberate direction
to fail.

Consequences to get right:

- The same key must be set on the web service **and** on any cron that prunes
  photo evidence. A *different* key there leaves photos undecryptable.
- Rotating it makes every previously sealed photo undecryptable. `key_id` (a
  truncated HMAC fingerprint, non-secret) is stored per row so two keys can be
  told apart across a rotation, but there is no re-encryption path.
- **Not generated, and deliberately not generated here.** Generating a
  production key is the owner's action, in the owner's secret store.

### 3.2 The `render.yaml` cron blocks are inert

`render.yaml` describes two jobs:

- `streakfit-moderation-evidence-sweep` → `flask moderation-prune`, 04:17 UTC
- `streakfit-moderation-notices` → `flask moderation-notify`, hourly at :07

**Render does not read this file for this service.** The blocks are
documentation of intent, in the same state as the coach-retention cron above
them. Creating them in the dashboard is an outstanding requirement. `region`
and `plan` are marked `VERIFY` because they must match the web service and
were written without dashboard access.

Until they exist, both sweeps depend entirely on the web service being up.

### 3.2b Retention monitoring now exists, and says when it is not running

`retention_run` rows carry a `kind`, and each promise is checked separately by
`GET /api/verification/self`:

| Check | Answers |
|---|---|
| `retention.recent` | are expired **conversations** being deleted? |
| `retention.moderation` | is expired **evidence** being deleted? |
| `moderation.notices_delivered` | has anybody been told about urgent work? |

Before the `kind` column these shared one row, so a moderation sweep could
report conversation retention as healthy. They can no longer vouch for each
other, and a check with no evidence says `UNKNOWN` rather than passing.

**Both retention checks now require an UNATTENDED run.** `retention_run.source`
distinguishes how a sweep was invoked, and only `cron` (an external scheduler)
or `thread` (the in-process worker) can produce a `PASS`:

| Source | Means | Passes the check |
|---|---|---|
| `thread` | the in-process hourly worker | yes |
| `cron` | an external scheduler | yes |
| `manual` | somebody typed the command | **no** |
| `request` | piggy-backed on a user's request | **no** |

One hand-typed `flask moderation-prune` used to satisfy the check for 48 hours
with no scheduler existing anywhere — and because the command labelled itself
`cron`, nothing afterwards could tell the difference. The command now labels
itself by how it was invoked (`--scheduled` or not), and a manual run reports
`UNKNOWN` with the reason stated: *a manual run exists, which proves somebody
swept once, not that anything sweeps on its own*. A fresh manual sweep can no
longer mask a scheduler that has stopped.

`request` fails the same way and for the same reason: it means a user happened
to arrive. An idle service serves no requests and sweeps nothing.

A failed sweep is recorded as `outcome='failed'` with the exception type only,
and a recent failure outranks an older success. **A sweep that dies partway
leaves no success record at all** — the success row is written in the same
transaction as the deletions, so both roll back together.

### 3.3 Delivery: unattended, prioritised, and still without a provider

The in-process worker (`_retention_sweeper_loop`) now runs a delivery pass
every cycle, in its own `try`. Before this, notices were generated hourly and
delivered only when somebody typed `flask moderation-notify` — an alert nobody
is awake to trigger is not an alert.

What the pass guarantees:

- **Urgent first.** `_notices_for_delivery` orders `urgent_filed` ahead of
  everything else, oldest first within each class, and applies retry
  eligibility **before** the batch limit. Slicing first and filtering second
  reproduced as a newly filed child-safety alert never being attempted at all,
  because fifty older notices sitting in backoff filled the batch window.
- **Nothing urgent is ever abandoned.** There is no attempt cap. Backoff is
  bounded — `(0, 1, 5, 15, 60, 240)` minutes, then a ceiling of 15 minutes for
  urgent notices and 4 hours for ordinary ones — so a recovered provider drains
  the backlog with no database edit.
- **One transaction per notice.** A single commit for the batch reproduced as
  messages accepted by the provider and zero recorded, because one failing
  commit erased the lot — `attempts` included, so the retry had no backoff.
- **Durable claims.** `claimed_at`/`claimed_by` are a 5-minute lease taken by a
  single conditional `UPDATE`, committed before the send. Two workers racing
  both issue it; the database serialises them and exactly one wins. An expired
  lease is reclaimable, so a crashed worker's notices are not stranded.
- **A claim is not a delivery.** `delivered_at` is set only beside a receipt
  from outside the process. A channel that returns nothing is a failure.

**Residual duplicate-delivery risk, stated plainly.** The order is claim → send
→ record. A crash between the send and the record leaves the claim in place
until the lease expires, then retries with the **same** `provider_key`, minted
and committed at claim time. A provider that honours idempotency keys collapses
that retry into one message; a provider that does not will deliver twice. This
window cannot be closed from inside one process, and a duplicate child-safety
alert is the right failure to prefer over a missing one.

Monitoring splits into three checks that fail independently, because collapsing
them is what produced a `PASS` on an empty database with no provider and no
worker:

| Check | Answers | Knowable from |
|---|---|---|
| `moderation.delivery_configured` | is a channel configured? | environment alone |
| `moderation.delivery_worker` | has an unattended pass actually run? | a `notification_run` record |
| `moderation.notices_delivered` | is anything urgent stuck? | the notice table |

An empty queue with no capability now reports `UNKNOWN` — *an empty queue is
not evidence that alerts work* — rather than `PASS`. Notices that have failed
`_NOTIFY_PERSISTENT_AFTER` (5) times are surfaced on that check as *failing
persistently*, so a provider that is configured, called, and broken is visible
rather than silent.

`GET /api/admin/reports` still surfaces `counts.undelivered_notices` next to the
queue, because a calm-looking queue and a growing undelivered count is exactly
what "nobody is being notified" looks like from the outside.

**What still does not exist is an adapter for any real provider** — that is the
owner decision below, and it is the one thing none of the above substitutes for.
`console` prints and deliberately raises rather than returning a receipt, and
it declares `certifies_delivery = False`, so setting
`STREAKFIT_NOTIFY_CHANNEL=console` **fails** `moderation.delivery_configured`
rather than reading as capability. A channel that can never deliver is not
configuration; counting it as such was the R2 false green one layer in.

To wire one up once a provider is chosen:

1. Write a `NotificationChannel` subclass whose `send(subject, body,
   idempotency_key=...)` returns the provider's message id and raises on
   anything else. Pass the key through to the provider if it supports one.
2. Register it in `_NOTIFICATION_CHANNELS`.
3. Set `STREAKFIT_NOTIFY_CHANNEL` to its name and `STREAKFIT_PUBLIC_URL` to the
   site root, plus whatever credential the provider needs.

Until step 3, every notice stays undelivered, `moderation.delivery_configured`
fails, and `moderation.notices_delivered` fails once an urgent notice is more
than an hour old — which is the true state of the system, reported rather than
hidden.

### 3.4 Staffing — still a decision, not a bug

Open decision 3 in the policy doc ("who reads the queue") is unchanged. The
deadlines are computed, the overdue state is tracked and now noticed. Nobody is
assigned to act on any of it. Review deadlines measure an obligation; they do
not create a reviewer.

---

## 4. Operator commands

All read-only unless stated.

```
flask moderation-queue                  # the queue: pending, urgent, overdue
flask moderation-notify                 # generate notices, attempt delivery
flask moderation-notify --scheduled     # same, recorded as an unattended run
flask moderation-notify --mark-delivered   # ALSO records them as carried by CLI
flask moderation-prune                  # DELETES aged-out evidence. Irreversible.
flask moderation-prune --scheduled      # same, recorded as an unattended run
flask coach-prune [--scheduled]         # DELETES expired conversation turns.
```

`--mark-delivered` is a claim that a human was told. Only pass it after acting.

`--scheduled` is a claim that a scheduler invoked the command, not a person. It
changes what monitoring believes, so passing it by hand puts a lie in the
record that nothing downstream can detect.

`moderation-prune` is the only destructive one. A legal hold is the sole thing
that suppresses deletion, and it requires a written reason.

---

## 5. What is verified, and how

- `tests/test_moderation_operations.py` — deadlines, encrypted evidence, access
  auditing, retention, appeals, notices, and that the in-process thread runs
  both sweeps and that one failing does not cancel the other.
- `tests/test_migrations.py` — the schema builds from empty through Alembic and
  matches the models, `moderation_notice` included.
- `scripts/uicheck.py` — `check_appeals_are_reachable` and
  `check_appeals_stay_hidden_for_everybody_else`, because the appeals UI once
  shipped with working endpoints and no way in, and every API test still passed.
- `tests/test_retention_monitoring.py` — that monitoring can tell a dead
  sweeper from a quiet one, that neither promise can vouch for the other, and
  that a failure record never carries the exception's text. Verified by
  mutation: removing the `kind` filter or ignoring a failed outcome both make
  these tests fail.
- `tests/test_notification_delivery.py` — 43 tests, one per defect the
  operational-readiness audit reproduced: that an empty queue with no provider
  is not a `PASS`; that a manual pass never satisfies the worker check; that a
  new urgent alert is attempted ahead of an older backlog; that an urgent
  notice is never exhausted and a recovered provider drains it; that two
  workers cannot both claim one notice and a stale claim is recoverable; that
  a provider acceptance followed by a database failure is never counted as
  delivered; that a failed send and a send with no receipt are both refused;
  that backoff holds; and that a message never carries a name, content,
  evidence or the admin secret. The channels are fakes — no real message is
  sent anywhere, which is the reason the interface exists separately from any
  provider.
- `scripts/verification/moderation.py` — blocking and reporting against a
  running app; the operator side only as a boundary, since the suite is built
  to be safe against production without an admin secret. Also asserts that the
  three monitoring checks are present, legible, and separate from one another.

What is **not** verified anywhere: that a notice reaches a person. Every test
above runs against a fake channel, and no real provider has ever been called.
The machinery around delivery is verified; delivery itself is not, and will not
be until a provider is chosen, configured, and observed working in production.
