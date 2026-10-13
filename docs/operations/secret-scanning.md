# Secret scanning

The repository is **public**. A committed credential is a published credential: rotate it, then clean up.

## What runs where

| Scan | Runs | Covers |
|---|---|---|
| `tests/test_no_committed_secrets.py` | every CI run (`test` job) | the files tracked at that commit |
| `python scripts/secret_scan.py --history` | locally, before a release and after any suspected leak | every blob reachable from any branch, tag or remote ref |

CI checks out a single commit, so it **cannot** see history. The history scan needs a full clone (`git fetch --all` first). It took ~35 s for 542 commits on 2026-10-13, and found no real credential.

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
