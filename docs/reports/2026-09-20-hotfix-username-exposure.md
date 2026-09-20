# Production hotfix — login identifier exposed to teammates

**Branch:** `hotfix/username-exposure` · **Base:** `8b409d1` (`origin/main`)
**Commits:** `5f8495c`, `76f194d`, `676ebbf` (+ this update) · Nothing pushed, nothing deployed.

This branch is **three commits on top of the production baseline**. It has no
relationship to `product-completion` and must not be confused with it — see §8.

---

## 1. The deployment baseline — the Render record is UNVERIFIED

**I could not read the live Render deployment record.** There is no `render`
CLI on this machine, no `RENDER_API_KEY` or any Render credential in the
environment or in `.env` (which holds only `ANTHROPIC_API_KEY`, `SECRET_KEY`,
`JWT_SECRET_KEY`), no `~/.render` config, and no Render MCP connector in this
session. `render.yaml` in the repo is **explicitly inert** — its own header
says the live service is configured in the Render dashboard, was not created
from a Blueprint, and that Render never reads the file.

**So the deployed SHA is reported as UNVERIFIED, not assumed.** What follows
bounds it rather than guessing it.

### Evidence gathered instead

**Byte-exact static-asset comparison** (read-only GETs, no side effects) —
production's served bundle against the git blobs at `8b409d1`:

| Asset | Live SHA-256 | `8b409d1` | Result |
|---|---|---|---|
| `/static/sw.js` | `d68a5d9add82ce8f…` | `d68a5d9add82ce8f…` | **MATCH** (2,124 B) |
| `/static/app.js` | `f75631d279b3ddb9…` | `f75631d279b3ddb9…` | **MATCH** (208,021 B) |
| `/static/style.css` | `99bad6078ecf9a49…` | `99bad6078ecf9a49…` | **MATCH** (67,086 B) |

A byte-identical 208 KB `app.js` is strong evidence, but it pins the *frontend*
bundle, not the backend commit.

**Supporting signals:** production 404s `/api/health`, `/api/build-identity`
and `/api/verification/self` and serves only `/health`; it has no team-photo
routes; its roster emits `username`. `origin/main` is `8b409d1`, last moved
**2026-07-26** (~8 weeks ago, consistent with "maintenance mode"), and the live
service is git-linked to `main` with auto-deploy.

### What the ambiguity actually costs — nothing, for this hotfix

The last commit on `main` touching `static/` is `bdc1a50`. Every revision from
`bdc1a50` to `8b409d1` — **12 commits** — serves a byte-identical static
bundle, so the asset match cannot discriminate between them. That is the honest
size of the uncertainty.

It does not matter here. **Every function this hotfix modifies is byte-identical
across all 12 candidates**, verified by AST-extracting each one at both ends of
the window and hashing it:

| Function | `bdc1a50` | `8b409d1` | Same |
|---|---|---|---|
| `get_team` | `d7ad3a5e4233` | `d7ad3a5e4233` | ✓ |
| `get_team_moments` | `623b19c1ec3a` | `623b19c1ec3a` | ✓ |
| `get_team_messages` | `12e284c53992` | `12e284c53992` | ✓ |
| `post_team_message` | `6646823ca22a` | `6646823ca22a` | ✓ |
| `_serialize_team_message` | `6f1f0fcf5cad` | `6f1f0fcf5cad` | ✓ |
| `_usernames_for_ids` | `586e45bdfffe` | `586e45bdfffe` | ✓ |
| `_moment_display_text` | `34182091a931` | `34182091a931` | ✓ |

The only `app.py` changes anywhere in that window are two rate-limit decorator
keyings on `join_team` and `post_team_message` (`@limiter.limit(...,
key_func=user_or_ip_key)`), which the hotfix does not touch.

**Conclusion:** the deployed SHA is **unverified**; the deployed revision is
**almost certainly `8b409d1`** and **provably within `bdc1a50..8b409d1`**; and
the hotfix applies identically to any revision in that range.

**To close this properly**, one of: the Render dashboard → the service →
**Events** (shows the deployed commit), or `RENDER_API_KEY` in the environment
so `GET /v1/services/<id>/deploys` can be read. **If the deployed SHA turns out
to be outside `bdc1a50..8b409d1`, stop — do not rebase or adapt this branch
without a fresh review.**

## 2. What was exposed, and where

`/api/login` takes `username`. **Four** team-facing serializers were sending
that field to every other member of the team:

| Endpoint | Field |
|---|---|
| `GET /api/teams/<id>` | `members[].username` |
| `GET /api/teams/<id>/moments` | `subject_username`, and `display_text` — *"timhill created the team"* |
| `GET /api/teams/<id>/messages` | `sender_username` |
| `POST /api/teams/<id>/messages` | `sender_username` in the echo |

The POST echo takes a different branch of the serializer from the GET list, so
a fix applied to the batch path alone would have left a live leak behind a
rarely-taken branch. Both are covered.

**Impact.** A teammate is handed half of somebody's credentials, and the
account is confirmed to exist before anyone starts guessing passwords. Invite
codes are shared out of band, so a private team of eight is not eight trusted
people.

### The roster was not the only path — and these were checked, not assumed

Audited every route at `8b409d1` and every use of `.username`:

- `GET /api/teams` (list) — **clean**, no member names at all.
- `GET /api/teams/lookup/<code>` — **clean**, name and counts only.
- `GET /api/teams/<id>/campfire` — **clean**, totals and stage only.
- `POST/DELETE` team routes — **clean**.

**Out of scope, found while auditing, both operator-facing rather than
peer-facing.** Reported, deliberately not fixed in a security hotfix:

- `app.py:1561` — the admin dashboard lists usernames. That is the site
  operator looking at their own users, behind an admin secret.
- `app.py:3321` — Rickie's context block sends `- Name: {user.username}` to
  the model. That is the user's **own** login, sent about themselves. It is a
  real defect (it is why `_safe_display_name` exists on `product-completion`)
  but a different one, and it is not a teammate seeing it.
- `app.py:3641` — the server-side deletion report. Operator tool.

## 3. Stored announcement content — nothing to clean up

The brief asked specifically about stored announcements. Checked two ways:

- **By reading the schema.** `TeamMoment` stores `moment_type`,
  `subject_user_id` (a foreign key) and `moment_metadata`. `display_text` is
  **computed at read time** and never persisted. `moment_metadata` is only ever
  `{"total_team_missions": n}` or `{"stage": "..."}`. `TeamMessage.body` for
  Rickie posts comes from fixed templates that name nobody.
- **By querying the database** after a full verification run: of 5 stored
  messages and 5 stored moments, **0 rows contained any username**.

**Therefore: no data migration, and no production data is touched.** The leak
was entirely in the read path, which is why a code-only deploy fully closes it.

## 4. The fix

A per-team ordinal label — `Member 1`, `Member 2` — computed from join order
with the creator first. Stable across requests, meaningless outside the team,
derived from position rather than from anything about the person.

**It never falls back to the username.** This is the one thing worth being
explicit about, because the obvious fix is worse than it looks: a helper that
prints the login when it "looks like a real name" and hides it otherwise still
exposes `olivia`, `timhill`, `sarahjones` — the common case, not an edge case.
`product-completion` shipped exactly that mistake first and had to correct it
after an adversarial review broke it on `timhill`. The hotfix does not repeat
it, and the tests are built from the names that would defeat it.

**There is no display name to substitute.** `8b409d1` has no `display_name`
column — zero occurrences. Adding one would need a migration *and* a UI to set
it, and until people set it every member would read `Member N` anyway. So the
migration would buy nothing today and cost schema risk. Deliberately omitted.

### Precise change set

| File | Change |
|---|---|
| `app.py` | `_team_member_labels()` added; four serializers switched to it; `_usernames_for_ids` kept for own-account/admin/deletion paths and documented as never-for-peers |
| `static/app.js` | roster reads `m.name`; **chat self-detection compares `sender_user_id` to `currentUser.id`** — it used to compare usernames, which no longer exist on the wire |
| `static/sw.js` | cache `v0748` → `v0749` (mandatory for any `static/` change) |
| `tests/test_username_exposure.py` | **new**, 10 tests |
| `tests/test_team_moments.py`, `tests/test_team_chat.py` | 6 assertions that encoded the old behaviour now assert its absence |
| `scripts/verification/{teams,moments,chat}.py` | 4 suite checks rewritten, 4 added |

**No migration. No model change. No column touched.**
`git diff origin/main -- migrations/` is empty; the diff contains zero
`db.Column` lines.

## 5. Verification

