# Secret scanning

The repository is **public**. A committed credential is a published credential: rotate it, then clean up.

## What runs where

| Scan | Runs | Covers |
|---|---|---|
| `tests/test_no_committed_secrets.py` | every CI run (`test` job) | the files at that commit, plus untracked files when run locally |
| `python scripts/secret_scan.py --history` | every CI run (`secret-history` job), and locally | every blob reachable from any branch, tag or remote ref |
| Gitleaks (pinned release) | every CI run (`secret-history` job) | the same history, with Gitleaks' own maintained rule set |

The `secret-history` job checks out with `fetch-depth: 0`, so it sees every branch and tag. Locally, run `git fetch --all` before the history scan. It took ~35 s for 542 commits on 2026-10-13, and found no real credential.

`secret-history` is **not** a required check in the branch-protection proposal. Add it only once it has been green on `main`.

## Why two scanners

Ours knows StreakFit's own credential shapes and placeholder conventions, and its tests pin exactly what it catches. Gitleaks is an independently maintained set of ~200 rules for providers we do not use yet, plus generic high-entropy detection. Neither replaces the other.

## Gitleaks: how it is installed, and upgrading it

CI downloads the official release tarball from `github.com/gitleaks/gitleaks/releases` and checks it against a SHA-256 **written in the workflow** before extracting anything. It is not a third-party GitHub Action, so no third-party code runs with the job's token. The job only has `contents: read`, and checkout does not persist credentials.

To upgrade:
1. Pick a release at least two weeks old.
2. Read the asset digest: `gh api repos/gitleaks/gitleaks/releases/tags/vX.Y.Z --jq '.assets[] | select(.name|test("linux_x64")) | .digest'`.
3. Confirm the release's `checksums.txt` lists the same value.
4. Update `GITLEAKS_VERSION` and `GITLEAKS_SHA256` together.

Pinned on 2026-10-13: v8.30.1 (published 2026-03-21 by the project maintainer), `linux_x64` SHA-256 `551f6fc8…f2470eb`.

## Triaging a Gitleaks finding

Output is redacted (`--redact`): it shows rule, file, line, commit and fingerprint, never the value. The job's log is public, like the repository.
- **Real credential:** follow "If a real credential is found" below.
- **Synthetic, and in a current file:** rename the value, as for our scanner.
- **Synthetic, and only in old history:** add the finding's fingerprint (`commit:file:rule:line`) to `.gitleaksignore`, with a comment naming who reviewed it, when, and why it is synthetic. A fingerprint names one commit and line, so it cannot excuse anything else.

## What it reports

Credential **shapes** StreakFit uses or could plausibly hold:
- Neon passwords (`npg_`) and Neon API keys (`napi_`);
- postgres/redis URLs with a password;
- libpq `host=… password=…` strings;
- values assigned to the app's secret variables (`SECRET_KEY`, `JWT_SECRET_KEY`, `ADMIN_SECRET`, `STREAKFIT_EVIDENCE_KEY`, `RESEND_API_KEY`, `ANTHROPIC_API_KEY`, `NEON_API_KEY`, `RENDER_API_KEY`);
- Render, Anthropic, Resend, Stripe-live, GitHub and AWS keys;
- private keys.

Output names file and line (or blob and path), **never the value**.

Not reported: identifiers (Neon endpoint IDs, Render service IDs). They are not credentials. Still keep non-production identifiers out of this repo, since there is no reason to publish them.

## Writing test values

A value is treated as synthetic when its shape says so:
- `<…>`, `{…}`, `$VAR`, `...`, `***`, `%s`;
- a **whole word** such as `test`, `example`, `dummy`, `placeholder`, `sentinel`, `fake`, `changeme`, `redacted`, `not-real`, `not-for-production`, `supersecret`, `localdev`;
- a single repeated character.

"Whole word" matters: `Testarossa9Qz` is reported, `redis-test-pw123` is not.

**If the scan flags a test value, rename the value. Do not widen the scanner.** A real-looking literal that a test genuinely needs is assembled at runtime (`'Zq8v' + 'Kp2Lm9Rt'`); see the scanner's own tests.

## History exceptions

`scripts/secret_scan_history_exceptions.txt` lists **full blob SHAs** that were reviewed and found synthetic, each with the reason. A blob is a file's exact content, so an exception can never excuse different content. Add one only after confirming, without printing the value, that the finding is synthetic.

## If a real credential is found
1. **Rotate it first.** Removing it from git does not un-publish it: forks, clones and caches keep it.
2. Record the incident (what, since when, where it was reachable).
3. Then remove it from the tree. Rewriting public history is a separate owner decision.
