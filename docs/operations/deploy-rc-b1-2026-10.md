# RC-B1 — what ships, and the one step after deploy

Repair tranche from the E2E campaign's R1 ledger (`e2e-campaign/ledger.jsonl`),
on top of `1613dfd` (= production `7aa1dff` + the verification-harness fix).
**Not deployed.** Deploying, and the step below, each need owner approval.

## Hard operational invariant — until D26–D31 are repaired

Production must stay exactly as observed on 2026-10-03:

- **one** `streakfit-api` instance (Render → Compute → Instances = 1);
- `WEB_CONCURRENCY` = 1 (Render's default for the 0.5c-512mb Starter plan; not set by us);
- **one synchronous Gunicorn worker** (Start Command `... gunicorn app:app`, no `--workers`);
- **no** `--threads`, no async/gevent worker class, no extra application workers.

**Any change to the plan, instance count, worker count or threading requires D26–D31
to be repaired first.** Those defects (concurrent filter purchases, lost XP/acorn
awards and double mission bonuses, member-cap overrun, and three 500s on duplicate
requests) are verified on a multi-worker lab and cannot occur while requests are
handled one at a time. Raising the plan to 1 CPU or more would raise Render's
default `WEB_CONCURRENCY` and expose them silently.

Evidence: Render settings/compute/env (read-only, 2026-10-03); independent
reproductions in `e2e-campaign/evidence/conc2/`.

## What ships

| R1 | change |
|---|---|
| D3 | A block takes effect only between current teammates, so blocking a stranger leaves the same trace as a missing id (no enumeration of accounts or names through `GET /api/blocks`). |
| D4 | Removing a photo, or deleting its sender's account, removes the caption from the photo row, its chat message body and its history moment. New uploads no longer copy the caption into the moment. |
| D1 | The Settings menu scrolls inside itself and stops above the bottom nav: Log Out and Delete my account are reachable on phones. |
| D5, D25 | Guest copy no longer promises a saved streak; leaving guest mode clears the guest session (greeting, toasts) and keeps only an invite code. |
| D10 | Three Days, A Full Week and Two Weeks are announced when crossed. |

Also: `verify_all` suite version 8 (the deletion check now deletes an account
with real dependent rows), `uicheck` additions, `sw.js` `streakfit-v1003c`.

**No migrations, no config change.** The alembic head is unchanged
(`08920334bccd`), so rollback is by code: redeploy the previous commit, no
`flask db downgrade`.

## After deploy — clear captions left by earlier removals (owner-approved)

The D4 fix acts when a removal happens. Photos and accounts removed **before**
it still have their captions in `team_message` / `team_moment` and are served
by `/messages` and `/moments`. Clear them once:

```bash
flask photo-caption-scrub            # dry run: prints what it would clear
flask photo-caption-scrub --execute  # clears it; safe to repeat (idempotent)
```

Run it the way other one-off production commands are run (Render shell),
dry run first, and record both outputs in the release audit. It also clears
every `photo_shared` moment's metadata, which held only a copy of the caption
that nothing reads.

## Side effects of `verify_all` against production, as of suite version 8

Same accounts and reports as before. In addition, the self-deleting leaver now
joins the run's `Smoke Test <tag>` team before deleting itself, which leaves one
"A member joined" moment and one Rickie "Glad you're here." message in that
disposable team.