| Gate | Result |
|---|---|
| `pytest` | **177 passed, 0 failed** |
| `scripts/build_check.py` | **32 assertions, 0 problems** |
| `verify_all` against a server built from this branch | **86 passed, 0 failed** |
| `node --check static/app.js` | OK |

**Fault injection — the part that makes the tests worth anything.** With
`app.py` reverted to the production baseline and the new tests left in place:
**9 of 10 fail.** The tenth is the control asserting your own username is still
yours to see, which the fix does not change. Every exposure test fails when the
bug is present.

**The usernames in the pytest suite are person-shaped on purpose** —
`timhill`, `oliviahill`, `sarahjones`. The verification suite's `qa_smoke_*`
accounts are machine-shaped, so they would pass against a heuristic fallback
that still leaked every real family's names. The pytest layer is what covers
that case; the suite layer covers the deployed-shape case. Neither substitutes
for the other.

## 6. Deployment

### What is and is not required

| | Required? |
|---|---|
| Database migration | **No.** `git diff origin/main..HEAD -- migrations/` is empty; the diff contains zero `db.Column` lines |
| Environment variable change | **No.** No new setting is read; `render.yaml` and `requirements.txt` untouched |
| Service restart | **Yes, implicitly** — Render restarts on deploy. No manual restart step |
| Production data modification | **No.** Nothing stored holds a username (§3); the leak was entirely in the read path |
| Start-command change | **No.** `flask db upgrade && … gunicorn app:app` is unchanged; the upgrade is a no-op |

### Steps, this hotfix alone

1. **Confirm the deployed SHA first** (§1 — currently unverified). Render
   dashboard → service → **Events**. If it is not within `bdc1a50..8b409d1`,
   **stop**.
2. Push `hotfix/username-exposure` and merge it to `main` — **fast-forward
   only**. It is 3 commits on top of `8b409d1`, so a fast-forward is possible
   iff the deployed baseline is in fact `8b409d1`. **A merge commit or a rebase
   would pull in work that is not in this branch; do not force either.**
3. Auto-deploy fires on push to `main`. Watch the Render build log.
4. **Confirm what actually deployed** — production has no build-identity
   endpoint to ask, so use the asset fingerprint:
   ```
   curl -s https://streakfit.pro/static/sw.js | grep -o 'streakfit-v[0-9]*'
   ```
   Expect **`streakfit-v0749`** (it serves `v0748` today). Cross-check the SHA
   in Render → Events against the merged commit.

### Risks, honestly ranked

1. **The roster stops showing names — this is the real cost.** Members become
   `Member 1`, `Member 2`. For a family team this is a genuine downgrade, and
   it is the direct consequence of there being no display-name column to fall
   back to. **This is the trade the owner is being asked to accept:** an
   anonymous roster now, versus a login identifier on every roster until
   `product-completion` ships a display name. It is reversible in minutes
   (§7) and it discloses nothing.
