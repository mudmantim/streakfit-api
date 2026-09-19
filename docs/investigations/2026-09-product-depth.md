# Product-depth investigation — September 2026

Evidence gathered before implementing the next structural changes. Four areas:
the exercise system over time, information quality, information architecture,
and Ask Rickie. Findings only — nothing here was acted on except one regression
fix (side-quest creation) that the investigation uncovered.

The content-quality audit lives beside this file in `2026-09-content-audit.md`.

Verification notes: every figure in Areas 1, 3 and 4 was reproduced directly
(simulations against the real selection function, heights measured in a browser
at 390px, a live probe of `/api/coach`). Two claims from the content audit were
re-checked and one was corrected — British spellings appear in Brain Boost (15)
but not in the insights (0), so the split is confined to the quiz content.

---

# AREA 1 — Exercise system over time (EVIDENCE)

Simulated with the real `get_daily_exercises()`, 40 users x 30 and 60 days, all three tiers.

## Observed

| metric | beginner | intermediate | advanced |
|---|---|---|---|
| distinct exercises seen in 30d | 28.9 / 30 | 29.3 / 30 | 29.8 / 30 |
| distinct in 60d | 29.7 | 29.9 | 30.0 |
| times each exercise recurs in 60d | 10.1 | 10.0 | 10.0 |
| median gap between repeats | 3-4 days | 3 days | 3-4 days |
| daily slots repeating YESTERDAY | 18.2% | 20.0% | 17.2% |
| avg high-impact exercises/day | 0.30 | 1.12 | 1.39 |
| back-to-back heavy days | 0.0% | 1.5% | **20.3%** |
| duplicate within one day | 0 / 3600 days (structurally impossible) | | |

## Findings

**1.1 Variety is adequate; rotation is not the problem.** ~30 distinct exercises in a
month, median 3-4 days between repeats. Whatever is wrong here, it is not "too samey".

**1.2 One exercise in five repeats yesterday.** 17-20% of daily slots. Not fatal, but it
is the most visible staleness signal and there is no logic preventing it.

**1.3 No recovery model at all.** Constraints are evaluated per-day only (>=1 high fun,
>=1 low/high impact, <=2 high impact) with zero memory of yesterday. Advanced users get
two consecutive heavy days **20.3% of the time** — one week in five.

**1.4 Zero progression.** `get_daily_exercises(user_id, date_str, skill_level)` — no
streak, no history, no completion count, no level. Verified: the apparent "xp"/"level"
matches in the source were false positives ("explosive", "skill_level").
Reps are FIXED strings forever: day 1 and day 365 both read "3 sets of 12 reps".

**1.5 Tier change is a cliff, not a ramp.** Zero key overlap between tiers — switching
replaces **100% of the pool at once**. Nothing in the app ever suggests switching, or
indicates readiness.

**1.6 Tier-hopping inflates XP (verified live).** Because `new_exercise` is once-ever per
key and the pools are disjoint, switching tier mid-day re-opens discovery XP:
beginner 140 XP -> +intermediate 100 -> +advanced 100 = **340 XP on day one** (2.4x),
reaching level 3. Up to 1800 XP of discovery is farmable this way. Streak and mission
count stay honest at 1, so only XP/level are corrupted.

**1.7 Dead metadata.** Every exercise carries `equipment`, `space`, `family_friendly`,
`requires_structure` — consumed by nothing. These were clearly designed for filtering
("small space", "no equipment") that was never built.

**1.8 `custom` skill level silently falls back to beginner.** The UI offers it as a
disabled option; the API accepts it and returns beginner exercises.

**1.9 Prescribed volume is not modelled.** Median reps+seconds/day: beginner 249,
intermediate 295, advanced 166. Not comparable across movement types — which is the
point: there is no load model, so "difficulty" means only "a different list".
# AREA 3 — Information architecture (EVIDENCE)

## Measured section heights at 390px (real browser, four personas)

| persona | today | mission | journey | teams | quests | install | prompts | TOTAL |
|---|---|---|---|---|---|---|---|---|
| first day | 88 | **1419** | 416 | 385 | 381 | 113 | 124 | **3248** |
| solo (11-day) | 88 | **1110** | 416 | 367 | 290 | 113 | 124 | **2830** |
| teen on a team | 88 | **1110** | 416 | 536 | 381 | 113 | 124 | **3089** |
| back after a break | 82 | 545 | 416 | 367 | 381 | 113 | 124 | **2349** |

**The mission card alone is 45-55% of the page.** Everything else combined is smaller
than it.

## 3.1 Tabs alone do NOT solve the problem

Modelling a Today / Progress / Team split against the measured heights:

| persona | TODAY | PROGRESS | TEAM |
|---|---|---|---|
| first day | 1744px (**2.1 screens**) | 797px (0.9) | 385px (0.5) |
| solo | 1435px (**1.7 screens**) | 706px (0.8) | 367px (0.4) |
| teen | 1435px (**1.7 screens**) | 797px (0.9) | 536px (0.6) |
| returning | 864px (1.0) | 797px (0.9) | 367px (0.4) |

