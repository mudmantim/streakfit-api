# StreakFit Status — June 22, 2026

## Currently implemented features

**Live (committed, last commit `44284f1`):**
- Auth (JWT register/login), rate-limited
- Daily Mission — 5 exercises/day, personalized by skill level, deterministic seeding
- Streaks — current/best streak, total missions, Rise Again return ceremony
- Today's Insight — 90-entry fact library, tap-to-reveal card
- Coach v1 — Anthropic-backed chat, scoped system prompt, exercise-level mistakes/tips modal
- Side Quests (custom habit tracking, separate from Daily Mission)
- Guest Mode
- PWA (manifest, service worker, iOS-correct icons, install banners)
- Analytics (guest funnel events + `/admin` dashboard)
- Admin stats dashboard

**Local only — built and verified this session, not committed:**
- Brain Boost — daily multiple-choice question (own card, separate from Today's Insight), answer-once enforcement, correct/incorrect points (10/3, no penalties), per-question explanations, Rickie-coaching feedback tone. New table `brain_boost_answer`, new route `POST /api/brain-boost/answer`, `/api/daily` extended with a `brain_boost` field.
- Rickie mascot — `static/rickie.svg` (flat-color cartoon raccoon), Coach UI renamed to "Ask Rickie" (button/title/placeholder, text-only, no API changes), Rickie avatar in Coach modal + Brain Boost card + Today's Insight card, Coach system prompt rewritten for Rickie's voice (friendly coach/health educator, occasional light humor, never sarcastic, never childish — scope restrictions and refusal string unchanged).
- Mobile polish — Daily Mission complete/done buttons and Brain Boost option buttons now meet 44px touch targets; exercise rows guaranteed ≥48px tall at all widths (not just <400px); header-right controls wrap instead of overflowing on narrow phones (real overflow bug found and fixed mid-session); retention/install banner dismiss button enlarged to 40×40px.

## What's live vs. local only

| | Live (deployed) | Local only |
|---|---|---|
| Daily Mission, Streaks, Today's Insight, Coach v1, Side Quests, Guest Mode, PWA, Analytics | ✅ | |
| Brain Boost | | ✅ |
| Rickie mascot/branding | | ✅ |
| Mobile touch-target polish | | ✅ |

**Nothing from this session has been committed, let alone deployed.** The working tree is the only place this code exists.

## What was completed today (this session)

1. Audited the Flask baseline vs. a competing Node/React version (feature comparison, recommendation to continue on Flask)
2. Audited the exercise illustration system (90 stick-figure SVGs), designed and iteratively refined 5 sample replacement illustrations in a new flat-color style, then scaled a first batch of 10 more
3. Designed the Rickie the Raccoon character system (personality guide, visual guide, poses, dialogue examples) and a broader gamification proposal (coins, leaderboard, surprise challenges) — flagged tension with locked design-philosophy memory (anti-addiction stance, "personal streaks are sacred," no paid streak saves) before proceeding at the user's explicit direction
4. Recovered and summarized the prior streak-save/freeze/grace-day decision from memory (conclusion: no personal streak restoration was ever part of the design, by deliberate choice)
5. Implemented Brain Boost v1 (simple reveal) → then Phase 1 (full graded quiz with points) → then split it into its own card with explanations and Rickie-coaching tone
6. Implemented mobile polish (touch targets, header overflow fix, dismiss button sizing)
7. Built `static/rickie.svg` and wired Rickie into Coach (rename + avatar + personality) and both Insight/Brain Boost cards
8. Verified everything locally at every step (Playwright, desktop + mobile, console/exception checks, API regression sweeps) — nothing deployed

## Next recommended task

**Commit the work, in logical groups, before doing anything else** — this is the most urgent item; see Known Issues below. Suggested commit split:
1. Mobile polish (pure CSS, zero risk, independent of everything else)
2. Brain Boost (`app.py` Brain Boost sections + migration file together, `app.js`/`style.css` Brain Boost UI)
3. Rickie integration (`rickie.svg`, Coach rename, avatar wiring, personality prompt)

After committing: decide whether to push/deploy, or continue with the next gamification phase (Leaderboard, Surprise Challenges, etc. — none of which have been built yet).

## Known issues / risks

- **Nothing is committed.** All of today's work (Brain Boost, Rickie, mobile polish) exists only in the working tree on this machine. Highest-priority risk — commit before doing anything else tomorrow.
- **The new migration must be committed together with `app.py`.** `migrations/versions/k5l6m7n8o9p0_add_brain_boost_answer_table.py` is currently untracked; committing `app.py`'s `BrainBoostAnswer` model without this migration will break `flask db upgrade` on any fresh deploy.
- **Pre-existing, unrelated to this session's work:** the Alembic `baseline` migration (`a3f8b1c2d4e5`) is a no-op (`pass`/`pass`), so a genuinely fresh database needs `db.create_all()` + `flask db stamp <head>` rather than a clean `flask db upgrade` from empty. Worked around for local testing every session; not fixed, not blocking, but worth knowing if a fresh environment ever needs bootstrapping.
- **Coach is untested against a real Anthropic response** in this environment — no `ANTHROPIC_API_KEY` was set locally, so every local verification of Coach saw the graceful `503 coach_unavailable` path, never an actual model reply. The rename/avatar/personality changes are verified structurally (right labels, right avatar, right prompt text) but not verified against live model output.
- **`static/exercise_samples/`** (the illustration-style exploration work — 5 refined samples + first batch of 10 more + comparison HTML pages) is untracked and not part of any feature work. Decide whether to keep it in the repo, move it elsewhere, or `.gitignore` it before it accidentally gets swept into a commit.
- **Leaderboard, Surprise Challenges, coins/points economy beyond Brain Boost's simple per-answer points, Rickie cosmetics/inventory** — none of these exist yet, by explicit instruction across multiple passes. Brain Boost's points (10 correct / 3 incorrect) are stored per-answer but never aggregated or displayed anywhere beyond the immediate feedback.

## Exercise Visual Refresh

See [EXERCISE_VISUAL_REFRESH.md](EXERCISE_VISUAL_REFRESH.md)

Milestone: Exercise Visual Refresh complete.

Current progress: 90 / 90 visuals complete

Beginner: 30 / 30
Intermediate: 30 / 30
Advanced: 30 / 30

Verification:
- Production verified on streakfit.pro
- 80px readability verified
- Exercise Tips modals verified
- Distinctness checks passed: l_sit_hold vs pancake_stretch, tuck_jump_burpee vs tuck_jump, straddle_v_up vs straight_leg_raise family
- guest and registered flows validated
- no console errors
- service worker cache refreshed successfully (streakfit-v0727)

## Sprint C — First Two Minutes Polish (July 2026)

Status: Complete

Shipped:
- Warm-colored Rickie avatar (replaced flat gray version)
- Register screen value proposition:
  "Tiny wins every day. Build healthy streaks together."
- Guest-mode dashboard banner:
  "Guest mode — sign up anytime to save your streak."

Verification:
- Production verified on streakfit.pro
- Desktop layouts verified
- 320px mobile layouts verified
- Warm Rickie renders correctly across all touchpoints
- Guest banner appears only for guest users
- Registered users do not see the guest banner
- Cumulative Layout Shift (CLS) = 0
- No console errors observed
- Service worker cache refreshed successfully (streakfit-v0724)
- Guest and registered user flows validated end-to-end

Operational Notes:
- Any change to assets under `static/` (SVG, CSS, JS, imagery) requires a service worker cache version bump.
- Cache advanced to `streakfit-v0724`.
- Registration reliability fix (`pool_pre_ping` + `pool_recycle`) remains stable in production.
- Registration 500s are now considered a monitoring item rather than a beta blocker.
