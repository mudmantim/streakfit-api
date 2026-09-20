# Stage 1 — safety and privacy fixes

**Date:** 2026-09-20 · **Branch:** `product-completion`, local only.
Not pushed, not deployed. No production change, no billing, no paid AI calls,
no child accounts enabled, nobody invited.

---

## 1. Defects reproduced and fixed

### 1.1 Teammates were being told each other's login identifiers

**Reproduced first.** A member registered as `olivia.hill@example.com`
appeared verbatim on the roster of every team they joined and in that team's
history. Setting a display name changed nothing, because the roster never
consulted the display name at all:

```
{"username": "olivia.hill@example.com", "current_streak": 0, ...}
"olivia.hill@example.com joined the team"
```

The codebase already knew people register with email addresses —
`_safe_display_name()` exists precisely to stop Rickie reading one aloud, and
its docstring says so. Four team serialisers were doing in writing,
permanently, what Rickie had been stopped from doing.

**The audit found five surfaces, not the four I first counted**, and a sixth
path:

| Surface | What it leaked |
|---|---|
| Team roster | `username` per member |
| Team history (moments) | `username` in permanent display text |
| Chat | `sender_username` |
| Challenge cards | `from_username` / `to_username` |
| **Challenge announcement** | the login **written into the stored chat body** |
| Message serialiser fallback | a second code path taken only when a caller omits the pre-resolved map |

The fifth is the one that matters most, and a serialiser-only fix would have
missed it entirely: `body=f"{sender.username} started a challenge: ..."`
baked the login into a durable row. Chat messages never expire, so it would
have sat in old threads indefinitely.

The sixth is the kind of thing that survives a careful fix: it only runs on a
rarely-taken branch, so fixing the batch path alone would have left a live
leak behind it.

**Historical records: fixed without a migration.** Rather than rewrite stored
history — destructive, and your call — announcement bodies are **derived at
read time** for exactly the rows the system wrote (`challenge_id` is non-null
only on announcements). An existing database is fixed by reading it. A
person's own typed message has no `challenge_id` and is never touched. Both
halves are tested, including a **forged legacy row** in the old format.

**So no migration is needed and none is proposed.** There is no remaining
category of stored user-visible text containing a login. The one place a login
is still stored and served is `admin_stats`, which is behind the admin secret
and is a deliberate operator surface.

**A note on what "safe name" means.** Members with no display name and a
login `_safe_display_name` refuses — which is exactly the person this protects
— now show as `Member 1`, `Member 2`, ordinal by join order. Stable,
distinguishable, and derived from position rather than from anything about the
person. It is also not friendly, and the real fix is to ask people to choose a
name; that is product work, flagged below.

### 1.2 Joining a team handed you everything said before you arrived

**Reproduced first.** A newcomer received every prior message:

```
NEWCOMER SEES MESSAGES FROM BEFORE THEY JOINED:
  ['private thing said earlier', "Glad you're here."]
```

`TeamMembership.joined_at` already existed and was read **nowhere** — one
match in the codebase, the column definition. `_member_since()` is what makes
it mean something.

Three read paths gated: **messages**, **moments**, and the **photo bytes**.

The photo route is the one that counts. Filtering the thread is not a boundary
if the image is one direct request away, so the guard sits on the route that
serves the bytes and returns the same 404 as a photo that never existed.
Proved by mutation: removing only that guard fails the test that skips the
list and requests the bytes directly — the exact test a UI-only fix would
pass.

**Aggregates are deliberately not filtered.** The campfire total is the team's
shared accomplishment and is the whole point of the feature; "47 logs on the
fire" tells a newcomer nothing about anybody. Named events are filtered,
because "X shared a photo" tells them who has been in this family's team.

### 1.3 Rickie had no response to a frightening disclosure

The prompt had a well-built medical boundary and a disordered-eating override.
It had **nothing** for self-harm, suicidal ideation, abuse, bullying or
emergencies, and no concept of naming a person rather than a clinician. "See a
doctor" is not an actionable instruction for a nine-year-old.

Added: a response shape (take it seriously, say it is bigger than him, name a
real person, stay one line), danger-now first, six absolute prohibitions, an
indirect/ambiguous rule that leaves one open door rather than interrogating, a
no-bypass rule, and a dependency rule that refuses to be the only person
somebody talks to without rejecting them.

Written up in `docs/safety/crisis-response-protocol.md`.

**Keyword matching is explicitly not the mechanism.** `_SENSITIVE_VETO`
decides what may be *stored* in Coach Notes, and was the only place these
words appeared before this change — easy to mistake for safety behaviour. A
test asserts the reply path does not branch on it.

---

## 2. Tests added, and what they are worth

| File | Tests | What it proves |
|---|---|---|
| `tests/test_peer_identity_privacy.py` | 10 | No member-visible endpoint returns another account's login |
| `tests/test_team_history_boundary.py` | 8 | Content before `joined_at` is unreachable, including via direct byte requests |
| `tests/test_coach_safety_rules.py` | 7 | The crisis rules are present, and the grader works |
| `scripts/coach_safety_eval.py` | 13 cases | Offline; costs nothing |

The identity tests **scan the whole response for the login string** at every
endpoint, rather than asserting a field is right — the failure mode is a field
somebody forgot, not a field somebody got wrong.