A 3-pane split cuts the page 45-55%, but Today is still ~2 screens on day one. The
exercise row itself (~110px x 5: 64px thumbnail, name, reps, two chips, category pill,
button) is the remaining half of the problem. **Navigation and row density are two
separate fixes and only doing one leaves Today at two screens.**

## 3.2 Unmounting sections would break real things (from the code inventory)

- **The coach panel is an in-flow `<section>`**, mounted with
  `document.querySelector('.side-quests-section').parentNode.insertBefore(...)`
  (app.js:6631-6632, unguarded). If Side Quests moves to another pane, opening the
  coach throws and it becomes permanently unopenable.
- `renderJourneyCard()` early-returns when `#journey-card` is absent, and `hidden=false`
  at app.js:273 is the ONLY place it becomes visible — unmount it once and it never
  comes back.
- `loadDailyExercises()` writes to **five** regions in one pass: the settings selects,
  the today strip, the mission card, the Journey card, and the Team Rickie card.
- `_applyRickieExpression()` broadcasts via one `querySelectorAll` across Journey,
  Teams, the mission card, the toast, the coach panel and the Memory Book.
- Nothing re-fetches on visibility change — no `visibilitychange`, no polling.
- `hidden` is already the visibility mechanism for `#side-quests-section` (setGuestUI),
  `#journey-card` and `#install-card`; a tab router using `hidden` would fight them.

**Therefore: keep every section mounted and toggle panes with CSS.** The problem to
solve is scroll length, not DOM size. This avoids every breakage above at no cost.

## 3.3 No routing exists at all
`showView()` (2 states) and `showTab()` (auth forms) are the entire router. `?join=CODE`
is read once and erased. No hash, no pushState, no popstate — so Android back exits the
PWA rather than closing the team panel, and no surface is addressable.

## 3.4 Proposed smallest coherent model

**Three panes, CSS-toggled, everything stays mounted:**
- **Today** — today strip, mission, insight/brain boost, coach entry
- **Progress** — Journey (level, week strip, acorns), Memory Book, Make a picture
- **Team** — teams list; **shown only when the user has a team**, otherwise a single
  "add people" row at the foot of Progress

Team being conditional is what keeps solo first-class: a permanent third tab labelled
Team is a standing nudge at someone who has chosen not to use it.

Prerequisites before implementing: guard the coach panel's mount point, and move the
install card + notification ask out of the flow (237px of permanent furniture on every
persona).

## 3.5 Candidates to simplify, demote or remove

- **Side Quests (290-381px on every screen)** — a second habit tracker with its own
  streak, awarding no XP, acorns, levels or milestones. It duplicates the core loop with
  none of its rewards. **It was broken for an unknown period and neither tests nor
  anyone noticed** — the strongest available evidence that it is not load-bearing.
  Recommend demoting to the Progress pane at minimum.
- **Install card + notification ask (237px)** — one-time asks living as permanent page
  furniture.
- **`custom` skill level** — a disabled `<option>` the API silently maps to beginner.
- **`#daily-streak-badge`** (index.html:181) — dead markup, unconditionally hidden.
- **Team Rickie card** for solo users — says "0 days with Rickie" and only opens the
  coach; the coach already has a button on the mission card.
# AREA 4 — Ask Rickie (EVIDENCE)

## 4.0 THE BLOCKER: Rickie has never been observed

`ANTHROPIC_API_KEY` in `.env` is **empty (length 0)**. Live probe of `/api/coach`:

    status 503 {'error': 'coach_unavailable'}

The "Ask Rickie" button is rendered unconditionally (`index.html:212`), so every tap
today produces *"Rickie stepped away from his burrow for a bit"*. 1,385 words of
carefully written character prompt have **never produced a single observed reply** in
this workspace. Whether production has a key is unknown to me and needs confirming.

Real API tests are therefore impossible until a key exists. `scripts/coach_eval.py`
(46 prompts, 10 categories) is built and validated and refuses to run without one.

## 4.1 Prompt coverage vs. risk categories (static audit)

| risk category | explicit coverage? |
|---|---|
| ordinary fitness questions | partial — see 4.2 |
| StreakFit product questions | **yes**, with facts and a format |
| motivation / discouragement | **yes**, unusually well |
| medical / injury | **yes**, explicit and in-character |
| teen chat, jokes, nonsense | **yes** |
| **exercise misconceptions** | **NO — gap** |
| **weight / body image** | **NO — gap.** The word "weight" appears nowhere. The only adjacent line is "never ... imply medical, training, or nutrition expertise" |
| **inappropriate requests** | **NO — gap.** Relies entirely on base-model safety |
| **prompt injection / jailbreak** | **NO — gap.** The one "reveal" hit is "Reveal the character through how you treat people" |
| self-critical user language | partial — covers *discouraged*, not "I'm lazy" / "I hate how I look" |

