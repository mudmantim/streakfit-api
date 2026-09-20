# Product completion phase — what was done, and what is not done

**Date:** 2026-09-20 · **Branch:** `product-completion` (not pushed, not deployed)

This covers the seven areas in the brief. It is written to be checked rather
than believed: every number below comes from a command in this repo that can be
re-run, and where something is unknown it says so instead of rounding up.

**Nothing in this phase was deployed.** No push, no production change, no live
payments, no invitation to a tester, and no paid AI calls beyond the caching
probe already reported ($0.12, behind an enforced ceiling).

---

## The short version

Three findings matter more than the rest, and none of them was on the list I
started with.

1. **A privacy failure in teams.** Once everybody left a team, its invite code
   still worked — a stranger holding it could read the family's chat and
   download photographs of their children, while the family themselves could
   not. Found by an independent walkthrough, reproduced, fixed, and pinned with
   six tests.
2. **Two live safety hazards in the content library**, both telling readers to
   balance with their eyes closed, in an app used by nine-year-olds and by
   seniors. The library was serving 526 items marked "accepted" that nobody had
   ever fact-checked.
3. **The app was claiming things it could not do** — a "favourite move" invented
   from one day's data, a guest banner promising to save a streak that was never
   stored, an acorn balance showing lifetime earnings.

All three are fixed. **Still not ready for Olivia**, and the blockers are named
in full at the end.

---

## 1. The individual acorn experience

Three defects in one loop, all found by driving it rather than reading it.

| | |
|---|---|
| **The balance was wrong** | `/api/me` never sent `acorns_spent`. The client works out what is spendable as `earned - spent`, and a missing field reads exactly like zero spent — so the Progress tab showed **lifetime earnings as though they were a balance**. Buy a 20-acorn filter and the composer said "10 left" while Progress still said 30, through a full reload. |
| **One tap spent them** | A priced filter chip was indistinguishable from a free one. An independent reviewer spent two thirds of everything they had earned by tapping a chip to see what it looked like. Acorns take days to earn and nothing refunds them. Now the first tap quotes the price and the balance; the second spends. |
| **The explainer named the wrong path** | It said acorns buy "photo filters for team photos" — telling a solo user their acorns were useless two lines above the button that spends them. |

Why 14 passing assertions missed the first one: the fixture had `acorns_spent = 0`,
so earned and available were equal by coincidence. The check now spends first.

**Loop verified end to end in a browser:** earn → see balance → open composer →
buy a filter → balance drops everywhere → picture is made. 14 assertions.

---

## 2. The complete individual experience

An independent reviewer walked the app as a new user. Every confirmed finding
was fixed, not filed.

**Things the app was claiming that were not true**

- **The Memory Book invented a pattern.** It took the single most-completed
  exercise with no minimum and no margin, so after one mission — where every
  move is tied at one — it picked an arbitrary row and announced *"Your
  favorite move seems to be Wall Sit. Rickie's noticed."* The reviewer called
  this the moment the companion stopped reading as honest, and that is the
  right severity: an observation derived from no observation makes every other
  number on the page suspect. It now needs a minimum tally **and** a margin over
  the runner-up, and says "done most often" rather than "favourite" — the app
  chooses the five daily moves, so the user never expressed a preference.
- **Guest mode promised to save a streak.** Guest progress is a `Set` in
  memory, cleared on entry and exit, and there was no sign-up control anywhere
  in guest mode to act on the offer. Both halves false at once. The copy now
  says what is true and the button exists.
- **The landing page said "Five *tiny* moves a day."** Measured against what
  the app prescribes, the heaviest beginner day of a year is about 330
  repetitions. The claim is now what is actually true: five moves, no
  equipment, and it ends. **Whether the volume itself should be gentler on a
  beginner's first days is a prescription question for you** — see Open
  decisions.
- **"Custom" difficulty** was accepted by the API and implemented nowhere; the
  mission header said "Custom" while the mission fell back to beginner.

**Things a person could not do, or could not read**

- **Side Quests were write-once.** No rename, no delete, and no routes to build
  either on. A typo in a habit you see daily was permanent, and a habit you had
  stopped doing sat in the list forever with "Not started" beside it. Both now
  exist, scoped to the caller in the query, with removal behind a second
  deliberate tap.
