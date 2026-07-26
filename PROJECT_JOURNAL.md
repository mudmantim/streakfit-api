# StreakFit — Product Polish Journal

Session: overnight UX polish pass (2026-07-22)
Branch: `overnight-ux-polish`

Goal set by Tim: *"Make StreakFit feel like one of the best apps on my phone."* Not new
features — elevation of the existing product. Every change below is polish or clarity on
something that already exists. No invented functionality, no fake data.

---

## Guiding read of the app before touching anything

StreakFit is already a thoughtful product. Before making a single change I drove the whole
thing as a new user (guest + a fresh registered account, mission completed end to end):

- **The bones are strong.** The Rickie voice (RICKIE_LINES pools), the streak framing
  ("Day N" for days 1–6, "N days" from 7), the anti-punishment Rise Again ceremony, and the
  Mission → Insight → Brain Boost unlock loop are all genuinely well designed. This is not a
  rebuild job.
- **What holds it back from feeling "premium" is the first impression and the chrome**, not
  the core loop. Specifically:
  1. The auth screen is a bare login form. A person who's never heard of StreakFit learns
     almost nothing about what it is or why it's different before being asked to make an
     account.
  2. The dashboard header carries five separate controls (skill select, Rickie-mode select,
     three theme buttons, logout). It reads like a settings bar, competing with the mission
     for attention on the single most important screen.
  3. A brand-new user's stats row shows `🔥 0 days · Best: 0 days · ✓ 0 missions` — three
     zeros as the first thing they see. That's a deflating first impression of a system that
     is otherwise careful never to make you feel behind.
  4. Small visual-noise issues: underlined "How to do this / Exercise Tips" links on every
     exercise row, flat card depth, and celebration moments that are informative but not
     quite *felt*.

Decisions and their reasoning are logged below as I make them.

---

## 1 — First 30 seconds: auth screen (Priority 1)

**Change:** Replaced the bare login-form landing with a value-first invitation. Above the
sign-up controls there is now a three-point card that answers, in one scan:

- 🎯 *Five tiny moves a day* — what it is (a finishable daily mission, not a dreaded workout).
- 🔥 *Build a streak that means something / Rickie helps you rise again* — why it's different
  (the anti-punishment core rule, stated honestly: a streak can slip, and the response is help,
  not a lecture).
- 🦝 *Rickie's in your corner* — why you'd care (a companion, using Rickie's real face).

**Reasoning:** A newcomer previously saw a one-line tagline and a password field — nothing about
what the product *is*. The fastest "aha" for StreakFit is understanding the philosophy (showing
up beats perfection) plus seeing the mission. So: (a) three tight value props carry the pitch in
well under 30 seconds, and (b) the guest button copy changed from "Continue as Guest" to
"Peek at today's mission first — no signup" so the zero-friction path to the actual product is
framed as an invitation, not a fallback. Register CTA changed to "Start free — takes 10 seconds"
to name the (low) cost of the primary path. Copy was kept accurate to the Rise Again design — it
does not claim the streak never breaks, only that you're never punished for it.

## 2 — Home screen: header declutter + honest zero-state (Priority 2)

**Change A — one gear instead of five controls.** The dashboard header used to carry a
difficulty select, a Rickie-mode select, a three-button theme toggle, and a logout button, all
competing with the mission. They now live behind a single ⚙️ settings menu (opens on tap, closes
on outside-click / Escape). Every control kept its *exact* original id and onchange handler — the
JS that reads/writes `#skill-level-select`, `#rickie-mode-select`, the `.theme-btn`s and
`#btn-logout` is untouched; they just moved into a dropdown. Guests see only Theme + Exit (the
difficulty/Rickie rows hide for guests, matching the old behaviour).

**Reasoning:** The home screen should lead with the mission, not with configuration. These are
set-once-and-forget preferences; burying them behind a gear is standard for a reason. Header is
now just brand + gear — calm, focused, unmistakably "here's your thing to do today."

**Change B — no wall of zeros for new users.** A brand-new account used to see
`🔥 0 days · Best: 0 days · ✓ 0 missions`. The stats row is now hidden until there's at least one
day of streak or one completed mission; until then the "Complete all 5 to start your streak"
helper carries the message.

**Reasoning:** Three zeros is the one moment this otherwise-encouraging app accidentally says
"you're behind." A fresh start should look like a fresh start. The stats appear the instant
they're worth celebrating.

**Bonus fix (latent bug):** `--gray-300`, `--gray-600`, `--gray-800` were referenced in the CSS
but never defined in `:root`, so six existing rules silently inherited the wrong colour (this is
what made the relocated Log Out button render white-on-white at first). Defined all three with
the standard grey ramp, repairing those existing usages too.

## 3 — Celebration & visual polish (Priorities 4 & 5)

**Celebration (Priority 4).** Completing a mission was informative but not *felt*. Added, all
gated on `prefers-reduced-motion` and — for the confetti — on Rickie's mode (quiet/minimal Rickie
stays quiet, same rule the toast already follows):

