# StreakFit — Release History

StreakFit has **no semantic-versioning scheme**, and `app.py` says so explicitly: the commit
SHA is the honest identity of a build until a scheme exists. So this file is keyed on what
production actually exposes — the **service-worker cache version** in `static/sw.js`, which is
bumped whenever shipped static assets change and is the signal used to confirm a Render deploy
went live. Where the deployed commit is known it is named alongside.

Deploys are git-push-triggered on Render (`git push origin main`, ~56s). Entries are newest
first. Anything before v0741 lives in `git log`; the narrative reasoning behind the product
decisions lives in `PROJECT_JOURNAL.md`.

---

## Pending deploy — SW `v0748` (10 commits on `main`, not yet pushed)

Prepared 2026-07-25. Production is still serving `v0747`. Two groups of work:

### Release-candidate hardening (2026-07-25, this pass)

- **`pytest tests/` could not run at all.** `tests/conftest.py` imports `app`, but pytest's
  prepend import mode puts a test file's basedir (`tests/`) on `sys.path`, never the rootdir —
  so the exact command in `.github/workflows/ci.yml` and `make test` died at collection with
  `ModuleNotFoundError: No module named 'app'`. It only looked green locally because the suite
  was being run as `python -m pytest`, which implicitly prepends the cwd. Since the CI workflow
  was still unpushed and had never executed, this would have failed on the first push to main.
  Fixed with `pytest.ini` (`pythonpath = .`).
- **`make setup`, the documented one-command setup, failed two ways** on a clean machine: the
  venv recorded the interpreter's PATH *name* as its home (fatal `No module named 'encodings'`
  with a symlinked launcher such as uv-managed CPython), and the `env` target treated `.env` as
  all-or-nothing, so a partial `.env` skipped secret generation and `make db` died on the
  `RuntimeError` `app.py` raises at import.
- **Added the three quality gates the project had none of** — `ruff` (defect-focused rule set),
  `mypy` (scoped to `app.py`), and `scripts/build_check.py`, a stand-in for the build step a
  bundled app would get: every asset referenced by `index.html`/`app.js`/`style.css`/`sw.js`/
  `manifest.json` must exist, the service-worker precache list must resolve, the cache name must
  carry a version token, all JS and JSON must parse, `app.py` must import under
  production-shaped env, and all 90 exercise illustrations promised by the mission API must
  exist. Wired into CI and `make check`.
- **Mobile layout of Today's Mission repaired**, and **14 tap targets raised to 44px**, this
  stylesheet's own established minimum — including the Log In / Register tabs on the first
  screen a new user touches, and the exercise modal's ✕, which was the only way to dismiss it.
- Real defects found by the new linter and fixed: a computed-then-discarded `fav_category`, an
  `int` default handed to `os.environ.get`, and a startup `SystemExit` raised inside `except`
  without `from exc`, which dropped the underlying cause.

### Custodian audit + docs (2026-07-25, earlier session — carried in the same undeployed set)

- **Security dependency bumps:** gunicorn 21.2.0 → 23.0.0 (CVE-2024-6827, CVE-2024-1135,
  request smuggling) and Werkzeug 3.0.1 → 3.0.6 (CVE-2024-34069).
- Maintainer documentation: `docs/architecture/` (8 files), `docs/adrs/` (ADR-0001..0010),
  `docs/api/openapi.yaml`, `docs/operations/`, `docs/engineering-roadmap.md`.
- Release tooling: `.env.example`, `Makefile`, `.github/workflows/ci.yml`, `requirements-dev.txt`,
  and a proposed (inert) `render.yaml`. Pinned `anthropic==0.120.0`, added `alembic==1.18.5`.
- Resolved the gunicorn `$PORT` question: the live bare `gunicorn app:app` is correct — gunicorn
  defaults to `0.0.0.0:$PORT` when `PORT` is set, and Render sets it. No `--bind` needed.
- `docs/memory_pipeline.md` corrected to describe the atomic persistence flow.

---

## v0747 — Rickie cross-session memory (`6328a91`, 2026-07-24)

Rickie remembers across sessions: `coach_turn` (server-owned rolling last-10; `coach()` loads
history from the DB and ignores client-sent history) plus `coach_note` (one per user, populated
only by deterministic regex over the user's own explicit statements, never by Rickie, injected
as non-recited background). `DELETE /api/coach/memory` and a Settings "Forget our conversations"
control wipe both. Migration `q1r2s3t4u5v6`.

Also in this window (code-only, no migration, no new env var): the robustness marathon — atomic
coach persistence, `CoachNote` first-write race safety, N+1 batch fixes for team moments /
messages / members, security hardening (`hmac.compare_digest` for the admin secret, security
headers, `MAX_CONTENT_LENGTH`, login timing), a thread-safe and size-bounded weather cache, and
a reusable `delete_user_account` service. One bounded Rickie tool: `get_weather` via Open-Meteo
(stdlib urllib, no key, no stored location). Prod smoke 12/12.

## v0746 — First-minute polish (`f4a87a4`, 2026-07-23)

Notification prompt moved below Today's Mission; Ask Rickie and how-to/tips chips raised to
44px tap targets; real `:focus-visible` ring on the difficulty and Rickie selects; Brain Boost
and Insight teasers shown pre-completion so they read as discoverable rather than locked.

## v0745 — Eliminate trust breakers (`a519c7d`, 2026-07-23)

- **Migration bootstrap fixed.** The baseline migration had shipped as a no-op — nothing in the
  chain ever created the `user` or `challenge` tables, so a fresh database could not be built
  from migrations at all. Backfilled baseline `a3f8b1c2d4e5`, proven on Postgres from empty.
  Startup no longer auto-migrates; migrations are an explicit deploy step and the serving
  process refuses to boot if the DB is not at Alembic head.
- Registration commit wrapped: `IntegrityError` / race now returns a clean 400, not a 500.
- Mission card gained an error + retry state instead of a silent blank or stuck spinner.

## v0744 — Rickie companion, Phase 1 (`160dc48`, 2026-07-23)

Context-aware conversational coach on Anthropic Sonnet 5 (768 tokens, thinking disabled,
10/day + 3/min, graceful 503 without a key). Character defined in
`docs/rickie_character_bible.md` as the source of truth.

## v0743 — Trust: password floor and honest auth copy (`fbeb54c`, 2026-07-23)

Server-authoritative password minimum (8 chars) and username bounds (2–80) with friendly,
specific messages; matching client `minlength` and an up-front hint; privacy line at the foot
of the auth card. Before this, registering with the password `"1"` was accepted with a 201.

## v0742 — Overnight UX polish (`b17f040`, 2026-07-22)

Value-first landing page (three value props ahead of the sign-up controls, guest path reframed
as "Peek at today's mission first — no signup"), header decluttered into a settings menu, honest
zero-state instead of three zeros, celebration polish. Tightened mobile auth spacing.

## v0741 — Mission Control (`0beef56`, 2026-07-08)

Verification and operations layer behind `/admin`.