- **"107 XP to next level"** and nothing else — the one screen built to show
  progress never stated the current figure. It now reads `13 / 120 XP · 107 to
  Level 2`.
- **Three currencies, no glossary.** A reader met a streak, XP and acorns in
  the first two minutes with nothing saying which mattered, which was spendable
  or which could be taken away.
- **Milestones were unreachable.** After day one the next was 100 exercises —
  twenty days away — so the page was a column of locks a new user could not
  move. Six early ones added, ordered by how soon they arrive.
- **The nav bar floated mid-screen** on any short pane, reading as a broken
  layout rather than as navigation.
- **"Ask Rickie" opened a blank box.** No greeting, nothing about what he is
  for. Here a wrong guess costs a real API call and returns a deflection, which
  teaches somebody he is not much use before he has had a chance to be. He now
  says one line and offers three things to tap, entirely locally.
- **Error messages spoke to a developer**: *"display_name can't be an email
  address"*, in front of a nine-year-old. All rewritten, with a test that walks
  every 400 path and refuses any raw field name or snake_case identifier.

**Found while writing that test:** `visit streakfit.example.com` was accepted as
a display name. The unsafe-name check knew `https://` and `www.` but not a bare
domain — and a display name is shown on the team roster and spoken aloud by
Rickie, which makes it a broadcast channel the moment it can hold a URL.

---

## 3. The optional team experience

Verified by 19 assertions in `uicheck` that drive the real UI — creating a
team, joining with a code, the roster, the campfire, sharing a photo with a
filter, and the other member seeing it — and by **53 team-specific checks** in
`scripts/verify_all.py` (teams, campfire, moments, chat, photos), including the
non-member access boundary.

What was already true and stayed true:

- **The roster never lists who has NOT moved.** There is no leaderboard, no
  ranking and no "behind" state. A test holds this.
- **The Campfire is cumulative and never resets.** It cannot go backwards, so
  a missed day cannot take anything away from anybody.
- **Teams are genuinely optional.** A solo user gets no standing Team tab —
  asking for one is what reveals it. The one quiet way in sits at the foot of
  Progress.
- **Photos are fetched through an authorized request, not a public URL**, EXIF
  including GPS is stripped, and the composer says plainly that anyone who can
  see a photo can screenshot it.
- **Ordinary teammate chat consumes no AI credits.** This is a test, not a
  promise.

Changed this phase: the free-plan team-count cap was removed, so unlimited
teams on every plan is now true of the code and not only of the pitch.

### What the independent team walkthrough found

It went through as a family — created a team, joined with a code, shared
photographs of a child, chatted, ran a challenge, and left — and found one
serious privacy failure plus a long tail.

**Fixed: an abandoned team's invite code was still a key.** After everybody
left, the code still resolved. A stranger holding it could join the empty
team and download both photographs in full, read the entire chat, and read
the team's history with everyone's usernames attached — while the two people
who *had* been in the family got 403 on those same photographs, because view
and delete are gated on current membership. And it could never be undone by
anyone, because Rotate Code requires membership too.

It also made the best sentence in the product false. The composer says "Only
your team can open this." With nobody in the team, "your team" silently
became "whoever still has six characters in an old text message."

A team with no members now refuses lookup and refuses join — both, because
`/join` takes a team id in the path and never has to ask lookup anything.
Lookup returns the same 404 as an unknown code, so a prober cannot learn that
a code was once real. Six tests, including the two that stop the fix going too
far: a team down to one member still works, and the creator leaving does not
close a team other people are in.

**Fixed: Leave Team took one tap and said nothing.** Out of the team in 200ms,
the tab gone from the nav, no confirmation before and no acknowledgement
after. This codebase already asks twice for removing a member, deleting a
photo, and spending acorns. It now asks twice for this too, and the note says
what somebody hesitating is afraid of — your streak, XP and acorns are
untouched.

### Not fixed, and why

These are real, and I stopped rather than widening the phase:

