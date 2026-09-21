# Child safety and launch readiness — initial report

**Date:** 2026-09-20 · **Branch:** `product-completion`, 91 commits ahead of
`main`, working tree clean, nothing pushed or deployed.

This is the report requested before any architectural change. Everything in
section 1 was verified by running it today, not carried over from the previous
report.

---

## 1. Verified current state

| Check | Result |
|---|---|
| `make check` (lint, types, build, pytest) | **560 passed**, 0 failed |
| `scripts/verify_all.py` end-to-end | 108 passed, 0 failed *(last run this session)* |
| `make uicheck` real-UI, 390×844 | 156 checks, 0 problems *(last run this session)* |
| Content store | **440 accepted, 311 revise, 47 rejected** — matches the report |
| Accepted items by review depth | 424 `sourced`, 16 `read` (the 16 are jokes, nothing to source) |
| Billing / sponsorship / Stripe | **None implemented.** `is_plus` is a bare boolean, referenced twice |

### What does NOT exist, confirmed by reading the code

**There is no age concept anywhere in the product.** `User` has
`username, display_name, password_hash, skill_level, display_mode, rickie_mode,
xp_total, acorns_total, acorns_spent, is_plus`. No date of birth, no age band,
no guardian link, no consent record, no parental-control state. The `min_age`
field (9/13/16) exists only on *content items* — it labels material, it does
not describe an account and nothing reads it per-user.

So every child-safety mechanism in this brief has to be built. Nothing is
partially implemented; there is nothing to extend.

---

## 2. Child-safety and privacy gap analysis

### 2.1 A child's login identifier is shown to every teammate — **reproduced**

Registration accepts any 2–80 character username with no format rule, and the
codebase already knows people register with email addresses. The team roster
and team history return **`User.username` verbatim**, not `display_name` and
not the `_safe_display_name()` guard that exists precisely to stop Rickie
saying a login out loud.

Reproduced today with a throwaway account:

```
roster, as another team member sees it:
  {"username": "olivia.hill@example.com", "current_streak": 0, ...}
team history:
  "olivia.hill@example.com joined the team"
after the child sets a display name of "Liv":
  "olivia.hill@example.com"     <- unchanged
```

A child's email address is therefore disclosed to every member of every team
they join, written permanently into team history, and the display-name feature
does not mitigate it. This is the single most concrete privacy defect found.

### 2.2 Joining a team backfills the entire history — **reproduced**

`TeamMembership.joined_at` exists on the model and **is never read anywhere in
the codebase** (one match: the column definition). No query filters team
content by when the viewer joined. A new member immediately receives every
prior chat message, and the same membership-only gate governs photographs.

```
newcomer sees messages from before they joined:
  ['private thing said earlier', "Glad you're here."]
```

A child added to an existing team inherits every conversation and photograph
that preceded them. The column being present but unused makes this cheap to
fix.

### 2.3 Unrestricted adult-to-child free-text contact is already possible

There is **no direct-message route** — the only messaging is team chat, which
is a genuine structural protection and worth keeping. But within a team:

- any user may create a team; any user holding a code may join;
- teams hold 8 members free, 25 with a Plus member;
- members exchange **free text** and **photographs**;
- there is **no blocking, reporting, moderation or abuse-handling code
  anywhere** — zero routes.

Invite codes are shared out of band, and there is no stranger discovery, which
is the existing protection. It is not sufficient on its own for a mixed-age
product.

### 2.4 Ask Rickie has no safety handling for the disclosures that matter most

The coach prompt is genuinely good on the medical boundary — there is a
well-written rule on when to refer to a doctor and when *not* to, plus an
override for disordered-eating signals. But across the whole file there is
**no instruction covering self-harm, suicide, abuse, bullying, or emergency
escalation**, and no concept of "tell a trusted adult". The only place those
words appear is `_SENSITIVE_VETO`, a regex that prevents such text being
*stored* in Coach Notes. It does nothing about how Rickie *responds*.

For a nine-year-old, "see a doctor" is also not an actionable instruction.

What actually leaves the service, verified: the user's message (≤500 chars),
up to ~10 recent turns, and a server-derived context block containing the
*safe* name, streak, best streak, mission count and level — **not** the
username or any identifier. Context minimisation is already handled well. The
free text is the exposure.

Access control on Ask Rickie today is rate limits only (10/day, 3/min). There
is no per-account gate, so no way to disable it for one account.

### 2.5 Operational claims — still not operational

- **Retention cron: NOT LIVE.** Declared in `render.yaml`, which is marked
  INERT in the file itself because the service is dashboard-configured and
  never reads it. The in-process sweeper is behind `STREAKFIT_RETENTION_SWEEPER`,
  off by default.