**Verified by mutation, not by passing:** reverting the roster fails 5 of 10
identity tests; removing only the photo-bytes guard fails the direct-request
test.

**What the safety tests do NOT prove.** They cannot tell you Rickie behaves
this way — that needs the live model. The offline suite tests the **grader**,
over hand-written replies known to be good and known to be bad. That earned
its keep immediately: two *good* fixtures were rejected by a regex expecting
"I'm just a raccoon" when the prompt instructs "a raccoon in an exercise app".
Finding that offline cost nothing.

**A passing test file here is not evidence that Rickie is safe and must not be
reported as though it were.**

---

## 3. Verification

| Check | Result |
|---|---|
| `make check` — lint, types, build, 585 tests | **585 passed, 0 failed** |
| `make uicheck` — real UI, headless Chrome, 390×844 | **157 checks, 0 problems** |
| `scripts/coach_safety_eval.py` — offline | 13 cases, every good reply accepted, every bad rejected |

Two existing tests failed during this work and both were right to:

- `test_roster_witness_does_not_reintroduce_an_n_plus_1` caught a real
  regression — my first version added a separate liveness query. Liveness now
  comes free from the name lookup. **The threshold was not touched.**
- The team UI check asserted the kid's *login* appeared on the roster, which
  is now exactly what must not happen. Rewritten to drive the real journey —
  the kid sets a display name, the parent sees it — plus a new assertion that
  the login appears nowhere on the rendered page.

---

## 4. Remaining exposure and safety concerns

**Not fixed, and I stopped rather than widening Stage 1:**

1. **No blocking, reporting or moderation exists anywhere.** Zero routes. A
   child in a team with an adult who behaves badly has no in-app recourse.
   This is the largest remaining child-safety gap in the team layer.
2. **Rejoining loses your old window.** Leaving deletes the membership row, so
   rejoining sets a new `joined_at`. Safe, and a real cost — a parent who
   leaves a family team and returns loses sight of their own family's earlier
   messages. **Fixing it properly needs membership history, which does not
   exist.** See §5.
3. **Free-text chat and photo sharing between adults and children** are
   unrestricted within a team, with no age model to restrict them by.
4. **Three of the four statutory duties on Rickie are not implemented**: the
   AI disclosure, the break reminder on long sessions, and an explicit
   sexual-content prevention rule. Only crisis referral was done here.
5. **No live evaluation of Rickie's safety behaviour has been run.** The suite
   is built and waiting for your approval to spend.
6. **No region-specific crisis numbers**, because that needs a country the
   product deliberately does not collect. Rickie names kinds of people and
   says "emergency services in your country".
7. **No human escalation path.** Nothing routes a disclosure to a person at
   StreakFit. Inventing one nobody staffs would be worse than not having it.
8. **`Member 2` is not a friendly thing to call somebody.** The real fix is
   prompting for a display name at sign-up or first team join.

**Cost, because every rule added to Rickie is billed on every reply anybody
ever sends:** the prompt grew 13,642 → 17,005 characters, about +841 input
tokens, roughly **+$0.0017 per reply** — a typical reply goes from ~$0.0099 to
~$0.0116, **up 17%**.

---

## 5. Decisions needed before Stage 2

**From Stage 1, newly surfaced:**

1. **Rejoining.** Should somebody who rejoins regain their earlier window?
   Doing it properly means keeping membership history (a new table or
   soft-deleted rows) so access can be reconstructed from the windows a person
   was actually present for. Today's behaviour is the safe default and is
   asserted by a test that says a change should be deliberate.
2. **Display names.** Prompt for one at sign-up, at first team join, or leave
   `Member 2`?

**Still open from the initial report, unchanged:**

3. **Age thresholds** — 13 and 18, confirming the research.
4. **Ask Rickie for under-13s at all.** My recommendation remains no at launch.
5. **Ask Rickie for teens** — on by default with guardian disable, or off
   until enabled.
6. **Mixed-age teams** — may a child join a team created by an adult who is
   not their guardian?
7. **Parental access.** Corrected in the initial report: for under-13s,
   parental *review* of the child's information is a requirement, not a values
   choice. The values question survives only for 13–17.
8. **Launch posture** — adults+teens, adults+teens+under-13 solo mode (my
   recommendation), or full parity.

---

## 6. Unresolved legal and operational requirements

**Legal — unchanged from the initial report, and none resolved by Stage 1:**

- Whether Rickie falls within the companion-chatbot definitions. This
  determines whether the AI disclosure, break reminder and published protocol
  are mandatory or advisable. **The protocol document cites them and
  deliberately does not classify him.**
- Whether StreakFit is "directed to children", which changes the obligations
  regardless of any age screen.
- Whether an approved verifiable-consent method can work for an account model
  with no email address.
- Whether team chat among 8 members counts as making information publicly
  available.
- Whether Anthropic's terms supply the written processor assurances.

**Operational — unchanged, and unresolvable from inside this repository:**

- **Retention cron: NOT LIVE.** Declared in `render.yaml`, which the
  dashboard-configured service never reads, and marked INERT in the file.
- **Backup retention and restore: UNKNOWN.** Never verified with the provider,
  no restore ever tested.

**No compliance claim is made anywhere in this report.** Passing tests
establish that specific behaviours are what the code says they are.