| Finding | Why it is still open |
|---|---|
| **"Copy Link" produces a link that does nothing.** The recipient lands on their ordinary Today screen; the code is captured into a form inside a hidden pane behind a hidden tab, and `replaceState` wipes it from the URL on the first tick. | This is a feature — an invite landing screen — not a bug fix. It is the single biggest barrier to a parent actually getting a child into a team, and it deserves designing rather than patching. |
| **Invite codes contain every confusable pair** (`U0HIUD`, `LXCI1S`). Since the link is broken, reading the code aloud *is* the path. | One-line fix, but it changes issued codes. Worth doing with the link fix. |
| **Nothing explains what the Campfire is.** "0 logs on the fire, 100 more to reach Small Flame" — 50 days for a family of two, shown as a bar at 2%. | Copy plus possibly a threshold change. A product decision. |
| **A targeted challenge cannot be declined.** One button, "I did it". The only place one person can place an obligation on another. | Needs a designed answer, not a dismiss button bolted on. |
| **Challenge rewards silently stop after 3/day** but still celebrate. | Needs a decision on what should happen instead. |
| **No photo gallery**; team history truncates at 8 with no "show more"; three moment types render as a bare bullet. | Straightforward but outside this phase. |
| **Invite codes use `random`, not `secrets`.** | The rate limiter is doing the real work (12/min, 60/hr against a measured 321 probes/sec oracle), but generation should not be the weak half. |

**A harness finding worth acting on:** `scripts/uicheck.py`'s `Browser` hardcodes
`--remote-debugging-port=9333` and a shared profile, so two agents running it
at once silently attach to each other's Chrome. The walkthrough lost several
runs to this before noticing, and it also cost several of my own `uicheck` runs
today. It needs a port override.

### What the walkthrough confirmed was right

Worth recording, because it was tested adversarially rather than assumed:

- **The roster is honest under pressure.** With one member who had done
  nothing, the panel gave her **no status line at all** — not "missed", not a
  grey dash, not a zero. Order is creator-first then join order, never by
  performance. No ranking anywhere.
- **Every aggregate line is positive or neutral and names nobody.**
- **Nothing earned disappears on leaving** — streak, level, acorns and best
  streak all verified intact afterwards.
- **The Campfire only goes up.** A contributing member left and the total
  stayed put. There is exactly one mutation site and it is `+= 1`.
- **Error copy is good throughout**: "This team is at its 8-member limit",
  "Already a member of this team", and lowercase invite codes work.

---

## 5. Brain Boost content quality

**This is where the serious findings were.**

The library was serving 526 items marked `accepted` at review depth `read`.
Nobody had checked a single claim against anything. The depth was recorded
honestly — they *had* been read — but `read` was being served as though it
meant verified.

Fifteen independent reviewers re-read every served item in slices. None saw
another's verdicts, and none was told the items were already live: `stage` is
stripped from the packets precisely so a re-reviewer cannot reproduce a prior
decision.

### What they voted

| Lane | Reviewed | Accept | Revise | Reject |
|---|---|---|---|---|
| high — facts, movement, trivia, experiments | 498 | 71% | 27% | 2% |
| low — jokes, riddles, Rickie asides | 95 | 39% | 26% | 35% |

**593 items re-reviewed. Store now: 440 accepted, 311 revise, 47 rejected.**
424 of the accepted are at `sourced` depth; the other 16 are jokes, where
there is nothing to source. Nothing was deleted — every demoted item is on
disk with the reason, in `content/reviews/*.jsonl`, because that reason is how
the next batch avoids the same mistake.

### Two safety hazards, both live until today

- **SF-FCT-000045** invited the reader to close their eyes while standing to
  feel how much harder balancing gets. The fall *is* the demonstration, no
  warning, in an app whose audience includes nine-year-olds and seniors.
- **SF-TRV-000170** went further: it *recommended* eyes-closed single-leg
  balance as "such a good way to make the exercise harder without moving
  anything." Two reviewers found the first; the widened safety gate found the
  second, which nobody had flagged.

The gate that should have caught both required the phrase "eyes closed **while
walking or running**" — narrower than the rule it stood for. It now covers
balancing and standing, matches "closing your eyes" as well as "eyes closed",
and applies to trivia **distractors**, because a wrong option is still read.

### Wrong answers in the answer key

- **SF-TRV-000154** — all four options are correct. Cornea, lens, tooth enamel
  and epidermis are all avascular. The distractors had never been checked for
  truth, only the key.
