# Production hotfix — login identifier exposed to teammates

**Branch:** `hotfix/username-exposure` · **Base:** `8b409d1` (`origin/main`)
**Commits:** `5f8495c`, `76f194d` · Nothing pushed, nothing deployed.

This branch is **two commits on top of what production is running**. It has no
relationship to `product-completion` and must not be confused with it — see §8.

---

## 1. The production revision, established rather than assumed

Production has **no `/api/build-identity`** (404) — that endpoint is itself
part of the undeployed work. It answers only `/health`. So the revision was
fingerprinted against behaviour:

| Probe | Production | `origin/main` = `8b409d1` |
|---|---|---|
| `/health` | 200 | present |
| `/api/health` | 404 | absent |
| `/api/build-identity` | 404 | absent |
| `/api/verification/self` | 404 | absent |
| team photo routes | absent | absent (0 occurrences) |
| roster field | `username` | `username` |

**Production is `8b409d1`.** The hotfix branch is cut from exactly that.

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

**Migration requirements: none.** Deploy is a code push. The existing
`flask db upgrade && STREAKFIT_ENFORCE_DB_HEAD=1 gunicorn app:app` start
command is unchanged and the upgrade will be a no-op.

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

### Verifying after deployment

1. `curl https://streakfit.pro/health` → `{"status":"ok"}`.
2. From a real account in a real team, `GET /api/teams/<id>` — every
   `members[].name` reads `Member N` and **no value anywhere equals a login**.
3. `GET /api/teams/<id>/moments` — `display_text` reads *"Member 1 created the
   team"*.
4. `GET /api/teams/<id>/messages` — `sender_username` is a label;
   `sender_user_id` is present.
5. Run the suite against production:
   `SMOKE_BASE_URL=https://streakfit.pro python scripts/verify_all.py`.
   Expect `teams.roster_carries_no_login_identifier`,
   `moments.history_carries_no_login_identifier`,
   `chat.read_carries_no_login_identifier` and
   `chat.post_echo_carries_no_login_identifier` to pass. It only ever creates
   throwaway `qa_smoke_*` accounts.
6. Hard-reload the app and confirm the roster renders and chat still shows your
   own messages on your side.

## 7. Rollback

Code-only, so rollback is a redeploy of the previous revision:

```
git checkout 8b409d1 && <redeploy>
```

No migration to reverse, no data written, no backfill to undo. The service
worker will serve `v0748` again on the next load. **Rolling back reinstates the
exposure** — so it is the right move for an unrelated outage, and the wrong one
for "the roster looks anonymous". For that, ship a display name instead.

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