- **Backup retention and restore: UNKNOWN.** Unverified against the provider,
  no restore ever tested. I cannot resolve either from inside this repository.

### 2.6 Team invite link is broken — **reproduced at code level**

`?join=CODE` is captured in `init()`, which immediately calls
`history.replaceState` to wipe it from the URL. The captured code is consumed
inside a card builder that only runs when the team pane renders — and a user
with no team has no Team tab, so it never runs. The code is captured, the URL
is cleaned, and nothing is shown. A reload loses the invitation entirely.

---

## 3. Highest-priority defects, ranked

| # | Defect | Class | Cost |
|---|---|---|---|
| 1 | Login identifier (often an email) shown to all teammates and in permanent history | Privacy | Small — swap to a safe name, plus a backfill decision |
| 2 | Team history and photos fully backfilled to new joiners | Privacy / child safety | Small — `joined_at` already exists, unused |
| 3 | No self-harm / abuse / bullying / escalation handling in Rickie | Child safety | Medium — prompt work plus evaluation |
| 4 | No block / report / moderation anywhere | Child safety | Medium |
| 5 | No age, consent or guardian model | Legal blocker | Large — see §4 |
| 6 | Invite link does nothing for the recipient | Product | Medium |
| 7 | Retention cron not live; backups unverified | Operational | Owner / infrastructure |

1 and 2 are the ones I would fix first regardless of any policy decision: they
are defects under the product's *current* adults-only posture, not only under
a child-safety regime.

---

## 3A. Regulatory position (researched 2026-09-20)

**Confidence note, stated first because it matters.** Statute text, the FTC
rule and platform policies came from primary sources. **Litigation status and
several dates came from secondary sources** (law-firm and trade analyses)
because primary dockets could not be reached — the 9th Circuit AADC outcome,
the Texas SB 2420 stay, and the federal bill tracker all fall in that category
and should be confirmed before you rely on them. Nothing below establishes
compliance. It narrows what to build and what to ask a lawyer.

### The four findings that change the plan

**1. Email-plus consent is not available to StreakFit, so full VPC is
required for under-13s.** The lighter "email plus a confirming step" route
under § 312.5(b)(2) is permitted *only* where the operator does not disclose
children's information to third parties or make it publicly available.
StreakFit does both: Ask Rickie sends the child's free text to Anthropic, and
team chat and photos disclose to other members. That forces an approved
verifiable method — payment with a transaction record, knowledge-based
authentication, government-ID check, video call, or print-and-send. All have
real per-verification cost and heavy drop-off.

**2. The teen band is not the light-touch tier I assumed.** Two laws reach
StreakFit **at any size, with no revenue or user threshold**, and both are
already operative:

- **California SB 243** (companion chatbots, operative 1 Jan 2026) — for any
  *known minor under 18*: disclose that Rickie is AI, a **break reminder every
  three hours**, prevent sexual content, and **maintain and publish a
  self-harm/suicidal-ideation protocol with crisis referral**. Private right of
  action, ≥$1,000 per violation plus fees.
- **New York's AI-companion provisions** (effective 5 Nov 2025) — crisis
  protocol for suicidal ideation and self-harm, plus recurring AI disclosure.

So the Rickie safety gap in §2.4 is not merely a gap in care. It is very
likely a live legal requirement for **every** minor who can reach him, not
only under-13s. That moves it from "should fix" to "must fix before any minor
uses the feature", and it is the strongest argument for doing Stage 1 item 3
first regardless of every other decision.

**3. You are liable for what Anthropic does with a child's text.** The FTC's
*Apitor* action (Sep 2025) held an operator responsible for a third-party
SDK's collection, and its Sep 2025 business guidance says so directly. The
2025 COPPA amendments additionally require a **written children's data
security programme** with a named coordinator and **written assurances from
processors**, a **published retention policy**, and a **separate, unbundled
opt-in for third-party disclosure** — the Anthropic call cannot be bundled
into a general consent.

**4. Most of the state design codes do not reach you — yet.** Maryland,
Nebraska and Connecticut are threshold-gated (revenue or user counts far above
StreakFit today). California's AADC is largely enjoined, with the
age-estimation provision remanded. App-store age-verification laws (Texas,
Utah) bind stores and apps distributed through them — **a web PWA is outside
them today, and listing on the App Store or Play pulls you in**, along with
Apple's Kids Category rules and Google's Families policy. Play's Families
policy independently bars child-targeted apps that enable communication with
unknown persons, which is worth weighing against invite-code teams.

### The architectural consequence

The solo core — daily mission, streak, XP, acorns, Brain Boost, Side Quests —
collects almost nothing: a username, a password hash and progress counters. No
email, no real name, no date of birth, no location, no photographs, no free
text leaving the service.