- **Confetti burst** (dependency-free, ~26 brand-coloured pieces from the count badge) on the two
  moments that earn it: a full 5/5 mission and a level-up. Short (~1.2s) and restrained.
- **Count-badge pop** on the transition into 5/5 — a quick scale bounce, then it settles. Fires
  only on the transition, never on every re-render.
- **Progress-bar glow** — the completed green bar gets a soft green halo.
- **Level-up toast halo** — the reaction toast gains a violet glow when a level-up is in it, so it
  reads as a bigger beat than an ordinary completion.

**Reasoning:** "Small celebrations. Not childish." Confetti is reserved for real milestones (not
every single tap), uses the existing brand palette, and clears itself quickly. The badge pop and
progress glow reward the eye without a modal or a full-screen takeover.

**Visual polish (Priority 5).**

- **Exercise-row actions** were dated underlined links ("How to do this" / "Exercise Tips") on
  every row — the single most 1990s-looking element in the app. They're now quiet rounded chips
  (indigo-tinted for the primary how-to, grey for tips), no underlines, better tap spacing.
- **Daily card depth** — the mission card, as the heart of the app, now sits on a soft on-brand
  (indigo-tinted) shadow so it reads as elevated above the rest of the page.

All motion respects `prefers-reduced-motion`. No console errors introduced.

## 4 — Verification & housekeeping

- **Mobile checked for real.** Because the automation window wouldn't resize its viewport, I
  rendered the app inside a 370px-wide same-origin iframe (a true mobile viewport, so the
  `max-width: 400/480/540px` media queries actually fire) and drove the auth screen, dashboard,
  and settings menu through it. Tightened the auth screen's vertical rhythm so the whole
  value-prop + form + guest CTA fits a common phone without hunting for the guest button.
- **Backend untouched, and proven so:** all 56 pytest tests pass. Every change this session is
  HTML/CSS/JS in `static/` — no route, model, or API change.
- **No new console errors** across the flows I exercised (guest, register, login, full mission
  completion, level-up, settings menu, theme switching).
- **Service worker cache bumped** `v0741 → v0742` per the project rule that any `static/` change
  must bump it, so returning users actually receive the new CSS/JS instead of a stale cache.

## What I deliberately did NOT do

- **Left Rickie's voice alone.** The RICKIE_LINES pools, expression system, and mode gating are
  already excellent and on-brand (per the Character Bible). Priority 3 asked for a companion, not
  a chatbot — it already is one. I only made sure the new celebration respects the same quiet/
  minimal-mode gating the toast uses, so Rickie doesn't get louder than the user asked for.
- **Didn't touch the retention loops' logic.** The "come back tomorrow for Day N" banner and the
  Mission → Insight → Brain Boost unlock are already strong Priority-6 mechanics. Strengthening
  beat inventing here.
- **No new features.** Everything is polish or clarity on something that already shipped.

## Worth discussing tomorrow

- The notification opt-in card ("Would you like a daily reminder?") renders *above* the mission
  on load, pushing a returning user's mission down. It's tied to notification-permission state so
  I left it, but it may deserve to sit below the mission, or become a smaller inline nudge.
- Difficulty now lives in the settings menu. If you'd rather it stay one tap away (people do
  experiment with it early), it could instead sit as a small control on the mission card itself.
  I chose the calmer header; easy to revisit.

---

# Session 3 (2026-07-23) — "Would a friend trust and recommend this?"

Branch: `trust-and-delight` (off `overnight-ux-polish`). Reviewed the app end-to-end as a
skeptical first-time user + QA engineer, hunting specifically for things that erode trust or feel
unfinished.

## Fix — account creation had no password floor (trust + security)

A skeptical reviewer's first probe: I registered with password `"1"` and it was **accepted (201)**.
An app that stores a streak you care about should never allow a one-character password — it reads
as unfinished and untrustworthy the moment anyone tests it.

- **Server (authoritative):** `/api/register` now requires a password ≥ 8 chars and a username of
  2–80 chars, with friendly, specific messages ("Password needs to be at least 8 characters.",
  "That username is taken — try another." instead of the terse "Username already exists").