## 4.2 The training-advice contradiction

The prompt forbids implying "training expertise", yet the app itself ships detailed
form instruction (What / How / Why / Common mistakes / Beginner tip per exercise) and
users will reasonably ask Rickie the same questions. There is no guidance on where
"helpful exercise information" ends and "training advice" begins, so behaviour on the
most ordinary question type is undefined.

## 4.3 SEVERE: Coach Notes will capture and re-inject body-image statements

`_coach_note_extract()` regex-matches the user's own words and stores whatever follows
a trigger phrase. **Verified live:**

| the child types | what StreakFit stores |
|---|---|
| "my goal is to lose 10 pounds before summer" | goals: *lose 10 pounds before summer* |
| "my goal is to get abs" | goals: *get abs* |
| "just so you know, I think I am fat" | notes: *I think I am fat* |
| "I prefer not eating lunch" | preferences: *not eating lunch* |
| "remember that my mum says I eat too much" | notes: *my mum says I eat too much* |

`_load_coach_note_block()` then injects these into **every future conversation** under:
*"What you quietly know about this user (background only — weave in naturally when it
helps...)"*.

So the product would instruct Rickie to weave in naturally that a child thinks they are
fat and prefers skipping lunch — permanently, across sessions. There is **no filter of
any kind** on what gets stored.

The content library has a hard no-body-talk rule enforced by tests. The memory system
has nothing. Mitigations that do exist: "Forget our conversations" deletes turns and
notes, and `GET /api/me/data` exposes what is stored — but a child will not look.

## 4.4 Non-model behaviour (testable today, and correct)

- rate limits 10/day + 3/min, keyed per user — verified present
- 503 degrades gracefully; the UI shows an in-character message, not an error
- server computes all numbers; the model is told never to calculate
- last 10 turns reloaded server-side; client-supplied history ignored
- 500-char message cap, context type allow-listed

---

# AREA 2 — Information quality (headline, verified)

Full detail in `2026-09-content-audit.md`. The claims below were reproduced
independently before being recorded.

**2.1 The correct answer is the LONGEST option in 143 of 190 questions (75%).**
Verified on the presented (shuffled) form. This is the exact structural twin of
the position bias already fixed — "always pick the second one" used to score
75%; "always pick the longest" scores 75% now. Longest by more than 6 characters
in 112/190. Cause: correct answers are written as real explanations while
distractors are 2-4 word dismissals. Shuffling positions did nothing about
length.

**2.2 Seven factual problems, all verified present verbatim.** Worst first:
- *"A habit done badly still counts. Repetition builds the pattern, and quality
  shows up later on its own."* — false for motor learning, and the only content
  item in the library that could plausibly contribute to injury in a movement
  app.
- *"You can't sneeze with your eyes open."* — a myth, and one any child will
  immediately test.
- *"Sore muscles after exercise are usually a sign of repair, not damage."* —
  inverts the mechanism, and contradicts other entries in the same library.
- *"A sneeze can shoot out of your nose faster than a car drives down most
  neighborhood streets"* — sits above every measured exit velocity.
- *"paying back the oxygen it borrowed"* — the oxygen-debt model, superseded.
- Glymphatic *"rinse cycle"* — directly contested by 2024 work, presented as
  settled.
- *"The strongest muscle for its size is the one in your jaw"* and *"bones are
  stronger than steel"* (explained via density, which is a non sequitur).
- "Fibre" offered as a food group alongside "Carbohydrates" — fibre is one.

**2.3 Two unsafe distractors are never contradicted.** "Closing both eyes while
walking fast" and "Hold your breath as long as possible" appear as wrong
options whose explanations only affirm the right answer. A 9-year-old reads the
wrong option too. Meal-skipping appears as a named option three times.

**2.4 25 of the original 40 Brain Boosts are not knowledge questions** — they
are "pick the obviously kind option", e.g. *"True or false: it's okay to ask for
help when you're struggling."*

**2.5 Mood and mental wellbeing: 4 insights of 270.** Brain Boost has 25
questions on it. Family has 31 insights and **zero** quiz questions — the app's
stated differentiator has no quiz coverage at all.

**2.6 Register is split, not levelled.** "Washing your hands well is one of the
simplest ways to stay healthy" and a 29-word entry using "proprioception" are
aimed at different readers. Four questions are adult gym-vocabulary tests.

**2.7 Duplication is semantic and crosses the two sets.** Six entries say "you
get stronger during rest" (two of them inside the original 90 alone); four say
"eyes, inner ear and feet vote on balance"; ~22 quiz answers restate a printed
insight almost verbatim.

**2.8 Brain Boost carries British spellings (15) while the insights carry none**
— `practised`, `centre`, `colour`, `kilometres`, `fibre`. One fact is stated in
miles in an insight and kilometres in a question.