- **Two items** awarded "children learn by watching" over "being encouraged",
  where the evidence favours the option marked wrong (modelling r≈.16,
  support r≈.38).
- **SF-TRV-000082/94** key the superseded damage-and-repair model of getting
  stronger.

### The pattern underneath, and the gate now enforcing it

Four reviewers reached the same conclusion without conferring: almost nothing
in the pool is outright false. The defects are a **true core wrapped in a
quantity nobody measured** — and all of them were tagged `simplified` with
empty sources, which passed validation because the schema only required a
source at `established`. `simplified` had become a sourcing exemption: the
label a claim wears in order not to be checked.

An unsourced comparative, proportion, percentage or dose is now an **error**,
not a warning, whatever the confidence tier says. Hedged language is
deliberately untouched — "tends to", "can", "often" is the honest version, and
penalising it would push authors back toward firmness.

A second, softer gate warns on a mechanism bolted onto a true claim
("...which is also why..."), which three reviewers named independently. That
one stays a warning because whether a causal link is supported is a judgement
no regex can make.

### What this cost, stated plainly

The served pool fell from 693 to 440. Measured against what a person actually
meets each day:

| Slot | Pool now | Days before it repeats |
|---|---|---|
| Today's Insight (facts, movement, riddles, experiments, Rickie asides) | 224 | 224 |
| Brain Boost (trivia) | 200 | 200 |
| Rickie's jokes (only when somebody asks him for one, in chat) | **16** | — |

**The jokes are the thin part, and only the jokes.** They went 60 → 16,
because a third of them were not jokes at all — a setup followed by a bland
positive statement with no wordplay or twist. They are not the daily reward
slot; they are what Rickie reaches for when asked for a joke in conversation,
so 16 is survivable rather than broken. It still needs authoring rather than a
lower bar: a flat joke told on request is worse than Rickie saying he hasn't
got a good one.

The two daily slots are in decent shape — seven months of Insight and six and
a half of Brain Boost before either repeats for a given person.

### The 5,000-item goal

**Not close, and further away than before this phase.** 440 accepted against a
5,000 target. The honest read is that the goal was being approached by
counting items rather than by checking them, and the measured accept rate on
independently reviewed content is 61–71% for facts and under 40% for humour.
Reaching 5,000 at this standard means authoring roughly 7,000.

---

## 4. Rickie's AI usage architecture

Unchanged this phase and still as reported: `docs/product/ai-usage-and-credits.md`.
The measured figure is **$0.0099 per typical reply**, $0.0199 worst case,
$0.0149 as a planning number, over 82 real replies. Prompt caching was measured
against realistic low-traffic patterns rather than assumed, and is implemented
behind a **default-off** flag.

The finding that matters for pricing has not changed: **the system prompt is
the cost**, at 63% of every request, and it grew 62% in a month as safety and
feature rules were added. Every rule added to Rickie is billed on every reply
every user ever sends.

**15 Free / 150 Plus remain provisional.** No payments code exists, no price is
settled, and ordinary teammate chat consumes no AI credits — that last one is a
test, not a promise.

---

## 6. Privacy, safety and operational readiness

Honest status, unchanged where nothing changed:

| Item | Status |
|---|---|
| Conversation retention (~10 turns, 30 days) | Swept independently of user traffic; `RetentionRun` records every sweep and a self-check fails if none has run in 48h |
| **Render Cron Job** | **NOT LIVE.** Declared in `render.yaml`, which the dashboard-configured service never reads. Marked INERT in the file itself. |
| **Database backup retention** | **UNKNOWN.** Not confirmed against the provider, no restore tested. Deletion does not reach backups and the export says so. |
| Photo EXIF/GPS stripping | Verified — the file is rebuilt segment by segment (10 tests) |
| Coach Notes | Closed vocabulary of canonical tokens; never the words a user typed |
| Account deletion | Real and caller-scoped |
| **Minors and consent** | **UNDESIGNED.** No age gate, no parental consent mechanism, no per-account way to turn Ask Rickie off. See `docs/operations/minors-and-consent.md`. |

**No compliance claim is made.** Passing tests establish that specific
behaviours are what the code says they are. They do not establish COPPA, GDPR
or any other regime, and no assessment has been done.

