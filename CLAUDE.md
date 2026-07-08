# StreakFit — Project Instructions

## StreakFit Verification Standard

Adopted 2026-07-08. Applies to every feature merged into `main` from this point forward.

A feature is **not complete** until all four of these are true:

1. **The feature** — the actual working code.
2. **Manual verification** — driven through the real UI (or a real API call sequence) and observed, not assumed. Guest mode, mobile width, and console errors are part of this, not optional extras.
3. **Automated verification** — a repeatable check in `scripts/verification/`, runnable on its own and as part of `scripts/verify_all.py`. Pytest coverage in `tests/` is separate and does not substitute for this — pytest verifies backend logic against a test database; `scripts/verification/` verifies the deployed app end-to-end, the same way a real user or real API client would hit it.
4. **Inclusion in the Verification Suite** — the new checks live in the subsystem module they belong to (see below), not bolted on as a one-off script that gets thrown away after one use.

**Why:** the R2 team/social layer shipped several features (R2.1–R2.6) that worked but were unreachable through the UI, and only the R2 Stability Review caught it. Manual + automated + suite-inclusion is what closes that gap going forward, not just for teams but for every subsystem.

**How to apply:** before calling any feature done, check off all four. If a subsystem's verification module doesn't exist yet, create it rather than skipping step 4 — see `scripts/verification/README.md` for the module contract.

## Verification Suite

`scripts/verify_all.py` runs the full production-safe end-to-end suite. See `scripts/verification/README.md` for the module list, what each one covers, and how to run a single subsystem standalone while developing.

Only ever creates throwaway `qa_smoke_*` accounts and disposable `Smoke Test <run tag>` teams. Never touches an existing user, team, or the database directly. Safe to run against production at any time.
