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

- **You can only report what you can already see.** The report route repeats
  the same membership check the read routes use. Without it, "report this
  message" becomes a way to ask the server to fetch content you were never
  shown — a safety feature that is also the hole.
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
| `GET /api/admin/reports` | the queue (`?status=pending\|closed\|all`) |
| `GET /api/admin/reports/<id>` | detail, preserved evidence, action history |
| `POST /api/admin/reports/<id>/action` | take an action |

Actions: `dismiss`, `restrict_content`, `unrestrict_content`,
`suspend_social`, `lift_suspension`, `remove_from_team`.

**Every action writes a `ModerationAction` row in the same transaction.** An
action that happened without an audit record is not a state this code can
reach.

## 5. Enforcement, and what each action costs the person

| Action | Effect | Explicitly NOT affected |
|---|---|---|
| `restrict_content` | the message/photo disappears for everyone, including its author; photo **bytes** 404 on direct request | the author's account, streak, other posts |
| `suspend_social` | cannot post chat, photos or challenges **anywhere** | daily mission, streak, XP, acorns, Brain Boost, Side Quests, *and they can still see their team* |
| `remove_from_team` | one membership row deleted | every other team, and all progress |

**Progress is never touched by any moderation path.** "Never punish who showed
up" applies to somebody being moderated too — a suspended person keeps their
streak and can still do today's mission.

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

## Open decisions — for the owner, not for me to settle

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

**2. Auto-restrict on `child_safety` reports.** Implemented: a `child_safety`
report hides the content immediately, before any human review. **This is
abusable** — anybody can hide one message by filing one report. The cost was
accepted because the alternative leaves flagged material in front of a child
for as long as review takes. Narrow on purpose: one piece of content, never
the person, reversible in one click, and recorded as a `system` action.
*Should this widen to `threats`? Should it expire automatically if unreviewed?*

**3. Who reads the queue.** There is no reviewer. The routes exist and the
audit trail works, but nobody is assigned, there is no SLA, and nothing
notifies anyone that a report arrived. **This is the largest remaining gap in
the milestone** and it is a staffing decision.

**4. Appeals.** None. A suspended person is told their posting is paused and
given no route to contest it. Deliberately left unbuilt rather than guessed.

**5. Retention.** Reports and evidence are kept indefinitely. Evidence
contains private content by design, so this needs a retention window
consistent with the coach-turn policy.

**6. Rate limits.** Reporting is capped at 10/hour per user. That is a guess,
not a measured figure.