2. **Cached clients.** A browser still holding the old `app.js` reads
   `m.username`. Commit `76f194d` keeps that key populated with the *same
   label*, so an old client shows `Member 2`, not `undefined (Creator)` —
   confirmed empirically against a running build. The `sw.js` bump
   (`v0748`→`v0749`) pulls the new bundle on next load. Chat self-alignment is
   briefly degraded for those clients (own messages render as someone else's)
   — cosmetic, no leak, resolves on reload.
3. **Unverified baseline** (§1). Bounded, not eliminated.
4. **Low blast radius otherwise.** No schema, no data, no auth, no billing,
   no background jobs. Four read serializers and one JS file.

### Risks, honestly ranked

1. **The roster stops showing names — this is the real cost.** Members become
   `Member 1`, `Member 2`. For a family team this is a genuine downgrade, and
   it is the direct consequence of there being no display-name column to fall
   back to. **This is the trade the owner is being asked to accept:** an
   anonymous roster now, versus a login identifier on every roster until
   `product-completion` ships a display name. It is reversible in minutes
   (§7) and it discloses nothing.
2. **Cached clients.** A browser still holding the old `app.js` reads
   `m.username`. Commit `76f194d` keeps that key populated with the *same
   label*, so an old client shows `Member 2`, not `undefined (Creator)`. The
   `sw.js` bump pulls the new bundle on next load. Chat self-alignment is
   briefly degraded for those clients (own messages render as someone else's)
   — cosmetic, no leak, resolves on reload.
3. **Low blast radius otherwise.** No schema, no data, no auth, no billing,
   no background jobs. Four read serializers and one JS file.

### Post-deployment checklist — and how it avoids logging real people

**The four previously leaking paths must be checked without putting any real
user's login into a terminal, a log or a report.** Two rules make that work:

- **Assert shape, never print values.** A label matches `^Member \d+$`. That is
  a complete check and it discloses nothing. Never paste a roster into a
  report.
- **Prove absence using accounts you created.** `verify_all` builds throwaway
  `qa_smoke_*` accounts, so it *knows* the logins it is searching for and can
  assert they are absent — without ever handling a real one.

| # | Path | Check | Expected |
|---|---|---|---|
| 0 | — | `curl -s https://streakfit.pro/health` | `{"status":"ok"}` |
| 1 | — | `curl -s .../static/sw.js \| grep -o 'streakfit-v[0-9]*'` | `streakfit-v0749` |
| 2 | roster | `GET /api/teams/<id>` — every `members[].name` matches `^Member \d+$` | all match |
| 3 | history | `GET /api/teams/<id>/moments` — every `display_text` matches `^(Member \d+\|A member\|The (team\|campfire)) ` | all match |
| 4 | chat list | `GET /api/teams/<id>/messages` — every user message's `sender_username` matches `^Member \d+$` and `sender_user_id` is non-null | all match |
| 5 | chat echo | `POST /api/teams/<id>/messages` — same shape on the 201 body | matches |
| 6 | suite | `SMOKE_BASE_URL=https://streakfit.pro python scripts/verify_all.py` | `teams.roster_carries_no_login_identifier`, `moments.history_carries_no_login_identifier`, `chat.read_carries_no_login_identifier`, `chat.post_echo_carries_no_login_identifier` all PASS |
| 7 | UI | Hard-reload; roster renders, chat still shows your own messages on your side | no `undefined`, correct alignment |

Steps 2–5 can run against a real team the operator is a member of, because the
assertion is a regex over shape. **If any check fails, capture the failing
field name and the regex — not the value.**

Note on step 6: `verify_all` creates throwaway accounts and disposable teams,
touches no existing user or team, and is documented as safe against production
at any time.

## 7. Rollback

Code-only, so rollback is a redeploy of the previous revision — in Render,
**Events → the previous deploy → Rollback**, or:

```
git revert 676ebbf 76f194d 5f8495c   # then push to main
```

No migration to reverse, no data written, no backfill to undo, no environment
variable to restore. The service worker returns to `v0748` on next load.

> ### Rollback is NOT a privacy-safe resolution
>
> **Rolling back reinstates the exposure.** The previous revision is the
> vulnerable one: it puts every member's login identifier back on the roster,
> in the team history, and in the chat — which is the defect this deploy
> exists to remove.
>
> Rollback is therefore appropriate **only** for an unrelated production
> failure that this deploy happens to have introduced, and it must be treated
> as *reopening a known privacy defect*, not as returning to a safe state.
>
> It is **not** the answer to "the roster looks anonymous now." That is the
> intended, documented trade (§6.1). The fix for that is to ship a display
> name — never to restore the login.

## 8. This is NOT `product-completion`

| | this hotfix | `product-completion` |
|---|---|---|
| Base | `8b409d1` (live) | 114 commits of unshipped work |
| Commits | 2 | 114 |
| Migrations | 0 | includes `08681a9bd9f9` (age bands, guardian links) |
| Scope | one defect | teams v2, photos, child-safety foundations, content store, Rickie 2.0 |
| Verified | 177 pytest / 86 suite, fault-injected | green, but carries unreviewed content and unreachable child-safety code |

**They must not be deployed together, and this branch is not a subset of that
one** — it solves the same defect differently, because production has no
`display_name` column to use.

**When `product-completion` ships**, delete the deprecated `username` compat
key from the roster (that branch reads `name`) and let `_peer_names_for_ids`
supersede `_team_member_labels`. The wire contract already matches, so that is
a helper swap rather than a redesign.

## 9. Recommended follow-ups, not in this hotfix

1. **Ship a display name** so the roster can show names again — the real fix
   for the cost in §6.1.
2. **Rickie's context block** (`app.py:3321`) still sends the user's own login
   to the model.
3. **The admin dashboard** lists usernames — appropriate for an operator, worth
   a conscious decision rather than an accident.

---

**Awaiting explicit approval before any production action.** Nothing has been
pushed, merged, deployed or backfilled, and no user has been contacted.
