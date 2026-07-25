# Reproducibility & Configuration-Drift Ledger

The release-engineering record: what was made reproducible, the drift that was found, and — per the standing rule *"if a change alters behavior, document it instead of implementing it"* — the items deliberately **left as-is** with the reason. A new engineer should be able to read this and know exactly where the repo and reality still diverge.

## What changed (behavior-preserving reproducibility)

| Change | File(s) | Why it's safe |
|---|---|---|
| Pinned `anthropic` (`>=0.40.0` → `==0.120.0`) and added explicit `alembic==1.18.5` | `requirements.txt` | Removes the only floating dep + a boot-time transitive. `0.120.0`/`1.18.5` are the versions the passing test suite runs on. Affects only a future, operator-triggered deploy — not running prod. **One-time reconcile step below.** |
| `.env.example` — every variable documented | `.env.example` | Pure template; no real values. |
| One-command local setup | `Makefile` | Dev tooling only; doesn't touch prod. Enforces the 3.12 runtime. |
| CI on the real pinned stack + "all deps pinned" gate | `.github/workflows/ci.yml` | Adds checks; runs on GitHub runners; never touches prod. |
| Deploy codified as reviewable config | `render.yaml` | **Inert** until a Blueprint is created from it (see below). |
| Ops docs: env/secrets/prereqs, setup, this ledger | `docs/operations/*` | Documentation. |
| `requirements-dev.txt` | `requirements-dev.txt` | Additive. |

**Net effect on the running production process: none.** Prod is at `56444f9`; every change here affects only a *future, user-authorized* deploy or local/CI environments.

## Configuration drift found

### 1. Python / SQLAlchemy version drift (live)
- **Pinned:** Python `3.12.7` (`runtime.txt`, `.python-version`), `SQLAlchemy==2.0.27`.
- **Actually used in recent local work:** Python `3.14` with a **newer** SQLAlchemy — because `2.0.27` doesn't import on 3.14.
- **Resolution:** the pinned 3.12.7 stack is authoritative (it's what prod runs). The `Makefile` now enforces 3.12 and errors with guidance on the wrong version; CI runs on 3.12.7. **Not changed:** the SQLAlchemy pin (bumping it to support 3.14 is a behavior change to the ORM — deferred, see below).

### 2. Deploy configuration lived only in the Render dashboard
- **Was:** Start/Build commands existed only as comments in `app.py` + `README.md`; nothing in git could rebuild the deploy.
- **Now:** `render.yaml` + `docs/operations/setup.md` codify it. `render.yaml` is **proposed** — committing it does not change the live service (Render applies a blueprint only when the service is created from/linked to one). Reconcile before adopting.

### 3. Unpinned / mispinned dependencies
- `anthropic` was floating (`>=0.40.0`); `alembic` (imported at boot) was only transitive. Both now pinned. CI's "all deps pinned" gate prevents regression.

## Documented, NOT implemented (would alter behavior or unverifiable)

Each of these is a real improvement that was **intentionally not made** because it would change runtime/deploy behavior, or because it can't be verified against the Render dashboard (which this repo can't see). They are the honest edges.

| Item | Why not done | What's needed to do it safely |
|---|---|---|
| **Reconcile the `anthropic`/`alembic` pins with prod's actual versions** | The pin was set to the test-proven `0.120.0`/`1.18.5`, but prod's *currently installed* versions are unknown (last deploy resolved `>=0.40.0` to whatever was latest then). The next deploy will install the pinned versions — possibly different SDK behavior. | In a prod shell (Render): `pip freeze | grep -Ei 'anthropic|alembic'`. If they differ, decide whether to match prod or move to the pinned version *deliberately*, then deploy. |
| **Gunicorn `$PORT` binding** | The documented start command is `gunicorn app:app` with no `--bind`. Gunicorn defaults to `127.0.0.1:8000`; Render web services must listen on `$PORT`. Either the live command actually includes `--bind 0.0.0.0:$PORT` (and the docs are incomplete) or Render injects it. "Fixing" it in `render.yaml` could change how prod binds. | Read the live Start Command in the Render dashboard; update `render.yaml`/docs to match reality. Do not change the running bind blindly. |
| **Adopt `render.yaml` as authoritative (Blueprint deploy)** | Switching the live service to blueprint-managed changes how config is applied. | Diff every field against the dashboard, then create/link the Blueprint deliberately. |
| **Bump `SQLAlchemy` to support Python 3.14** | ORM behavior can change across minor SQLAlchemy versions; the suite is validated on `2.0.27`. | Bump in a branch, run the full suite on 3.12 *and* 3.14, review ORM-behavior notes, then pin the new version. |
| **Add a `gunicorn.conf.py`** (explicit workers/timeout) | `gunicorn.conf.py` is **auto-loaded** — it would change runtime settings (workers, timeout, bind) on the next deploy. | Capture the live gunicorn invocation first; encode current values exactly; verify workers=1 assumption. |
| **Split dev vs prod deps** (move `pytest` out of `requirements.txt`) | Removing `pytest` changes the deployed image (prod currently installs it). | Confirm nothing in prod imports pytest at runtime (the in-app verifier uses `WsgiClient`, not pytest), then move it to `requirements-dev.txt` on a deploy you're watching. |
| **Full transitive lockfile (`pip-compile`/hashes)** | Generating it here would resolve to *today's* latest transitives (newer than prod), i.e. a silent bump. Also can't build a 3.12 lock on this 3.14-only machine. | Run `pip-compile` (or `pip freeze`) inside a clean **3.12.7** env — ideally seeded from prod's actual versions — and commit `requirements.lock`. |
| **`Procfile`** | On some platforms a `Procfile` overrides the dashboard start command — could silently change what runs. `render.yaml` is the idiomatic Render mechanism and is already provided (inert). | Not needed if `render.yaml` is adopted. |

## The one manual step that remains (and why)

**Provisioning secrets in the Render dashboard.** `DATABASE_URL`, `SECRET_KEY`, `JWT_SECRET_KEY`, `ANTHROPIC_API_KEY`, `ADMIN_SECRET` are entered by an operator (`sync:false`) and are correctly *not* in git. This is not drift — it's the secret boundary. Everything *around* it (which keys, what they do, how to generate them) is now documented in [environment.md](environment.md) and `.env.example`.
