# Agent permissions — what is actually enforced

`.claude/settings.json` is the checked-in permission list for coding agents
working in this repo. `.claude/settings.local.json` holds machine-local entries
and is git-ignored. This file records what those rules do and, more usefully,
what they do **not** do.

## The rule that shapes the rest

**A deny rule does not make an unsafe allow rule safe.** Deny is a backstop for
a mistake, not a licence to allow something broad next to it. So the allow list
is built to be defensible on its own merits, and an entry that needs a deny rule
standing beside it to be acceptable is the wrong entry.

Three rules from the first pass at this list failed that test and were removed:

| Removed | Why it was not safe on its own |
|---|---|
| `Bash(.venv/bin/python scripts/*)` | Write a file into `scripts/`, then run it. Combined with Write access this is arbitrary code execution — and a Python script reads `.env` with `open()`, which `Read(./.env)` never sees. |
| `Bash(sqlite3 *)` | `.shell` and `.system` are shell escapes. |
| `Bash(find *)` | `-exec`, `-execdir`, `-ok` and `-delete`. |
| `Bash(.venv/bin/python -c 'import*)` | Matches `-c 'import os; os.system(...)'`. |

Python is now allowed only for the **named scripts that exist**. A new script
costs exactly one approval. That is the point: the existing workflow runs
unattended, and novelty gets a look.

## What is genuinely enforced

**No production write.** `git push` is denied in every spelling, along with
`git push --force`, `git remote add`, `git remote set-url`, `gh workflow run`,
`gh release`, `gh secret` and `flask db downgrade`. This matters here
specifically because **Render deploys from this repository** — an allowed
`git push` is an allowed deploy.

**No network egress.** `curl`, `wget`, `nc`, `ssh` and `scp` are *denied*, not
merely absent from the allow list. Absent would mean a later session could add
one by approving it once; denied means it stays denied. HTTP checks go through
the project's own entry points (`make verify`, `make uicheck`,
`scripts/verification/`) which is narrower than a `curl` rule could be, since
flags precede the URL and a host-scoped `curl` prefix rule cannot be written
reliably.

**Secrets are not casually readable.** `Read` denies cover `./.env`, `./.env.*`,
`~/.ssh`, `~/.aws`, `~/.config/anthropic` and `~/.claude/.credentials.json`.
`env`, `printenv` and `cat .env*` are denied. `cp`/`mv` of `.env` is denied too,
because copying a secret to a path the read tools *are* allowed on would launder
it past the `Read` deny.

## What is NOT enforced — read this part

**On a machine where the agent has a shell, file-level deny rules are friction,
not a boundary.** The allow list still contains ordinary development primitives
that can read and write files: `cp`, `mv`, `touch`, `mkdir`, `chmod`, `echo`,
`printf`, `head`, `tail`, `grep`, `sed -n`. Some combination of those can reach
a local file's contents. They are kept because removing them makes the agent
useless for ordinary work, and a determined path around them exists regardless.

So do not read the `Read(./.env)` deny as "the agent cannot read the key." Read
it as "the agent will not read the key by accident, or without going out of its
way." The controls that actually bound the damage are the two above: **it cannot
send anything anywhere, and it cannot write to production.** A secret read
locally and never transmitted is a much smaller event than either of those.

If a hard boundary is ever needed, the mechanism is the sandbox
(`sandbox.credentials.envVars` with `mode: deny`, and
`sandbox.filesystem.denyRead`), which enforces at the OS level instead of at the
permission-prompt level. That is not enabled today, because it would change how
every local command runs and that is a bigger decision than this file.

## Anthropic API key

The key lives in `.env` (mode `600`, git-ignored) and is loaded by the
Makefile's `LOAD_ENV` into recipes that need it. It is **StreakFit's key, not
the coding agent's**: Claude Code authenticates with the account subscription,
and the key is recorded in `~/.claude.json` under
`customApiKeyResponses.rejected` so Claude Code will not spend it.

Worth checking periodically: if a key ever appears in that file's `approved`
list, Claude Code will use it silently the moment it is in the environment.

## Verifying the effective rules

Settings merge across managed → user → project → local, so reading one file is
not the same as knowing what is in force:

```bash
python3 - <<'EOF'
import json
from pathlib import Path
for p in [Path("/etc/claude-code/managed-settings.json"),
          Path.home()/".claude/settings.json",
          Path(".claude/settings.json"),
          Path(".claude/settings.local.json")]:
    if p.exists():
        perms = json.loads(p.read_text()).get("permissions", {})
        print(f"{str(p):52s} allow={len(perms.get('allow', [])):3d} "
              f"deny={len(perms.get('deny', [])):3d}")
EOF
```

Claude Code appends to `settings.local.json` whenever a permission is approved
during a session, so that file grows on its own. Re-check it after long
unattended runs; that is how the original 283 entries accumulated.