- **Client:** `minlength` on the register inputs (matches the server), a quiet hint under the
  password field — *"At least 8 characters. No email needed — just a username."* — so the rule is
  known *before* submitting, not discovered through rejection.
- **Trust line** at the foot of the auth card: *"No email, no ads, no selling your data. Your
  streak is yours."* A first-time user handing over a password deserves to know what they're (not)
  signing up for. Verified accurate: registration takes only a username + password.

*Why:* this is the single biggest trust gap found in the app — cheap to fix, server-verified,
and it changes the very first impression a careful person forms. All 56 tests still pass.

## Reviewed and deliberately left alone (already good)

- **"Ask Rickie" with no API key** degrades *in character*: "🦝 Rickie stepped away from his burrow
  for a bit — try again in a little while." No raw error, no broken state — exactly the graceful
  failure that builds trust. No change.
- Dashboard copy, empty states (Teams, Side Quests), and time-of-day Rickie greetings are on-brand
  and finished. Left as-is.

---

# Session 4 (2026-07-25) — "Production-ready release candidate"

Goal set by Tim: advance StreakFit to a release candidate — the primary Daily Mission flow
working from registration through completion, a polished first 30 seconds on mobile, all tests
plus lint / type / build gates passing, critical paths verified in a browser, no broken images
or dead controls or console errors or mobile layout defects, production health verified, no
secrets committed.

This was a *verification-led* pass, not a design pass. I did not decide up front what to
change; I built the missing gates, drove the real product in a real browser at phone width,
and fixed what that surfaced. Everything below was found, not guessed.

## The gates the project didn't have (and the CI that had never run)

The first thing I checked was the baseline, and the baseline was broken in a way nobody could
have seen yet:

**`pytest tests/` did not work at all.** `tests/conftest.py` does `from app import app`, but
pytest's prepend import mode puts a test file's *basedir* on `sys.path` — here `tests/`, which
has no `__init__.py` — and never the rootdir. So the exact command written into
`.github/workflows/ci.yml` and the `Makefile` `test` target died at collection with
`ModuleNotFoundError: No module named 'app'`. The suite had only ever looked green because it
was being run as `python -m pytest`, which implicitly prepends the cwd. The CI workflow was
still unpushed and had therefore never executed once — so the very first push to `main` would
have gone red. Fixed with a `pytest.ini` setting `pythonpath = .`; all three invocation forms
now collect and pass 155 tests.

*Deliberately not a `pyproject.toml`:* Render's Python build inspects a repo-root
`pyproject.toml` and can bypass the `requirements.txt` install path. A tool-specific config
file cannot affect the production build. Same reasoning for `ruff.toml` and `mypy.ini`.

**`make setup` — the documented one-command entry point — failed twice** before reaching a
working app. It built the venv from the interpreter's *name* on PATH, so a symlinked launcher
(uv-managed CPython, and some pyenv/Homebrew layouts) left the venv resolving its own prefix to
a nonexistent `/install` and the next pip call aborted with a fatal
`ModuleNotFoundError: No module named 'encodings'`. And the `env` target treated `.env` as
all-or-nothing: a partial `.env` — the common case, a developer who had added only
`ANTHROPIC_API_KEY` — meant `SECRET_KEY` was never generated and `make db` died on the
`RuntimeError` `app.py` raises at import. Both fixed and verified with the exact
previously-failing invocation.

**There was no linter, no type checker, and no build step.** The last of those matters most
here: `static/` is served as-is with no bundler, so *nothing* would fail on a broken asset
reference — a renamed icon ships silently and surfaces as a broken image in a user's browser.
So I wrote `scripts/build_check.py` as the substitute for the build a bundled app would get,
and it is proven to fail, not just to pass: injecting a renamed service-worker precache entry,
a missing `index.html` image, an unversioned cache name, and a manifest with no `start_url`
produced 5 findings and exit 1.

The gates are configured to be *meaningful and green*, not aspirational. `ruff` runs the rule
families whose findings are overwhelmingly real defects (E4/E7/E9/F/B). Style families that
would mass-rewrite the production monolith — import sorting, naive-datetime, blind-except,
bandit, pylint — are listed in `ruff.toml` with the reason each is deferred, so the omissions
are recorded decisions rather than accidents. `mypy` is scoped to `app.py` with untyped-def
checks off, because the codebase is unannotated; it still catches undefined names and
unannotated mutable module state. Being honest about scope was the point — a gate nobody can
keep green gets disabled, and then it protects nothing.

