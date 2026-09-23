# Release audit — `ed72c66` + `c40d837`, overnight 2026-09-22/23

Autonomous audit prepared for the owner's phone test. **No application code,
committed tests, production configuration or migrations were modified. Nothing
was pushed, merged or deployed. No production data was read or written beyond
two unauthenticated GETs of public endpoints.**

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

**URL: `http://192.168.1.61:5000`** — re-verified at the end of this audit:
`/health` **200** and `/` **200** over the LAN, admin **403**, server outbound
connections **0**.

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

Nothing is pushed. Nothing is deployed. The server is left running securely.
