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

A failed sweep is recorded as `outcome='failed'` with the exception type only,
and a recent failure outranks an older success. **A sweep that dies partway
leaves no success record at all** — the success row is written in the same
transaction as the deletions, so both roll back together.

### 3.3 There is no delivery channel — the real gap

This is the one that matters most and the one nothing in this repo fixes.

`flask moderation-notify` generates notices and prints the undelivered ones. It
marks delivery only with `--mark-delivered`, which is off by default, and the
cron deliberately does **not** pass it: marking a notice delivered because a
cron printed it to a log nobody reads is precisely the lie the table exists to
prevent.

A green cron here does not mean the owner was told. Until a real channel is
configured, the owner finds out by running the command and looking.

`GET /api/admin/reports` surfaces `counts.undelivered_notices` next to the
queue, because a calm-looking queue and a growing undelivered count is exactly
what "nobody is being notified" looks like from the outside.

**A provider-independent delivery interface now exists** (`NotificationChannel`),
with retries, exponential backoff, and the rule that `delivered_at` is set only
beside a receipt from outside the process. What does NOT exist is an adapter for
any actual provider — that is the owner decision below.

To wire one up once a provider is chosen:

1. Write a `NotificationChannel` subclass whose `send()` returns the provider's
   message id and raises on anything else.
2. Register it in `_NOTIFICATION_CHANNELS`.
3. Set `STREAKFIT_NOTIFY_CHANNEL` to its name and `STREAKFIT_PUBLIC_URL` to the
   site root, plus whatever credential the provider needs.

Until step 3, every notice stays undelivered and `moderation.notices_delivered`
fails once an urgent notice is more than an hour old — which is the true state
of the system, reported rather than hidden.

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
flask moderation-notify                 # generate notices, print undelivered
flask moderation-notify --mark-delivered   # ALSO records them as carried by CLI
flask moderation-prune                  # DELETES aged-out evidence. Irreversible.
```

`--mark-delivered` is a claim that a human was told. Only pass it after acting.

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
- `tests/test_notification_delivery.py` — that a failed send, and a send with
  no receipt, are both refused as delivery; that backoff holds; and that a
  message never carries a name, content, evidence or the admin secret.
- `scripts/verification/moderation.py` — blocking and reporting against a
  running app; the operator side only as a boundary, since the suite is built
  to be safe against production without an admin secret. Also asserts that the
  three monitoring checks are present, legible, and separate from one another.

What is **not** verified anywhere: that a notice reaches a person. There is
nothing to verify.
