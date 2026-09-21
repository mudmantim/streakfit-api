# Giving StreakFit an Anthropic API key

Ask Rickie is a core feature and it has never produced a single real response
in this project. Everything written about his tone, his boundaries and his
usefulness is currently a claim about a prompt. This is how to change that.

**Never paste the key into a chat, a commit, a screenshot, or a file in the
repository.** Everything below keeps it out of all four.

---

## 1. Create a key with a spend cap

At <https://console.anthropic.com> → **API keys** → *Create key*.

- Name it `streakfit-local-eval` so it is obvious what it is for and safe to
  revoke on its own.
- Before you use it, go to **Billing → Limits** and set a **monthly spend
  limit**. Ten dollars is far more than the evaluation needs and small enough
  that a mistake is an annoyance rather than an event.
- Copy the key once. The console will not show it again.

## 2. Put it in `.env`, which git already ignores

`.env` is in `.gitignore` and has been since before this work started. Verify
that yourself rather than taking my word for it:

```bash
cd ~/Desktop/Streakfit/streakfit_production_baseline
git check-ignore -v .env          # prints the .gitignore rule that covers it
```

Then, in a terminal — **not in a chat window**:

```bash
# A leading space does NOT reliably keep this out of your shell history: it
# only works when HISTCONTROL includes `ignorespace` or `ignoreboth`, and the
# common default (including on this machine) is `ignoredups`, which does not.
# Check first:  echo "$HISTCONTROL"
#
# The safe version prompts instead, so the key is never on a command line at
# all -- arguments are also visible in `ps`, where history settings do not
# help. `read -s` leaves no echo and no history entry.
read -rs -p 'Anthropic API key: ' K && printf 'ANTHROPIC_API_KEY=%s\n' "$K" >> .env && unset K
chmod 600 .env                     # only you can read it
```

Or open `.env` in an editor and fill in the line that is already there:
`ANTHROPIC_API_KEY=`

**Check nothing leaked before going further:**

```bash
git status --short                 # .env must NOT appear
grep -rl "sk-ant" --exclude-dir=.git --exclude=.env . || echo "clean"
```

## 3. Run the evaluation

```bash
make run                                        # one terminal
python scripts/coach_eval.py --list             # another: costs nothing,
                                                # prints all 56 prompts
python scripts/coach_eval.py                    # the real run
```

Before sending anything it prints the worst-case call count and token estimate.
`--max-calls` is an enforced ceiling, not an estimate — the run stops there
whatever the matrix says. It defaults to 200 against a 56-prompt matrix.

Expect roughly 56 to 168 calls (a weather question runs a tool loop, so one
prompt can be up to three), about twenty minutes at the default pacing, and a
cost in the region of a few tens of cents.

## What the harness will and will not do

| | |
|---|---|
| Accounts | Creates its own, named `qa_coach_eval_<timestamp>_<n>`, seconds before use. |
| Your data | Never touches it. A fresh account has no conversation and no Coach Notes, so nothing anybody has typed can reach Anthropic through this script. |
| Prompts | 56 written prompts, in `scripts/coach_eval.py`, readable in full before a penny is spent. |
| Target | Local only. It refuses any host that is not localhost, because it writes to the database directly. |
| Ceiling | `--max-calls`, enforced in the loop. |
| Output | A transcript at `coach_eval_results.json`, which is git-ignored. |

Four tests in `tests/test_coach_privacy.py` hold those properties so they
cannot quietly stop being true.

## Afterwards

Revoke the key when the evaluation is done, or leave it with the spend cap on.
If it is ever pasted somewhere it should not be, revoke it in the console
first and worry about the history second — revocation is instant and free.

For production, the key belongs in Render's environment variables, never in the
repository. That is a separate decision and is not needed for any of this.