---

## 7. Verification

| Check | Result |
|---|---|
| `make check` (lint + types + build + pytest) | **560 passed**, 0 failed |
| `make uicheck` (real UI, headless Chrome, 390×844) | **156 checks, 0 problems** |
| `scripts/verify_all.py` (end-to-end, production-safe) | **108 passed, 0 failed** |
| Content validation | **0 errors** on served items |

Independent agents did the reviewing wherever a judgement was involved: the
solo-user walkthrough, the team walkthrough, and every content packet. None of
the content reviewers saw another's verdicts, and none was told the items were
already live.

### Two of my own checks were wrong, and both mattered

- **A "fix" that made a real bug invisible.** The step-aside check failed about
  one run in four, and I had earlier made it wait for Rickie to stand still
  before planting content. That made it pass by arranging for it never to meet
  the failing case. The flakiness was the finding: content arriving *during* a
  walk left him standing on it afterwards, because nothing re-ran the check when
  the walk ended. Fixed in `rickie-roam.js`, and the check now plants content
  whenever it likes.
- **A guard that tested a proxy.** `test_nothing_earned_is_lost_by_being_away`
  guarded the design rule with an allow-list of metric *names*, which failed the
  moment correct new milestones arrived. It now builds the same history at two
  different absences and compares. Getting that right took two attempts —
  the first version compared two users who were *both* away, so it passed no
  matter what. Verified by mutation.

---

## Open decisions for you

1. **Beginner day-one volume.** ~330 reps on the heaviest beginner day. The
   landing copy no longer overclaims, but the prescription is a design call and
   I did not change it.
2. **Backup retention**, and a tested restore. Until then "deleted" cannot be
   qualified for anyone, child or adult.
3. **Create the Render Cron Job**, or accept that retention sweeps only while
   the web service is up.
4. **Minors**: whether Ask Rickie should be switchable off per account. The
   coach already fails closed to a friendly 503 with no key, so a per-account
   flag reuses that exact path. It is the single cheapest change that would most
   reduce the surface for a child account.
5. **Rickie's joke library is down to 16.** A third of the originals were not
   jokes. These are only served when somebody asks him for one, so it is not
   urgent — but 16 is few enough that a chatty user will see a repeat. Worth
   commissioning authoring against the standard the reviewers applied rather
   than restoring the rejected ones.
6. **Whether `simplified` should mean anything.** Several reviewers noticed the
   same thing: items that are plainly editorial opinion, and items that are
   genuinely contested, are both labelled `simplified` because it is the tier
   that needs no source. The measured-claims gate bites the worst of it, but
   the label itself is doing no work.

---

## What would stop me calling this ready

Two things, and neither is cosmetic.

**The content pool.** Every served item has now been independently reviewed,
which is a real change of state — but 311 items sit at `revise` waiting for
somebody to fix them, and several of those are items a reviewer called good
with one clause wrong. That is a day of editing, not a rewrite, and until it
happens the library is a third smaller than it reads.

**The operational unknowns.** The retention cron is not live, and backup
retention is unverified. Both are marked as such everywhere they appear, and
neither can be resolved from inside this repo.

**The team invite link.** A parent taps "Copy Link", texts it to their child,
and the child lands on an ordinary Today screen with no mention of an
invitation. The only working route in is for the child to independently find a
quiet row at the foot of Progress. Teams are optional, so this does not block
solo use — but "invite Olivia to a team" is a thing that currently does not
work as designed, and it is a feature to build rather than a bug to patch.

Everything else in the brief is done: the acorn loop works end to end, the
individual experience no longer claims things it cannot do, teams are honest
and stay optional, and the AI cost architecture is measured rather than
guessed.

---

## How to check any of this

    make check                                   # 553 tests, lint, types, build
    make uicheck                                 # 153 browser checks at 390x844
    python scripts/verify_all.py --base-url http://localhost:5000
    python scripts/content/validate.py           # content gates
    python scripts/content/review_queue.py --batch 0001 --status
    python scripts/rickie_cost_audit.py          # makes no API calls

The review ledgers are `content/reviews/0001.jsonl` and `0002.jsonl` — every
verdict, who made it, and why.