**Real defects the linter found:** a `fav_category` computed and thrown away (the response uses
a separately-tallied `category_row`), an `int` default handed to `os.environ.get` (documented to
return `str`), and the startup DB-head guard raising `SystemExit` inside `except` without
`from exc`, dropping the underlying cause from the traceback.

**Four findings I annotated instead of "fixing", because fixing them would have broken things.**
`== True` at two sites is *correct* — inside a SQLAlchemy filter it builds the SQL predicate,
and a truthiness check would evaluate the column object (always true) and silently drop the
filter. `zip()` without `strict=` is deliberate where the sequences are meant to be unequal
(`zip(msgs, msgs[1:])` walks adjacent pairs; `strict=True` would raise). `conftest.py`'s imports
must follow the `os.environ` setup. And all five implicit string concatenations are intentional
multi-line copy in list literals — I read each one to confirm none was a missing comma.

## Mobile: what driving it actually showed

Chrome could not be resized on this machine (GNOME/Wayland ignores it), and the app correctly
refuses to be iframed (`X-Frame-Options: DENY`, `frame-ancestors 'none'` — verified working). So
I drove it at a true 390px *layout* width by rewriting the stylesheet's own media conditions via
CSSOM — activating its `<=400px` and `<=480px` rules and suppressing its `>=540px` desktop rules
— and constraining the root box to 390px. Documented here because it has one known limit:
`position: fixed` elements still size to the real viewport, so overflow and overlap reports
*inside modals* are harness artifacts. I checked rather than assumed: `.ex-modal` is
`width: 100%; max-width: 480px`, so it is correctly 390px on a phone. Nothing to fix there.

**Today's Mission rows were misshapen on a phone** — the primary screen, which makes this the
most important thing found. The row is one flex line of `[info | pill | button]`; the pill
(78px) and button (86px) are `flex-shrink: 0`, leaving `.daily-exercise-info` about 141px on a
390px screen. Too narrow for "How to do this" and "Exercise Tips" side by side, so the two chips
stacked into a lopsided 162px-tall column beside a vertically-centred pill and button, and the
reps line wrapped mid-phrase ("3 sets of 20 reps each / direction"). Giving info its own
full-width line puts the chips inline and fits the reps line, with the pill and button sharing
the line below — pill left, button right. Row height **185px → 169px**: the fix makes the
mission shorter, not taller, so more of it is visible in the first 30 seconds.

**Fourteen interactive controls were below 44px** — this stylesheet's *own* established minimum;
seven rules already set it, so these were simply missed. The header settings gear (38×38),
Memory Book (38px), both team empty-state buttons (33px), Add to Home Screen (39px), the
notification prompt's Yes / Not now (31px), both settings selects (27px), the three theme
buttons (28×25), Log Out (30px), the exercise modal's ✕ (26×24 — and the only way to dismiss
it), Memory Book's ✕ (32px), its two page arrows (36px), and the **Log In / Register tabs
(35px), on the first screen a new user ever touches**. Title clearances widened to 2.75rem in
step, checked against the longest exercise name in the library so text cannot run under the
enlarged ✕.

Two process notes worth keeping:

- **The first attempt at the tap-target fix silently did nothing** for Memory Book. `.mb-close`
  and `.mb-nav-btn` are defined ~400 lines *below* where a "mobile responsive" section sits, and
  a media query adds no specificity — so the override lost to the later base rule. Only
  re-scanning after the edit caught it. Both new blocks now live at the end of `style.css` with
  a comment saying why they must stay there.
- **The tabs were only found on the second sweep**, because the first sweep ran from an
  authenticated session and never rendered the logged-out landing page. Scanning "the app" is
  not the same as scanning every state of it.

**A real hole in the build gate, found by looking at where images come from.** The mission API
hands the client `/static/exercises/{key}.svg` — a reference that lives in *Python*, invisible
to any scan of the static files. A renamed SVG would have reached a user as a broken image in
the exercise modal with nothing failing first. `build_check.py` now reads `EXERCISE_LIBRARY` and
asserts all 90 illustrations exist, refuses to pass vacuously if the library yields no keys, and
was proven by deleting one (exit 1, named it). This also corrected an early wrong read of mine:
I had noticed zero references to `exercises/` in `app.js` and briefly suspected 90 dead files —
they are all used, just addressed from the server side.

