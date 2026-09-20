# Release blockers — 2026-09-20

Branch `product-completion`, HEAD `4b13154`, **112 commits ahead of
`origin/main`**. Nothing pushed, nothing deployed.

Every claim below is either a command output recorded here or a file path you
can open. Where something was not measured, it says so rather than inferring.

---

## 1. Completed and verified locally

All five gates green against HEAD `4b13154`, run after the last change, not
before it:

| Gate | Result |
|---|---|
| `pytest` | **641 passed** |
| `scripts/build_check.py` | **42 assertions, 0 problems** |
| `scripts/content/validate.py` | **0 errors, 438 items cleared to serve** |
| `verify_all` (`SMOKE_BASE_URL=http://localhost:5000`) | **112 passed, 0 failed** |
| `make uicheck` | **157 checks, 0 problems** |
| `tests/test_migrations.py` | **2 passed** — empty DB builds from the chain alone |

**Verified features**

- **Every API error answers in JSON** (`f6a326c`). The 413 and 403 handlers
  returned HTML, so the client's `res.json()` threw and the user saw nothing.
  Fixed on both sides: handlers return JSON, and `static/app.js` treats a
  non-JSON body as a message rather than an exception. `build_check.py` runs
  the real `api()` function against 502/403/204/200 in node, so the guard is
  checked rather than merely written.
- **Rickie's model is configuration** (`f6a326c`), and **the reply budget is
  bounded** (`f88aed9`). Making `max_tokens` configurable had left it
  unbounded — `STREAKFIT_COACH_MAX_TOKENS=200000` was accepted. It now clamps
  to 64–2048 around the 768 it runs at, a malformed value falls back with a
  loud log, and the model name is logged at boot because a name carries no
  price and an unexpected model is a cost change. Verified: 200000→2048,
  0→64, `abc`→768, 1500→1500.
- **Conversation-memory failures are reported, not swallowed.** A persist
  failure increments `_COACH_HEALTH` and surfaces through the
  `coach.memory_writes` self-check (UNKNOWN at zero, FAIL when nonzero).
  The test drives a real failure through `/api/coach` — an earlier version set
  the counter directly and passed with the increment deleted.
- **Content provenance is honest** (`4b13154`). See §4 below.

---

## 2. Implemented but not fully verified

- **Stage 2 child-account foundations.** `can()` exists, denies by default,
  and has **18 passing tests**; the schema (`age_band`, `GuardianLink`,
  `Consent`) is real and migration `08681a9bd9f9` builds it from empty.
  **But no route in `app.py` calls `can()`, and nothing writes `age_band`,
  a `GuardianLink` or a `Consent` row.** The permission layer is correct and
  currently unreachable. This is deliberate — see §3 — but it means the
  behaviour is verified only in tests, never through the product.
- **Team photos and the roster fix are verified locally only.** They are not
  in production; see §4 and §5.
- **`self.coach.configured` is OBSERVED, not VERIFIED** — the self-check can
  see a key is present but not that a call would succeed, because confirming
  that would mean a paid AI call.

---

## 3. Awaiting owner decision or external assurance

### 3a. The Anthropic written assurance — the one hard external blocker

- **Requirement:** **16 CFR § 312.8(c)** — before releasing a child's personal
  information to a service provider, the operator must take reasonable steps
  to determine the provider can protect it **and obtain written assurances
  that it will**.
- **Authoritative source:** `docs/child-safety/requirements-matrix.md`,
  "The one finding that changes the build".
- **The specific assurance needed:** a written § 312.8(c) assurance from
  Anthropic, and **zero-data-retention on the Ask Rickie API key**.
- **Owner action:** ask Anthropic in writing. This is a commercial
  negotiation, not a research question, and **not something this session will
  do on your behalf.**
- **Encoded, not filed away:** `CAP_ASK_RICKIE` is hard-blocked for children
  **regardless of guardian consent**, because consent is not what is missing.
  It opens only on `STREAKFIT_CHILD_AI_ASSURANCE_ON_FILE=1`, and setting that
  flag is a claim that the assurance exists. A test proves the gate *opens*,
  so it is wired to something rather than hard-coded to refuse. **The gate has
  not been removed or weakened.**

