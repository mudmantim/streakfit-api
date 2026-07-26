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

## v0748 (2) — Rate limits keyed on the real client (`d262336`, deployed 2026-07-26)

**Deployed and verified.** Pushed `6a6eedf..d262336` at 21:15:13Z; `/health` returned 200 on every
poll through the restart (zero downtime). No static assets changed, so the service-worker version
stays `v0748` and the deploy signal was behavioural instead: `/api/register` capacity dropped from
~35/min to **exactly 5/min**. No migration, no new environment variable — rollback is code-only.

Closes the finding recorded in the entry below, which that deploy's new post-deploy protocol had
surfaced.

- **`ProxyFix(x_for=2)`** — `request.remote_addr` was gunicorn's TCP peer (a Render-internal
  address), so every per-IP limit was keyed on infrastructure and effective capacity was ~6-7×
  configured. `x_for=2` matches the documented chain (client → Cloudflare → Render internal →
  gunicorn), and counting from the right is what makes it unspoofable: Cloudflare appends the
  connecting IP, so a forged header only lands further left. Render's own "first IP in the list"
  guidance would have been **spoofable**.
- **Per-route limiter keys.** Making per-IP limits bind exposed a latent design error: five
  *authenticated* routes were keyed per IP, including `/api/coach` at 10 per **day** — a household
  behind one router is one IP but several people, so a family would have shared one coach quota.
  Authenticated routes now key on the JWT subject; anonymous routes (`register`, `login`, `events`,
  `admin/*`) stay per IP, where the IP is what is being protected against.
- **Regression gates.** `post_deploy_check.py` asserts `/api/register` admits exactly 5/min,
  two-sided so both a partitioned key and an over-coarse one fail. 12 new unit tests, every one
  fault-injected to confirm it fails (removing ProxyFix, `x_for=1`, `x_for=3`, coach reverted to
  per-IP, register switched to per-user).
- Full evidence: `docs/operations/rate-limiting-client-ip.md`. Roadmap **M1a resolved**, **M1b**
  (`memory://` storage) re-scoped and explicitly marked *do not do before M1a*.

**Production results:** 38 of 39 checks passed; `verify_all` **81/81**; `/api/register` exactly
5 allowed / 5 limited; mission flow 0/5 → 5/5 with Day-1 streak; coach 200 and in character. The one
failure was a single health poll returning a **connection error** (`HTTP 0`, not an application
status) out of 40. It did not recur across **80 further samples over 20 minutes**, latency around it
was normal (avg 0.16s, max 0.28s), and 15 independent samples were clean — transient network, not
attributable to this deploy. Per-user bucketing is proven by automated tests; the production
verification directly proves the per-client IP limit.

## v0748 — Release-candidate hardening (`995f178`, deployed 2026-07-26)

**Deployed and verified.** Pushed `56444f9..995f178` at 15:47:53Z; the service worker flipped
`v0747` → `v0748` 62s later with `/health` returning 200 on every poll throughout (zero
downtime). Post-deploy protocol: **36/36 checks passed**, `verify_all` **81/81**, 20-minute
health watch **40/40 polls green** (avg 0.16s, max 0.37s). No migration and no new environment
variable, so rollback is code-only.

One finding, pre-existing rather than introduced here: **rate limits are enforced, but per
proxy-hop rather than per user, so effective capacity is ~6-7x the configured value.** A follow-up
investigation established this precisely — an earlier note in this file claimed limiting was "not
enforced at all", which was wrong: that conclusion rested on an 8-request probe, far too small to
reveal a limit whose effective ceiling is ~35.

Measured: 12 POSTs over a **single keep-alive TCP connection** gave exactly `400 x5` then `429` —
the configured 5/min, enforced precisely. Across many connections, capacity multiplies: 50
sequential calls -> 30 allowed / 20 limited; 60 calls -> 37 allowed / 23 limited, first 429 at
request 21. Consistent with ~6-7 independent buckets.

Cause: `Limiter(get_remote_address, ...)` keys on `request.remote_addr`, and there is **no
`ProxyFix`** — production is Cloudflare -> Render router -> gunicorn, so `remote_addr` is an
internal proxy address, not the client. Partitioning across those addresses (and across processes,
since storage is `memory://`) is what multiplies the ceiling. `docs/security_review.md` #15
already documented both gaps.

Nothing in this deploy touches the limiter, its storage, or the worker count; the new post-deploy
protocol is simply the first thing that ever probed it. `/api/coach` has no application-level cap
behind flask-limiter, so its 3/min + 10/day are effectively ~20/min + ~70/day per client — real
spend exposure on a paid API, and the top-priority follow-up. Note the fix is the **key**, not the
storage: shared storage alone would collapse all users onto one proxy-keyed bucket and cause
collateral limiting.

Two groups of work shipped in this deploy:

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