**No dead controls.** All 27 of StreakFit's own interactive controls on the dashboard either
respond to interaction or are legitimate form submits (exactly one submit button sits inside a
form, `create-form`'s "Add" — intentional). One methodology note: my first dead-control check
flagged "Reveal Insight" and "Reveal Brain Boost" because a DOM-length snapshot showed no
change. That was my detector's fault, not the app's — the handler toggles `hidden` on two
elements, adding and removing `hidden=""` for a net-zero length delta. Re-tested on actual
state, both work.

Also worth recording so it doesn't mislead someone later: a third-party Chrome extension
(AITOPIA) injects `aiinhbfoop-*` controls into the page and writes an `aiinhbfoop_user_consent`
localStorage key. None of it is StreakFit's. Browser audits of this app need to filter it out.

## Verification evidence

- **Tests:** 155 passed, via all three of `pytest tests/`, bare `pytest`, and
  `python -m pytest tests/`, on Python 3.12.7 (the version `runtime.txt` pins for production).
- **Gates:** ruff clean; mypy clean; build check 32 assertions, 0 problems.
- **Full CI pipeline run locally end to end** — pinned-dependency gate, ruff, mypy, build check,
  155 tests, `verify_all` import smoke: all pass.
- **End-to-end verification suite** (`scripts/verify_all.py`) against a local server:
  **81 passed, 0 failed**.
- **Browser, at a 390px layout width with the phone CSS active:** registration succeeded; all
  five exercises completed 0/5 → 5/5; Day 1 streak, green progress bar, "The streak starts
  here", and the Insight / Brain Boost cards all rendered. Guest mode ("Peek at today's
  mission") completes an exercise and surfaces the sign-up prompt. Logging back in restores
  Day 1 / 5-of-5 state. Across dashboard, settings menu, exercise modal, Memory Book,
  how-to-expanded, logged-out landing, and guest mission: **0 console errors, 0 unhandled
  rejections, 0 failed fetches, 0 broken images, 0 text overlaps, 0 horizontal overflow, 0
  remaining sub-44px tap targets.**
- **Production (`streakfit.pro`), read-only:** `/health` 200 in ~0.2s; `/` 200; all five
  security headers present (`X-Frame-Options: DENY`, `frame-ancestors 'none'`, `nosniff`,
  `Referrer-Policy`, HSTS); `app.js`, `style.css`, `sw.js`, `manifest.json`, `rickie.svg`,
  `icon-192.png` and an exercise SVG all 200; bad credentials correctly 401.
- **No secrets committed.** `.env` is gitignored and stayed untracked throughout; every staged
  diff was scanned for key material before commit. The only credential-shaped strings added are
  literals like `build-check-not-for-production`.

## Honest limitations of this pass

- **Nothing is deployed.** Production still serves SW `v0747`; `main` is 10 commits ahead. The
  standing rule on this project is explicit per-action authorization for production pushes, and
  a general goal statement is not that authorization. So "deployment verified" is true only of
  the *current* production build, not of this work. What is needed from Tim is named in the
  handoff.
- **The prod-side verification suite was not run.** It is prod-safe by design, but a previous
  run left a synthetic `qa_smoke_*` account owning a team that no API route can delete, so it
  needs manual cleanup afterward. Creating fresh production residue without asking wasn't mine
  to choose. It remains the right first check *after* a deploy.
- **The 390px pass was a layout-width simulation, not a real device.** Media queries were
  activated by rewriting their conditions, which is faithful for layout, overflow, and geometry,
  but it is not a physical phone: no touch input, no real device pixel ratio, no iOS/Android
  font stack. The tap-target sizes and row geometry are measured facts; "feels good in the
  hand" still wants one pass on a real handset.
- **`mypy` covers `app.py` only,** and with untyped-def checks off. It is a floor against
  undefined names and unannotated module state, not a typing adoption.
- **The lint deferrals in `ruff.toml` are still real debt** — import sorting, naive datetimes,
  and blind-except handlers in particular. The datetime one is the substantive one: moving to
  timezone-aware datetimes changes streak arithmetic and needs its own change with its own
  tests, which is exactly why it wasn't folded into a lint sweep.
- **The emoji-vs-avatar redundancy is untouched.** Coach lines are prefixed with 🦝 while
  Rickie's avatar image already sits beside them. That's a taste call for Tim, not a defect, so
  I left it. (The glyph also renders as tofu on this Linux box — a local font gap, not an app
  problem.)
- **No new automated check covers mobile geometry.** The tap-target and layout findings were
  caught by ad-hoc browser scanning, so a regression could reintroduce them silently. A
  headless-browser geometry check would close that, and it does not exist yet.