### 3b. Your four Stage 2 decisions, against what exists

| Your decision | State | What is missing |
|---|---|---|
| **All ages at launch** | **Blocked** | No age screen exists. Open: whether a self-declared *band* is a compliant neutral screen or month-and-year is the floor. Until something writes `age_band`, every account is NULL — and NULL is treated as a child, so this is safe but non-functional. |
| **Guardian-authorized, age-appropriate Rickie** | **Blocked on 3a** | The consent path is designed and unbuilt. Even built, § 312.8(c) blocks under-13 AI. Anthropic's Guidelines additionally require their child-safety system prompt, AI disclosure and a break reminder — of the four SB 243 / New York duties, **only the crisis protocol is done**. |
| **Guardian-approved private teams** | **Designed, not implemented** | Per-team consent is specified. The real blocker is not consent: **there is no blocking, reporting or moderation anywhere in the product — zero routes.** A child in a team with an adult behaving badly has no in-app recourse. Mixed-age teams should not be enabled before that exists. |
| **Defined — not unrestricted — guardian access to private conversations** | **Open question, deliberately unanswered** | COPPA § 312.6 makes a guardian access path a *requirement* for under-13s, so this is not a values choice. The design says it must be a deliberate, logged access request rather than a live feed, **because the guardian is not always the safest person to notify** — a child disclosing abuse may be disclosing it about the recipient. What the right escalation is needs a child-safety specialist. Recorded as open rather than guessed at. |

### 3c. Other open decisions

- **Six questions need qualified legal review before launch**, listed in
  `requirements-matrix.md`. The sharpest is **CTDPA's ban on "any system
  design feature to significantly increase, sustain or extend any minor's
  use"** — streaks and daily-mission nudges are exactly that shape. Whether a
  streak that never punishes a missed day reads differently is the argument,
  and it needs counsel, not an engineer.
- **Content review staffing** — §4.

---

## 4. Awaiting production infrastructure or deployment approval

### 4a. Content: the numbers, kept separate on purpose

| Measure | Count |
|---|---|
| Generated / in the store | **798** |
| Structurally validated (0 errors) | **798** |
| **AI-reviewed** | **440** (every accepted item, `review.by: ai-agent`) |
| **Independently human-reviewed** | **0** |
| Genuinely source-checked | **11** |
| **Actually served** | **438** |

**No unreviewed item reaches users.** Serving requires `stage: accepted`, and
every accepted item carries a review record. But **AI review is all there has
ever been**, and until today the library could not have told you that.

The audit found 664 items labelled review depth `sourced` whose own records
show `"sources_checked": []`. The defect was the **label**, not the content —
the reviews happened and their notes are substantive — so the labels were
corrected (`sourced → read`, each with an audit note) rather than 95% of the
library being thrown away. Two gates in `validate.py` stop it recurring:
`sourced` with no sources is an error, and `review.by` outside
`ai-agent`/`human` is an error.

**Quarantined, not gate-weakened:** `SF-EXP-000004` and `SF-EXP-000007` moved
accepted → revise. Both assume a reader who can stand — one on a deliberately
narrowed stance — with no seated alternative. They cleared the balance-safety
gate only because it matched phrasings and neither was on the list; the gate
now covers stance and marching wording. Served pool 440 → 438, ratchet
224 → 222 with the reason recorded.

**Owner decision:** whether 0 independent human review is acceptable for
launch content that includes physical instructions and health claims read by
a nine-year-old and a seventy-year-old. That is a staffing decision, not an
engineering one.

### 4b. Production is behind, and one gap is live

Running `verify_all` with no base URL defaults to `https://streakfit.pro`. It
returned **84 passed, 10 failed** — that is an accurate measurement of the
**deployed** build, not of this tree. It says production:

- has **no team-photos routes** (`/api/photo-filters` 404), and
- **returns `username` in the team roster** — a login identifier exposed to
  teammates.

Both are fixed on `product-completion` and **neither fix is deployed**. The
roster leak is the one I would not leave in production.

### 4c. Infrastructure not provisioned

