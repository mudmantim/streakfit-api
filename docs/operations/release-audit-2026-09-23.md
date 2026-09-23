# Release audit — `ed72c66` + `c40d837`, overnight 2026-09-22/23

Autonomous audit prepared for the owner's phone test. **No application code,
committed tests, production configuration or migrations were modified. Nothing
was pushed, merged or deployed. No production data was read or written beyond
two unauthenticated GETs of public endpoints.**

> **Superseded for the release decision by [section 6](#6-real-phone-test-and-final-release-preparation--2026-09-23-afternoon)**,
> written after the real-phone test passed. The release now also contains
> `5e51274` and `0c5fcde`; sections 1–5 describe the overnight state.

Everything below is separated into **Verified** (I ran it and saw the result),
**Documented** (the repo asserts it; I did not independently confirm), and
**Assumption / Unknown** (nobody has evidence).

---

## 1. The local phone-test server — secured

**Verified.** The server that was running with a temporary admin credential was
identified, stopped, and restarted without it.

| | Before | After |
|---|---|---|
| pid | 3136238 | **3250449** |
| `ADMIN_SECRET` | `uicheck-local-secret` | **not set** |
| `/api/admin/*` unauthenticated | would accept the test secret | **403** |
| Working directory | `integrate-product-completion` | unchanged |
| Database | local SQLite | **unchanged, preserved** |

**Verified security properties of the running server:**

- `ADMIN_SECRET` absent from the process environment; `/api/admin/reports`
  returns **403** for: no header, the old test secret, a guessed secret, and an
  empty secret. `_require_admin_secret()` fails closed when the variable is
  unset, so the admin surface is unreachable from the LAN.
- `DATABASE_URL` absent → falls back to `sqlite:///streakfit.db`. The process
  has **five open file descriptors on the local SQLite file and zero outbound
  network connections**, which is what proves no production database is
  reachable from it.
- No `ANTHROPIC_API_KEY`, no `RESEND_*`, no `STREAKFIT_NOTIFY_*`. The AI coach
  will answer `coach_unavailable` (503) — the panel opens, the reply does not
  arrive. That is expected locally, not a defect.
- Test database preserved: **323 users, 21 teams**, backed up before the
  restart to the session scratchpad.

**Verified serving the release under test.** `/api/build-identity` reports
`e94487ef1dba` and `environment: development`; the only difference between that
commit and `c40d837` is `RICKIE_3D_PLAN.md`, so **the application code served
is exactly the release**. Confirmed by fetching the assets over the LAN:

| served asset check | result |
|---|---|
| `--t-body-wash` in style.css | present |
| `width: 64px` base / `112px` ≥900px | present |
| `revealWhenClear`, `stepAsideNow`, `el.offsetWidth` in rickie-roam.js | present |
| `streakfit-v0922f` in sw.js | present |
| `_buildTeamRickieCard` in app.js | **absent** (correct) |
| `Teams are optional` in app.js | present |

---

## 2. Release audit against production baseline `4700708`

### Automated

**Verified.** pytest **1167 passed**; `build_check.py` **39 assertions, 0
problems**; **ruff** clean; **mypy** clean.

### Functional, driven through the real UI at 390px

**Verified — 15 of 15.** Team Rickie card absent with no teams and with a real
team; empty state reads "Teams are optional" with both Create and Join; the
join form opens with an invite-code input; the section subtitle switches
between "Optional — StreakFit works on your own" and "Who you're building this
with"; a real team renders exactly one card; the Ask Rickie button opens the
coach panel with a working input and thread; invite code well-formed
(`WK8PJN`); Rickie is 64px with `pointer-events: none`.

Two of those initially failed and **both were faults in my own probes**, not
the app — recorded because a future reader will otherwise see them in the logs:

1. `#coach-panel` — the element is `.coach-panel` (a class). Re-probed
   correctly: panel opens, visible, input present, thread present.
2. A whole run of teams checks failed with `GET /api/teams → 422` because I
   launched the script with `JWT_SECRET_KEY=y` while the server runs
   `localdev-jwt-not-real`. Tokens minted by the script did not verify.
   **Anything driving this server must use the same JWT key it was started
   with**, or every authenticated call returns 422 and looks like a product
   bug.

### Accessibility and layout, at 320 / 390 / 1280

**Verified:** no horizontal overflow at any width; every visible control is
keyboard-reachable; `:focus-visible` styling exists (13 rules in `style.css`).

**One pre-existing finding, not a regression:** `.settings-toggle` is 38x38 on
desktop. It is 38px at `4700708` as well and this release does not touch it. It
passes WCAG 2.2 AA (24px minimum) and fails the 44px touch guideline; it is a
desktop mouse target. **Not introduced here, not fixed here.**

### Reproduced defect — a contrast regression in the contrast commit

**Verified, and it is mine.** `ed72c66` tokenised the moderation filter's
active state from a hardcoded `#4338ca` to `var(--accent)` (`#6366f1`). White
text on the new colour is measurably worse:

| `.mod-filter.active`, white text on | ratio | AA (4.5:1 at 13.6px/700) |
|---|---|---|
| `#4338ca` — production `4700708` | **7.90:1** | passes |
| `#6366f1` — `var(--accent)`, this release | **4.47:1** | **fails by 0.03** |

Bold does not reach WCAG's large-text exemption until 18.66px, so 4.5:1 is the
applicable threshold. Every other measured element in the panel passes
(`#moderation-queue` 14.88:1).

**Severity: low.** It is the `/admin` moderation filter chip — an operator-only
surface with one user — and it is 0.03 short. **But the commit's stated purpose
was fixing contrast on that panel, so it is worth fixing before deploy rather
than after.** One-line change; `#4f46e5` would give 6.29:1.

**Not fixed — no approval to change application code.** Awaiting a decision.

---

## 3. Production readiness

### This release is migration-free — verified

- **0** files under `migrations/` differ between `4700708` and HEAD
- Alembic chain head **identical on both sides**: `47f7dc9962e3`
- No `db.Column` / `db.Model` / `__tablename__` / `Index` / `ForeignKey` /
  `op.` changes
- **No deploy-time configuration touched** — `render.yaml`, `requirements*.txt`,
  `runtime.txt`, `.python-version` all unchanged

### Release-specific rollback — code-only

`docs/operations/deployment-sequence.md` documents rollback as a **mandatory
two-part operation** (downgrade the database, then the code) because
`STREAKFIT_ENFORCE_DB_HEAD=1` makes gunicorn `SystemExit(1)` on a head
mismatch. **That warning is correct for migration-bearing deploys and does not
apply to this one.**

Because the chain head is unchanged, the guard cannot fire in either direction.
The rollback for this release is:

```
Render dashboard → streakfit-api → Manual Deploy → Deploy a specific commit
  → 4700708
```

- **Rollback commit: `4700708`** — verified live now via `/api/build-identity`
  (`4700708bca3b`, `gitBranch: main`)
- **Do NOT run `flask db downgrade`.** There is nothing to downgrade, and doing
  so would move production *off* the revision both builds expect — turning a
  clean rollback into an outage.
- Confirm the rollback by watching `static/sw.js` flip from `streakfit-v0922f`
  back to `streakfit-v0922`.

### Deployment procedure — a documentation discrepancy worth fixing

**Verified.** The live configuration and four documents disagree:

| source | says |
|---|---|
| `render.yaml` (matches dashboard) | `preDeployCommand: flask db upgrade` **+** `startCommand: STREAKFIT_ENFORCE_DB_HEAD=1 STREAKFIT_RETENTION_SWEEPER=1 gunicorn app:app` |
| `README.md:24`, `docs/runbooks.md:10`, `CLAUDE.md:26`, `docs/adrs/0010` | a single **Start Command** `flask db upgrade && … gunicorn app:app` |
| `docs/operations/phase-b-runbook.md` | correctly describes the Pre-Deploy arrangement |

Migrations moved to a Pre-Deploy Command and most of the documentation was not
updated. The failure mode is mild in one direction (running the upgrade twice
is a no-op) and caught in the other (`STREAKFIT_ENFORCE_DB_HEAD` refuses to
serve at the wrong revision), but four documents describing the single most
important operational command incorrectly is a real hazard for whoever rebuilds
the service under pressure. **Documentation-only fix; not made here.**

### The production database provider is not machine-confirmable

This is the gap I would most want closed, and it is worth being precise about
what is and is not known.

| class | evidence |
|---|---|
| **Configuration** | `render.yaml` declares `DATABASE_URL` with `sync: false` — set by hand in the dashboard, deliberately **not in the repo**. The repo therefore records nothing about the provider. |
| **Live read-only** | `https://streakfit.pro/api/build-identity` returns **`storageProvider: unknown`**. The running application does not report which database it is using. |
| **Documented** | `docs/runbooks.md` asserts Neon throughout — and warns, in its own words, that **there is more than one Neon project named `streakfit`**. |
| **Circumstantial** | The encrypted backups are named `streakfit-neondb-production-*`, which is evidence of what `DATABASE_URL` pointed at when the dump ran. |
| **Assumption** | That production is *currently* pointed at the intended Neon project. **Nothing machine-checkable confirms it.** |

The consequence is concrete: in an incident, the first question is "which
database is production actually using", and today it is answered from memory
and documentation rather than from the running system. A `storageProvider`
value populated from the connection host would close it without exposing a
secret. **Owner decision required; not implemented.**

### Backups and restore testing — genuinely good

**Verified by inspection** (no backup, restore or mutation was performed):

- Encrypted dumps exist at `~/backups/streakfit/`, mode `600`, GPG-encrypted,
  most recent **2026-09-22 10:27** (~16h before this audit)
- `streakfit-rehearse.sh` performs **real restore testing**: it decrypts the
  production dump into a throwaway in-RAM PostgreSQL 17, runs migrations
  against it, verifies, and destroys it — with **the plaintext never touching
  disk**, the container bound to `127.0.0.1`, and every outbound integration
  explicitly unset
- The downgrade path was **verified 2026-09-21** on disposable PostgreSQL 17,
  not assumed (`deployment-sequence.md:355`)

**Documented limitation, worth repeating:** `phase-b-runbook.md:464` records
that migration duration on real data is an **estimate** — the rehearsal ran
against an empty database. Not a concern for this release, which has no
migrations.

### Other gaps

The repo already maintains an honest register in
`docs/operations/production-readiness.md`. The ones that bear on this release:

- **No staging environment (P1).** `render.yaml` defines three services, all on
  `branch: main`. `docs/architecture/deployment.md`: *"merging to `main` **is**
  deploying to production."* This is why the phone test is on a LAN server.
- **No alerting on `/health`, 5xx rate, coach 503s or DB errors (P1).** After
  deploying, nothing tells you it went wrong except looking.

---

## 4. Phone test

> ### ⚠️ The server is NOT running — it must be restarted first
>
> It was verified working at the end of the audit (`/health` 200, `/` 200 over
> the LAN, admin 403, zero outbound connections) and was then **stopped by
> Claude Code at about 06:25 on 2026-09-23, because the machine ran critically
> low on memory while the session was idle** — 12 GiB of 15 GiB in use.
>
> That is a host-memory event, not a fault in the server, the audit or the
> release. Nothing about the findings below changes. The local test database
> survived intact (937,984 bytes) and a pre-restart backup is also held.
>
> **Ask Claude to restart it**, or run it yourself from
> `~/Desktop/Streakfit/integrate-product-completion`:
>
> ```bash
> FLASK_APP=app SECRET_KEY=localdev-secret-not-real \
>   JWT_SECRET_KEY=localdev-jwt-not-real \
>   ../streakfit_production_baseline/.venv/bin/python app.py
> ```
>
> Deliberately **no `ADMIN_SECRET`** — that is what keeps `/admin` fail-closed.
> Deliberately **no `DATABASE_URL`** — that is what keeps it on local SQLite.
> Closing other applications first would help; the machine was short on memory.

**URL: `http://192.168.1.61:5000`** — verified during the audit: `/health`
**200** and `/` **200** over the LAN, admin **403**, server outbound
connections **0**. Re-check the IP after restarting; it can change.

`http`, not `https`. Keep the `:5000`. Phone on the same Wi-Fi.

**This is the LOCAL server, not production.** Production is `streakfit.pro`,
still on `4700708`, untouched by this release. Nothing you do on the phone
against this URL can affect production or any real user's data.

**Account:** register a throwaway one in the app — any username, password 8+
characters. It exists only in the local SQLite file. Do not use a real password.

### Checklist

| # | Check | Expected | Failure symptom |
|---|---|---|---|
| 1 | Home screen background | Soft coloured wash, cards clearly readable | Flat grey/white, or colour so strong text is hard to read |
| 2 | Rickie appears | Within about half a second, 64px | Appears instantly **on top of** the mission text, or never appears |
| 3 | Watch Rickie ~30s | Wanders; may pass over text briefly; never comes to rest on it | Sits still on an exercise name, reps or "I did this" |
| 4 | Tap "I did this" behind Rickie | Button responds | Tap does nothing (he should not be able to intercept it) |
| 5 | Progress tab → "Add people" | Teams tab opens: "Teams are optional", Create + Join | A "🦝 Team Rickie" card — must NOT be there |
| 6 | Tap "Join a team" | Invite-code field appears | No field, or the form does not open |
| 7 | Create a team | Team card with its invite code | Error, or a Team Rickie card reappears |
| 8 | Tap "Ask Rickie" | Panel opens with a text box | Panel does not open. *A "coach unavailable" reply is expected locally — no API key* |
| 9 | Rotate to landscape | No horizontal scrolling, nothing clipped | Page scrolls sideways |
| 10 | Scroll the mission list | Smooth; Rickie stays out of the way | Stutter, or Rickie parked on content |

### LAN limitations — what this test cannot tell you

- **No service worker.** It needs HTTPS or `localhost`; over `http://` to an IP
  it will not register. The code is guarded (`if ('serviceWorker' in
  navigator)`) so nothing breaks — but **no PWA install prompt, no offline
  cache, and the `v0922f` cache bump is not exercised.** Assets are always
  fresh, which is convenient for testing and unlike production.
- **SQLite, not PostgreSQL.** Concurrency, connection pooling and any
  Postgres-specific behaviour are not exercised.
- **No AI coach, no email, no shared rate-limit backend** — those credentials
  are deliberately absent.
- **Headless testing does not replace this.** Everything in section 2 was
  measured in headless Chrome, which shares blind spots with the code it tests.
  Three checks in this project have already agreed with the bug they existed to
  catch. Your eyes on a real screen are the point.

---

## 5. Handoff

### Verified facts

1. Local server secured: no admin credential, admin 403, no production
   database, test data preserved, serving exactly this release.
2. pytest 1167 passed; build-check 39; ruff and mypy clean.
3. 15/15 functional UI checks at 390px.
4. No horizontal overflow, full keyboard reachability, focus styling present.
5. Release is **migration-free**; Alembic head identical to production.
6. Production is live on `4700708`; rollback is **code-only**.
7. Backups exist, encrypted, recent, with genuine restore rehearsal tooling.

### Reproduced defects

| | severity | introduced by |
|---|---|---|
| `.mod-filter.active` contrast 4.47:1 (was 7.90:1) | Low — admin-only, 0.03 short | **This release, `ed72c66`** |
| `.settings-toggle` 38x38 desktop | Low — passes AA, fails 44px touch guideline | Pre-existing at `4700708` |

### Confirmed readiness gaps

1. Production database provider not machine-confirmable (`storageProvider:
   unknown`).
2. Deploy command wrong in four documents.
3. No staging environment (documented P1).
4. No alerting (documented P1).
5. CHANGELOG has no entry for `v0922f` — its own convention is to key entries
   on the service-worker version, and the newest entry is `v0768`.

### Needs your decision

1. **Fix the `.mod-filter.active` contrast before deploying?** One line. I have
   not touched it.
2. **Populate `storageProvider`** so production can state its own database?
3. **Correct the four stale deploy-command documents?**
4. **Add a CHANGELOG entry for this release?**
5. **Rickie's mobile size stays 64px** — already decided, noted here so the
   phone test is judged against it rather than against "2x".

### Exact next action

**Test on your phone at `http://192.168.1.61:5000` using the checklist above.**
Then decide on the contrast fix, and whether to merge
`integrate-product-completion` → `main` and deploy.

Nothing is pushed. Nothing is deployed.

**The local server is stopped** — see the warning in section 4. It was left
running securely at the end of the audit and was reaped later by Claude Code
under host memory pressure. It needs restarting before the phone test, and I
did not restart it on my own because memory may still be short.

---

## 6. Real-phone test and final release preparation — 2026-09-23, afternoon

Evidence classes as in the header: **Owner-observed** (Tim, on his own Android
phone), **Verified** (Claude ran it and saw the result), **Documented**,
**Not verified**.

### 6.1 Real-phone test — PASSED

Local server at `http://192.168.1.61:5000`, started with the section 4 command
(no `ADMIN_SECRET`, no `DATABASE_URL`) from this worktree. It was stopped again
after the test; port 5000 was confirmed free.

**Owner-observed** — Android phone, new disposable local account `Humpty`:

- All five exercises completed; each button became a checkmark; 5/5; 1-day streak.
- Completion status survived a page refresh.
- The first-mission celebration messages stayed **above** the bottom
  Today/Progress navigation.
- Both navigation tabs remained visible and tappable while messages showed.
- The messages disappeared normally.
- Earlier the same day, with `Johnny`, the owner completed a 5/5 on the phone
  (the run that exposed the placement defect fixed in `0c5fcde`).

**Verified by Claude:**

| check | result |
|---|---|
| `/api/build-identity` on the phone-test server (owner ran the curl; output read by Claude) | `gitSha 0c5fcde4f7ef`, `environment: development`, `migration.atHead: true`, `latest 47f7dc9962e3`, `featureFlags.coach: false` |
| Local SQLite, read-only, after the test | `Humpty` (id 379): **5** `daily_completion` rows for 2026-09-23 — floor_tricep_dip, bird_dog, thoracic_rotation, calf_raise, low_skip |
| Same, `Johnny` (id 332) | **5** rows, unchanged from before the restart |
| Local DB backed up before the restart | copy in the session scratchpad |

**Not verified:**

- The one owner screenshot shows the final toast at the bottom of the viewport
  but **does not show the navigation bar**, so placement rests on the owner's
  direct observation, not on the image.
- `/admin` returning 403 on the restarted server was not re-checked (command
  permissions were denied mid-session). The server is now stopped, so this no
  longer has any exposure.
- Service worker, PWA install and the `v0923b` cache bump — not exercised over
  `http://` to a LAN IP (section 4, LAN limitations).

### 6.2 What would ship

**Verified.** Branch `integrate-product-completion`, clean tree. `main` and
`origin/main` are both `4700708` (fetched today); HEAD is a strict descendant,
so the merge is a **fast-forward**, no merge commits.

| commit | kind | summary |
|---|---|---|
| `ed72c66` | app | Team Rickie card removed; teams optional; visual tokens |
| `c40d837` | app | Rickie no longer stands on the mission during load |
| `d38ac6f`, `292153b`, `e94487e` | docs | `RICKIE_3D_PLAN.md` — planning only, nothing implemented |
| `52519bf`, `b57ac37` | docs | this audit |
| `5e51274` | app | a completion that did not save says so; no sticky purple hover |
| `0c5fcde` | app | celebration toasts sit above the section nav |
| *(this commit)* | docs | section 6 |

Application files changed vs `4700708`: `app.py`, `static/{admin.html, app.js,
index.html, rickie-roam.js, style.css, sw.js}`. `app.py` changes are comments,
Rickie's system-prompt text about teams, and invite codes generated with
`secrets` instead of `random` (a security improvement). Plus tests,
`scripts/uicheck.py` and `scripts/verification/`.

pytest re-run today at `0c5fcde`: **1167 passed** (includes
`tests/test_migrations.py`). Not re-run today: `uicheck` (225/225 on the
second full run at commit time), `verify_all` (181/181 locally at commit time).

### 6.3 Migrations and production data — none

**Verified.** 0 files under `migrations/` differ from `4700708`; no
`db.Column`/`db.Model`/`__tablename__`/`Index`/`ForeignKey`/`op.` changes;
`render.yaml`, `requirements*.txt`, `runtime.txt`, `.python-version`
unchanged. Local database stamped `47f7dc9962e3`, the same head production
reported during the overnight audit. No code path writes data differently;
no backfill, no script, no data change is part of the release.

### 6.4 Moderation contrast — STILL UNRESOLVED

**Verified today.** `static/admin.html:110`, `.mod-filter.active` is still
`background: var(--accent)` with `--accent: #6366f1` and white text:
**4.47:1**, below 4.5:1 by 0.03. No dark-mode override. Introduced by
`ed72c66`; production (`#4338ca`) is 7.90:1. `#4f46e5` would give 6.29:1.
Admin-only surface, one operator. Shipping it is a small accessibility
regression on an internal page; fixing it is a one-line app change that needs
owner approval.

### 6.5 Intermittent Rickie roaming check — NOT reproduced as an app defect, NOT ruled out

**Documented** (previous session transcript, 2026-09-23 ~13:17): the first
full `uicheck` run on the `0c5fcde` fix failed 1 of 225 —
`he steps aside when content appears under him, without a scroll — stayed at 206,29`.
Run alone it passed 3 of 3; a second full run passed 225/225. A comparison run
against the previous code was **not** performed (permission denied), so it is
unknown whether this predates the release.

**Assessment from reading the code (not reproduced):**

- The failing check plants a fixed-position block exactly on Rickie and waits
  up to 6s for him to move. It does not involve toasts, so `0c5fcde` is an
  unlikely cause.
- `stepAsideNow()` (`static/rickie-roam.js:405`) returns without moving when
  `somewhereClear()` finds **no** clear spot (`if (!spot) return;`). A
  real-but-rare path therefore exists in which he stays on newly-arrived
  content — contrary to the file's own comment that he "leaves, by slipping
  off an edge" when the band is busy. It also returns early while `paused`,
  `suspended` or `walking` (walk arrival re-checks).
- The check's own history says an earlier one-in-four flake **was** a real
  defect. So this should be treated as a possible real defect of low
  severity, not dismissed as test noise.

**User impact if real:** cosmetic. Rickie is 64px with `pointer-events: none`
(verified overnight), so a tap on content under him still reaches it; he can
visually cover a few words until he next moves.

**To settle it (not done, not authorised):** run `check_rickie_roams` ~20×
on `0c5fcde` and ~20× on `4700708`, logging `somewhereClear()` and the
early-return reason on failure.

### 6.6 Deployment and rollback — appropriate as documented, with one caveat

**Deploy.** The live Render configuration (documented, owner-verified
2026-09-22): Auto-Deploy **Off**; Pre-Deploy `flask db upgrade`; Start
`STREAKFIT_ENFORCE_DB_HEAD=1 STREAKFIT_RETENTION_SWEEPER=1 gunicorn app:app`.
For this release the pre-deploy upgrade is a no-op (already at head) and the
head guard cannot fire, because the chain is unchanged.

**Rollback is code-only** (section 3): redeploy `4700708`. **Do not run
`flask db downgrade`** — the two-part rollback in `deployment-sequence.md`
applies to migration-bearing deploys, not this one.

**Caveat.** A fresh backup is not strictly required (no schema or data change),
but the newest encrypted dump is `2026-09-22 10:27` (~28h old). Taking one
before the deploy is cheap insurance.

**Not verified today:** that production is still on `4700708` — the read-only
GET was denied this session. Last verified during the overnight audit. The
first step of the proposed sequence re-checks it.

### 6.7 Remaining blockers and risks

**Release blockers: none found** — provided the owner accepts or fixes 6.4.

**Regressions introduced by this release:**

| item | severity | decision |
|---|---|---|
| `.mod-filter.active` 4.47:1 (6.4) | Low, admin-only | fix before deploy, or accept |
| Possible rare Rickie step-aside failure (6.5) | Low, cosmetic, unconfirmed — may predate the release | accept and track, or investigate first |

**Existing production risks, not introduced or worsened here:**

- **Security — shared rate-limit storage (Phase C2) still not done.**
  `ratelimit.shared_storage` FAIL; limits are `memory://`, per-worker and
  reset on every deploy — including this one. This is the control in front of
  invite-code lookup. (This release *improves* the same area: invite codes now
  come from `secrets`.)
- **Security/UX — sessions are a fixed 1-hour JWT with no refresh and no
  "log out everywhere"**; a tap after expiry is lost (investigated
  2026-09-23, options proposed, no decision yet).
- **Durability — production database provider not machine-confirmable**
  (`storageProvider: unknown`).
- **Durability — newest encrypted backup ~28h old** (6.6).
- **Operations — no staging** (merge to `main` is the production code
  path) and **no alerting** on `/health`/5xx.
- **Docs — deploy command wrong in four documents** (section 3).
- **Unexplained — why the first phone taps never reached the server** is still
  unproven; `5e51274` now makes that failure visible instead of silent.
- `.settings-toggle` 38×38 on desktop (pre-existing).

### 6.8 Proposed sequence — NOT EXECUTED, awaiting owner approval

Run from `~/Desktop/Streakfit/integrate-product-completion`. `main` is not
checked out in any worktree, so no branch switching is needed.

**0 · Decisions first.** Contrast fix (6.4) yes/no; accept 6.5 or investigate.
Any code change resets this sequence to "re-test".

**1 · Pre-flight (read-only)**

```bash
git status --short                    # must be empty
git fetch origin
git rev-parse --short origin/main     # must be 4700708
git merge-base --is-ancestor origin/main HEAD && echo fast-forward-ok
curl -s https://streakfit.pro/api/build-identity   # gitSha 4700708…, migration.atHead true
curl -s https://streakfit.pro/static/sw.js | head -1   # streakfit-v0922
```

**2 · Fresh encrypted backup (recommended)** — the owner's usual
`~/backups/streakfit/streakfit-backup.sh` procedure. Claude does not handle
the passphrase.

**3 · Merge (fast-forward only) and push**

```bash
git push origin HEAD:refs/heads/main   # refused by git if not a fast-forward; no --force
git fetch origin main:main             # bring the local main ref along
```

**4 · Deploy** — Render dashboard → `streakfit-api` → Manual Deploy →
**Deploy latest commit** (the SHA from step 3). Watch the log: Pre-Deploy
`flask db upgrade` should report nothing to do; gunicorn starts.

**5 · Verify**

```bash
curl -s https://streakfit.pro/health                   # 200
curl -s https://streakfit.pro/api/build-identity       # new gitSha; atHead true; appliedCount 25
curl -s https://streakfit.pro/static/sw.js | head -1   # streakfit-v0923b
python scripts/verify_all.py                           # production by default; qa_smoke_* accounts only
```

Then on the phone at `https://streakfit.pro`: close and reopen the app (or
reload twice) so the service worker picks up `v0923b`; confirm no Team Rickie
card, Rickie not parked on the mission, and — on an account that has not done
today's mission — the celebrations sit above Today/Progress.

**6 · Rollback, if needed** — Render → Manual Deploy → **Deploy a specific
commit → `4700708`**. Do **not** run `flask db downgrade`. Confirm
`/api/build-identity` shows `4700708` and `sw.js` reads `streakfit-v0922`.
`origin/main` then sits ahead of production; decide afterwards whether to
revert on `main` or fix forward.