**Every heavy obligation above attaches to exactly three surfaces:** Ask
Rickie, team chat, and team photos. That is a useful shape. It means a
genuinely complete under-13 experience is reachable at a far lower consent
burden than the product as a whole, and the consent cost can be attached to
the features that actually create it.

---

## 4. Proposed account model

**Status: PROPOSAL. Nothing here is built, and the thresholds are the part
most likely to move once the regulatory briefing lands.** The shape below does
not depend on the exact numbers; the numbers are marked where they matter.

### 4.1 Three bands, one stored field

Store a **band**, not a birth date. The product's strongest existing privacy
property is that it collects no email, no real name, no date of birth and no
location — and a date of birth would be the most sensitive item in the
database. A band is enough to drive every rule below.

| Band | Proposed | What it changes |
|---|---|---|
| `child` | under 13 *(threshold to confirm)* | Verifiable parental consent required before the account can be used at all. Teams and Ask Rickie off until a guardian authorises each, separately. |
| `teen` | 13–17 *(to confirm)* | Account usable. Teams on. Ask Rickie **on by default but guardian-disableable** — or off by default; this is a decision, see §6. |
| `adult` | 18+ | Today's behaviour, unchanged. |

### 4.2 How the band is established without collecting more data

A neutral age screen at sign-up asking for **birth month and year only**
(not day), used once to compute a band and then discarded — the band is
stored, the date is not. Neutral means it must not telegraph the "right"
answer: no "are you over 13?" checkbox, which teaches the answer in the
question.

This is age *assurance*, not age *verification*. It is self-declared and a
determined child will lie. That is expected and is not by itself a failure —
but it is exactly why the escalation in 4.3 matters, and it is a point the
owner should see plainly rather than have buried.

### 4.3 Consent, and what does not count as consent

**A guardian link is a separate object from the child's account**, so that
"who pays" and "who authorises" never become the same thing:

```
GuardianLink(child_user_id, guardian_user_id, established_at,
             method, evidence_ref, scopes[], revoked_at)
```

`scopes` is an allow-list — `account`, `teams`, `coach`, `photos` — granted
individually. That is what makes "a parent can manage the account without
reading every private conversation" expressible rather than a promise.

**Explicitly NOT valid consent**, and each needs a test asserting it:
a checked box; an invite code; a sponsorship payment; a shared household; the
child's own assertion that a parent agreed; an email click with nothing
behind it. The brief says this and it should be enforced in code, not prose.

### 4.4 Parental controls a child cannot reverse

Any control that matters must be **server-enforced and guardian-scoped**:
changing it requires the guardian's own authenticated session, not the
child's. A client-side toggle or a settings row in the child's own app is not
a parental control, and the previous report already said so. Concretely:
the coach gate becomes a server-side check on the same path as the existing
503-when-no-key behaviour, so the fail-closed route is already built and
tested.

### 4.5 Ageing out, and lost guardian access

- A band is stored with the month/year it was derived from, so the account can
  **transition on a birthday** rather than being frozen at its sign-up band.
  Transition must be an explicit, logged event, not a silent widening of
  permissions.
- **Losing guardian access must not orphan a child's account.** A recovery
  path is needed and it is a genuine abuse surface — it is how an unauthorised
  adult would try to take over a child's account. Flagged in §7 rather than
  designed here.

---

## 5. Implementation sequence

Ordered by dependency. Items 1–3 need no policy decision and I would start
there.

**Stage 1 — defects that are defects today (no decision needed)**
1. Stop serialising `User.username` to peers. One helper replacing
   `_usernames_for_ids` at four call sites — roster, moments, challenges,
   message sender. *Acceptance: a teammate never receives another account's
   login identifier, asserted per endpoint; existing team history is covered
   too, not just new rows.*
2. Filter team content by `TeamMembership.joined_at`. *Acceptance: a member
   who joins at T cannot read messages, photos or moments created before T.*
3. **Rickie safety escalation — now the highest-priority item of the three.**
   Self-harm, suicidal ideation, abuse, bullying, emergencies, and "tell a
   grown-up you trust" phrased for a nine-year-old. Per §3A this is very likely
   a live legal requirement for every minor who can reach him, not only
   under-13s, so it is needed under *every* launch option including
   adults-plus-teens. *Acceptance: a scripted evaluation over disclosure
   prompts, scored by an independent reviewer, with MUST-ESCALATE and
   MUST-NOT-DEFLECT cases — the same harness already used for the
   medical-caveat calibration. Plus the written protocol published, which the
   statutes require separately from the behaviour.*