- `storageProvider: sqlite`, `environment: development`. No production
  Postgres, no shared rate-limit storage, no object storage for team photos
  (currently Postgres-by-constraint).
- Deploy contract is `flask db upgrade && STREAKFIT_ENFORCE_DB_HEAD=1
  gunicorn app:app`. **Not run. Awaiting your deployment approval.**
- No billing. No live child accounts. No real users invited.

---

## 5. Awaiting Mudman Command qualification

**This is no longer a blocker, and my earlier finding is superseded.**

An earlier measurement reported StreakFit NOT QUALIFIED on two FAILs —
`checkAuthBoundary` and `checkLoginThrottle` probing PorchLight paths that
StreakFit does not have. **That was measured against a stale shared checkout.**

Verified directly against `origin/master` (`git show`, immune to whatever
branch the shared tree is on):

- `runner.ts` carries `authProbePath`, `throttlePath` and
  `loginIdentifierField`, each defaulting to the PorchLight literal it
  replaced — the same pattern `healthPath` already used.
- `apps.ts` **registers StreakFit**: `healthPath: /api/health`,
  `authProbePath: /api/me`, `throttlePath: /api/login`,
  `loginIdentifierField: username`.

**Re-measured** by running that registry entry through `origin/master`'s
`verifyApp()` against a local StreakFit:

> **12 checks — 11 PASS, 1 UNKNOWN, 0 FAIL. Overall PASS, level VERIFIED,
> recommendation `proceed`.**

The single UNKNOWN is `self.coach.memory_writes` ("none since boot"), which is
its designed value at zero.

**Owner of the change:** **another StreakFit session**, not the Command
session and not queued for you. Committed as `5a9b8f9` ("Verification probes
ask each app about its own routes"), reaching master through `ad1cbf4`. Note
for the record: `5a9b8f9` is **not an ancestor of master** — the reconciling
commit copied the content rather than merging the commit, so `git log` on
master will not show it. Credit belongs to that session, not the reconciler.
The change also carries an in-run negative control on the auth probe and a
throttle guard that reports UNKNOWN when no attempt was actually rejected.

**No Mudman Command file was modified from this session, and no qualification
score was invented.** The verifier was extracted read-only into a scratchpad
and run from there.

**Two caveats that keep this out of §1:**

1. That PASS is a **local development build** (`environment: development`,
   `storageProvider: sqlite`). **Production has never been measured this
   way.** Qualification of the deployed product remains unmeasured until §4c.
2. **§6A is open and not ours to fix.** StreakFit's Option B rate-limit
   fallback degrades *deliberately* when shared storage is unreachable, and
   Command's `CheckStatus` has no value for "degraded on purpose" — so it
   reports FAIL and the weakest-link roll-up recommends "roll back" for a
   build that is not the problem. Written up at
   `docs/reports/2026-09-20-mudman-command-qualification.md` §6A (`c5539d7`).
   A fourth enum value was explicitly rejected as unknown-shaped noise at
   every consumer. Parked pending the Command owner's decision; **nobody is
   implementing a fix.** It will surface the moment production uses real
   shared storage.

---

## Next concrete actions, in the order they unblock things

1. **Deploy `product-completion`** — 112 commits, all gates green. The team
   roster is leaking `username` in production today and the fix is sitting
   here. Needs your approval; nothing is pushed.
2. **Write to Anthropic** for the § 312.8(c) assurance and ZDR on the Ask
   Rickie key. Longest lead time of anything on this list, and under-13 AI
   cannot ship without it.
3. **Decide on content review staffing** — 0 of 438 served items have been
   independently human-reviewed, and they include physical instructions.
4. **Engage counsel** on the six questions in `requirements-matrix.md`,
   starting with whether streaks are a prohibited engagement-extending design
   feature for 13–17s. That one can change the product, not the paperwork.
5. **Build blocking / reporting / moderation.** Unbuilt for want of time
   rather than a decision, and it gates every mixed-age team.
6. **Then** decide the age screen shape, which unblocks everything else in
   Stage 2.

Not done and not to be done without you: no push, no deploy, no live child
accounts, no invitation to Olivia or any real user, no infrastructure
provisioned, no billing, no paid AI calls.
