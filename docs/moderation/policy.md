# Moderation — what it does, and what is still undecided

Blocking, reporting and operator review, built 2026-09-20 on
`product-completion`. Local only; nothing here is deployed.

---

## 1. What "contact" means in StreakFit

There are **no direct messages**, so "stop them contacting you" cannot be
answered by closing a DM channel. The surfaces one person can aim at another
are exactly three:

| Surface | How it reaches a person |
|---|---|
| Team chat | anything posted into a thread you are both in |
| Team photos | an image posted into that thread |
| Team challenges | `TeamChallenge.target_user_id` — the one feature that *names* someone |

Those three are what a block covers. Anything claiming "blocking works"
without reaching the challenge target would leave the only genuinely
person-to-person feature open.

## 2. Blocking

- **Directional as a record, symmetric in effect.** A block is A's decision.
  But enforcement hides the thread **both ways**, because one-way hiding
  announces the block: if B keeps watching A post while A never answers, B has
  learned something the block exists to avoid telling them.
- **Silent.** No notification on block or unblock. No route anywhere tells a
  person who has blocked them.
- **Not destructive.** Nobody is removed from a team, no message is deleted,
  no progress is touched. The roster still shows both people and
  `member_count` is unchanged.
- **Reversible.** Unblocking restores the thread immediately.
- **Not an enumeration oracle.** `PUT`/`DELETE /api/blocks/<id>` answer `204`
  whether or not that id exists, so the route cannot be walked to discover
  which accounts are real. Self-blocking is the only 400, because that is a
  statement about the caller, not about anybody else.

**Shared-team policy, as implemented:** a block inside a shared team is
*mutual invisibility plus a challenge-targeting ban*, and nothing else. See
OPEN DECISION 1 — this is the conservative default, not a settled answer.

## 3. Reporting

Categories: `harassment`, `threats`, `inappropriate_content`, `child_safety`,
`spam`, `other`. Subjects: a **user**, or a specific **message**, **photo** or
**challenge**.

- **You can only report what you can already see, and the server decides
  that, not the client.** The content is resolved FIRST; its team and author
  are derived from the row; the reporter is then authorized against that team,
  its `joined_at` boundary, and the content's state. `team_id` is checked
  against the truth rather than believed, and `reported_user_id` is ignored
  outright for content reports.

  The first version took all three from the request body and had three holes
  because of it — cross-team disclosure, a cross-team automatic takedown, and
  a bypass of the history boundary. All three are reproduced as attacks in
  `tests/test_moderation_security.py`.
- **Every refusal is one 404.** Missing, someone else's, before you joined,
  deleted, expired, already withheld — all identical. A 403 that appears only
  for content which exists is an oracle for other people's teams.
- **Evidence is snapshotted at report time**, not read back later, so an edit
  or a delete cannot empty a report. The photo *caption* is copied, never the
  pixels; an operator opens the image through the existing photo route.
- **The reporter is protected.** `reporter_user_id` never appears in any
  non-operator response, and it is withheld even from the operator **queue**
  view — it appears only in the single-report detail. The reported person is
  never told a report exists.
- **The receipt says nothing.** "Thanks — someone will look at this", and no
  information about the other person or what will happen to them.

## 4. Operator review

Server-enforced by the existing `X-Admin-Secret` gate — the same one every
other `/api/admin/*` route uses. **No new bypass, no second credential, and no
secret needs to be pasted anywhere.**

| Route | Purpose |
|---|---|
| `GET /api/admin/reports` | the queue (`?status=pending\|overdue\|closed\|all`), urgent first |
| `GET /api/admin/reports/<id>` | detail, preserved evidence, action history |
| `POST /api/admin/reports/<id>/action` | take an action |
| `GET /api/admin/reports/<id>/photo-evidence` | decrypt and serve one preserved image, audited |
| `POST /api/admin/reports/<id>/legal-hold` | suspend retention deletion, written reason required |
| `GET /api/admin/appeals` | open appeals |
| `POST /api/admin/appeals/<id>/decide` | uphold or overturn |

Actions, as `MODERATION_ACTIONS` defines them: `dismiss`, `escalate`,
`restrict_reporting`, `lift_reporting_restriction`, `restrict_content`,
`unrestrict_content`, `suspend_social`, `lift_suspension`,
`remove_from_team`.

The queue carries `counts.undelivered_notices` alongside the work itself,
because a calm queue and a growing undelivered count is what "nobody is being
notified" looks like from the outside.

**Every action writes a `ModerationAction` row in the same transaction.** An
action that happened without an audit record is not a state this code can
reach.

## 5. Enforcement, and what each action costs the person

| Action | Effect | Explicitly NOT affected |
|---|---|---|
| `restrict_content` | the message, photo or challenge disappears for everyone including its author — card, caption and metadata, not just the attachment; photo **bytes** 404 on direct request; a restricted challenge cannot be completed | the author's account, streak, other posts |
| `suspend_social` | cannot post chat, photos or challenges **anywhere** | daily mission, streak, XP, acorns, Brain Boost, Side Quests, *and they can still see their team* |
| `remove_from_team` | one membership row deleted | every other team, and all progress |

**Progress is never touched by any moderation path.** "Never punish who showed
up" applies to somebody being moderated too — a suspended person keeps their
streak and can still do today's mission.

**Suspension covers every social write**: chat, photo upload, photo delete,
challenge create, challenge complete, team create, team join, invite rotation.

**It deliberately does NOT cover blocking or reporting.** Owner decision: being
moderated must never remove somebody's ability to protect themselves. A
suspended person can still block, still report, and still read `/api/blocks`.
Anything else would mean a harassment suspension leaves the suspended person
unable to report harassment against them.