**Stage 1b — the other statutory duties on Rickie (needed for any minor)**
3b. AI disclosure where a user could reasonably think Rickie is human; a break
   reminder on long sessions; and confirmation that sexual content is
   prevented. *Acceptance: disclosure visible without hunting for it; the
   reminder fires on a long session and is tested; the content rule is an
   assertion, not an assumption.*

**Stage 2 — needs the age model (blocked on §6 decisions)**
4. `age_band` on `User` + migration + neutral age screen.
5. `GuardianLink` with scopes, and server-side enforcement of the coach and
   teams gates.
6. Verifiable parental consent — an approved § 312.5(b)(2) method, plus a
   **separate unbundled opt-in** for the Anthropic disclosure, and a
   **parental review and deletion** path covering transcripts and photos
   (§ 312.6). Only needed if you choose launch option (c).
6b. Written children's data security programme, named coordinator, processor
   assurances, and a published retention policy — paperwork, but required
   paperwork, and it is owner-side rather than code.

**Stage 3 — needs Stage 2**
7. Block / report / moderation. *Acceptance: a reported item is removed from
   the reporter's view immediately and queued; a blocked member cannot send to
   or see the blocker.*
8. Age-appropriate team rules — whether a child may join an adult-created
   team, and under what authorisation. **Decision, §6.**
9. Invite-link flow done properly: an invite landing screen that handles
   logged-out recipients, expiry, revocation, and age-appropriate consent.

**Stage 4 — content and operations, in parallel throughout**
10. Work the 311 `revise` items back through independent review.
11. Retention cron and backup/restore verification — owner-side.

---

## 6. Decisions I need from you

1. **Age thresholds — now largely settled by the research, so this is a
   confirmation rather than an open question.** 13 is the COPPA boundary and 18
   is the boundary for SB 243 and the New York AI-companion rules. I propose
   `child` under 13, `teen` 13–17, `adult` 18+ unless you want different
   product behaviour at some other age.
2. **Ask Rickie for teens.** On by default with guardian ability to disable,
   or off until a guardian enables it? This is the single biggest usability
   trade-off in the whole design.
3. **Ask Rickie for under-13s at all.** My recommendation is **no at launch** —
   free text from a child to a third-party model is the highest-risk surface
   in the product and the one hardest to make safe and to prove safe.
4. **Mixed-age teams.** May a child join a team created by an adult who is not
   their guardian? My recommendation: only with explicit per-team guardian
   authorisation.
5. **Parental access to conversations.** ~~I first recommended that a guardian
   should *not* get a reading feed of the child's conversations.~~ **That was
   wrong for under-13s and I am correcting it.** COPPA § 312.6 gives a parent
   the right to *review* the personal information collected from their child on
   request — which includes Ask Rickie transcripts and team photos. For an
   under-13 account this is a requirement, not a values choice, and it has to
   be built.

   The values question survives only for **13–17**, where no equivalent federal
   right applies. There my recommendation stands: a guardian can see *that*
   Rickie was used, turn it off, and export or delete — but does not get a live
   reading feed of a teenager's conversations. Your call.
6. **The username exposure backfill.** Existing team history contains
   usernames. Replace them retroactively, or leave history as written?
7. **Launch posture.** The research makes three options concrete, and they
   differ enormously in cost:

   **(a) Adults + teens first.** Needs the Rickie crisis protocol, AI
   disclosure and break reminders (SB 243 / New York), and no age screen beyond
   a neutral one. No verifiable parental consent machinery at all. This is
   weeks, not months.

   **(b) Add under-13 in "solo mode".** Ask Rickie, team chat and team photos
   are off for that band. Because those three surfaces are what create the
   disclosure, the consent burden drops sharply and the child still gets the
   whole movement product. **This is what I would recommend**, and it is the
   reason §3A's architectural point matters.

   **(c) Full under-13 parity**, with teams and Rickie. Requires full
   verifiable parental consent with an approved method, separate unbundled
   consent for the Anthropic disclosure, a written security programme with
   vendor assurances, a published retention policy, parental review and
   deletion, and moderation. Months, with per-verification cost and heavy
   sign-up drop-off.

---

## 7. Needs legal, privacy or platform review

I can implement to a standard but cannot certify compliance, and passing tests
will not establish it.

- Whether the intended design constitutes obtaining **verifiable** parental
  consent to the required standard, and which approved method to use.
- Whether sending a child's free text to a third-party model is permissible
  under the applicable regime, and on what basis.
- The **guardian-recovery** path — an abuse surface that deserves review
  before it is designed, not after.
- Whether StreakFit is "directed to children" once it markets to them, which
  changes the obligations regardless of the age screen.
- Data-retention and deletion obligations specific to minors, which may be
  shorter than the 30 days currently used.
- App-store age-rating and policy obligations if it ever ships through a store.
