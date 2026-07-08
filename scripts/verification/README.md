# StreakFit Verification Suite

The automated half of the [StreakFit Verification Standard](../../CLAUDE.md): every feature merged into `main` needs a module here, not a one-off script that gets thrown away after one use.

Standard library only — no `pip install` needed. Every module only ever creates throwaway `qa_smoke_*` accounts and a disposable `Smoke Test <run tag>` team; nothing here touches an existing user, an existing team, or the database directly. Safe to run against production at any time.

## Running it

Full suite, one shared scenario, in dependency order:

```bash
python scripts/verify_all.py https://streakfit.pro
```

Just one subsystem while developing — each module builds its own minimal scenario from scratch, so it runs standalone with no setup:

```bash
python scripts/verification/chat.py https://streakfit.pro
```

Every entry point accepts the base URL as a positional arg, `--base-url`, or `$SMOKE_BASE_URL`, in that precedence order, defaulting to production if none is given.

Exit codes: `0` all passed, `1` at least one check failed, `2` a setup step (e.g. registration) failed and the rest of the run was skipped, since nothing downstream could work without it.

## Modules

| Module | Covers |
|---|---|
| `auth.py` | Register, login, `/api/me`, duplicate-username and wrong-password rejection |
| `teams.py` | Create, join, roster (names + creator flag), bad-code and duplicate-join rejection |
| `mission.py` | Daily Mission completion, streak/total-missions stats |
| `campfire.py` | Cumulative team mission counter and derived stage |
| `moments.py` | Team Moments history (`team_created`, `member_joined`, `campfire_log_added`, ordering) |
| `chat.py` | Team Chat post/read, empty/over-length rejection, emoji reactions as plain messages |
| `rickie.py` | Rickie's team reactions (welcome, first-log) — fixed templates, `sender_user_id` null |
| `security.py` | Invite rotation, remove member, leave team, unauthorized access — runs **last**, mutates membership |

## Not yet covered

These subsystems exist in the app but don't have a verification module yet — the next ones to add, not silent gaps discovered later:

- **`brainboost.py`** — question delivery, answer submission, scoring
- **`coach.py`** — Rickie's 1:1 Coach chat (distinct from `rickie.py`'s team reactions)
- **`notifications.py`** — the daily-reminder permission ask and completion notification
- **`pwa.py`** — install prompt, manifest, service worker cache versioning

Add a module here the same day you ship the feature it verifies, per the Verification Standard — don't let this list grow.

## Module contract

Every module exposes:

```python
def run(api, results, scenario):
    """Appends checks to `results`. Returns `scenario` (possibly mutated)."""
```

And a standalone entry point:

```python
if __name__ == "__main__":
    run_module_standalone("<Subsystem name> verification", build_scenario_fn, run)
```

`build_scenario_fn(api, results)` builds whatever minimal scenario this module needs on its own — reuse `_fixtures.build_team_scenario` where a plain two-member team is enough; write a small wrapper (see `campfire.py`, `moments.py`, `rickie.py`) when the module also needs a completed mission first.

`scripts/verify_all.py` builds one shared scenario and runs every module's `run()` against it, in dependency order — `security.py` last, since it's the only module that mutates membership state (removes a member, leaves a member).

`_client.py` and `_fixtures.py` are shared infrastructure, not subsystems — `ApiClient`, `Results`, and the scenario-building helpers every module needs.