**Fail-closed** in two places: a restricted photo 404s on the byte route as
well as vanishing from the thread (hiding it from a list while the image is
one request away is not hiding it), and `_social_suspension_for` treats a
failed read of its own table as *suspended*. A moderation check that passes
when its storage is unavailable is not a moderation check.

## 6. What this does NOT make safe

**Mixed-age teams are still not safe, and blocking and reporting do not make
them so.** This milestone gives an adult in a bad situation a way to act and
an operator a way to respond. It does not provide: age bands, guardian
consent, guardian visibility, proactive detection, or any human reviewer
actually reading the queue. A queue nobody is staffed to read is a data
structure, not a safety system.

---

## Decisions the owner has made, and what was built for each

Seven decisions were taken after the first version of this document. They are
listed here as settled, with the code and the test that carries each one.
`tests/test_moderation_operations.py` maps them the same way in its header.

**1. The reviewer is the owner.** No reviewer account, no role, no second
credential — the operator routes behind `X-Admin-Secret` are the whole of it.
This settles the *mechanism* of old decision 3. It does not settle the
staffing; see "Still open" below.

**2. Review deadlines: 24 hours for `child_safety`, 72 for everything else.**
Computed once at filing (`_review_due_at`) and never recomputed, so a deadline
cannot be pushed back by re-saving a report. The queue sorts urgent first —
sorting by arrival buries a 24-hour report under three days of spam.

**3. Restricted content stays hidden until a decision is made.** This answers
the question old decision 2 left open: an auto-restriction does **not** expire
if unreviewed. Only a moderation action lifts it. An unreviewed report means
the content stays hidden, which is the failure direction that protects a child
rather than the one that protects throughput.

**4. Reported photos are preserved as encrypted evidence, 30 days from
capture.** This replaces old decision 8. Bytes are sealed with Fernet under
`STREAKFIT_EVIDENCE_KEY`, which the application never generates and never
defaults — with no key, **nothing is captured** and the reviewer is told
`no_evidence_key` rather than shown an empty record. Every access, successful
or not, writes an `EvidenceAccess` row, committed *before* the bytes are
produced. The 30 days run from capture and do not stretch because a workflow
stalled.

**5. Appeals are private.** This replaces old decision 4 ("none"). A person
with a decision against them can see it in plain language and contest it once.
An appeal reveals nothing about the reporter or the evidence, filing one
restores nothing by itself, and an upheld appeal changes nothing while an
overturned one reverses. The route into it is hidden entirely for anyone with
no decisions — a standing "Appeals" row in a movement app reads as an
accusation.

**6. Evidence retention: 30 days after closure for text and captions.** This
replaces old decision 5 ("indefinite"). Two clocks, because they are two
different rules: text and caption evidence goes 30 days after the report
*closes*, photo bytes 30 days from *capture*. A legal hold, with a written
reason, is the only thing that suppresses either. What survives is the minimal
audit record — that a report existed, its category, its dates, its outcome.
The same record survives a reporter, reported person or appellant deleting
their account; it simply stops saying who they were
(`docs/operations/privacy-retention.md`, "When somebody in a report deletes
their account").

**7. Repeated false reports need a human.** Dismissals alone never restrict
anyone; no counter acts by itself. Restricting somebody's ability to report is
an operator action requiring a written reason, it is reversible, and it is
appealable. It never blocks `child_safety` reporting or blocking — the two
things a person uses to protect themselves stay available to somebody who has
been wrong before.

---

## Still open — for the owner, not for me to settle

**1. Shared-team blocking.** Implemented as mutual invisibility. The
alternatives, both deliberately not chosen:

- *(a) Block prevents co-membership* — the later joiner is refused entry to a
  team containing someone they have blocked. Stronger, but it leaks: being
  refused tells you a block exists.
- *(b) Block forces one of them out* — rejected outright. Silently removing
  somebody from their family's team is a punishment the blocker gets to
  impose unilaterally.

Current behaviour is the least destructive and the least disclosing. It is
also the weakest: the blocked person remains in the same team and can still
read the thread.

**2. Auto-restrict on `child_safety` reports is still abusable.** Anybody can
hide one message by filing one report. The cost was accepted because the
alternative leaves flagged material in front of a child for as long as review
takes. Narrow on purpose: one piece of content, never the person, reversible
in one click, recorded as a `system` action. Decision 3 settled that it does
not expire. *Should it widen to `threats`?* — still open.

**3. Nobody is notified, and nobody is assigned.** Decisions 1 and 2 gave this
a mechanism and a clock: deadlines are computed, overdue reports are tracked,
and `ModerationNotice` durably records that an obligation came due. **What does
not exist is delivery.** There is no email, no push and no pager in this
application; `flask moderation-notify` prints to a terminal, and a notice with
a NULL `delivered_at` means nobody has been told.

Measuring an obligation is not discharging it. **This remains the largest gap
in the milestone**, it is still a staffing decision, and the delivery channel
is an outstanding deployment requirement — see
`docs/operations/moderation.md`.

**4. Rate limits.** Reporting is capped at 10/hour per user. That is a guess,
not a measured figure.

**5. Exceptional operator actions.** Moderation actions are pinned to the
verified report: `target_user_id` and `team_id` may be supplied but must
match, and a mismatch is a 400 rather than a silent substitution. There is
**no `override: true`**, on purpose — a boolean that lets one route act on
anything is indistinguishable in the audit trail from the route working
normally.

Acting outside a report is a genuine need: a tip-off arrives by email, or an
operator sees something directly. That wants its own route with its own
mandatory reason field, its own `actor` value, and its own entry in the audit
trail — not a flag on this one. **Not built, and needs separate authorization.**
