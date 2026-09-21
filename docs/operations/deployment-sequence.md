# Deployment sequence — the order, and why it is this order

**Status:** the plan of record, superseding the stage order in
[notification-rollout.md](notification-rollout.md) §4 where they differ.
**Date:** 2026-09-21.

The rollout doc says deploy first and configure afterwards. That is wrong for
this release, for two independently-verified reasons found while preparing it.
This file is the corrected order.

## The two constraints that fix the order

### 1. The reporting UI must not go live without its safeguards

Owner instruction: *"The new reporting UI must not become publicly available
with missing evidence encryption or untested notification delivery."*

`/api/reports` does not exist in the deployed build — verified, zero
occurrences in `fa92abd`. So there is no reporting today and no exposure
today. **The exposure begins at the instant of deploy**, which means the
safeguards have to be in place *before* that instant, not configured after it.

### 2. Shared rate-limit storage must come AFTER the deploy, not before

This one is counter-intuitive and is the reason "configure everything first"
would have been dangerous.

The deployed build **reads `RATELIMIT_STORAGE_URI`** but contains **none** of
the protections added on this branch — verified against `fa92abd`:

| | deployed build | this branch |
|---|---|---|
| reads `RATELIMIT_STORAGE_URI` | **yes** | yes |
| `_degrade_limiter_when_shared_storage_is_down` | **absent** | present |
| `sensitive_when_degraded` | **absent** | present |
| `in_memory_fallback_enabled` | **absent** | present |

Point the *current* production app at a new Key Value instance and any blip in
that instance returns 500 from every throttled route, with no degradation and
no recovery path. The free plan explicitly *"might restart a Free Render Key
Value instance at any time"*, so this is not hypothetical.

**Therefore `RATELIMIT_STORAGE_URI` is the one variable that must not be set
until the fixed code is live.**

Everything else is safe to set early, because the deployed build does not
reference it at all — verified, zero occurrences each:
`STREAKFIT_EVIDENCE_KEY`, `RESEND_API_KEY`, `STREAKFIT_NOTIFY_CHANNEL`,
`STREAKFIT_NOTIFY_FROM`, `STREAKFIT_NOTIFY_TO`, `STREAKFIT_RETENTION_SWEEPER`.
They sit inert and become live the moment the new code boots — which is
exactly the property the first constraint needs.

> Saving environment variables in Render may trigger a redeploy of the
> *current* commit. That is a no-op here: the build ignores all of these, and
> its `flask db upgrade` is already at head, so the chain is a no-op too.

## The sequence

### Phase A — before the deploy (no production behaviour changes)

- [ ] **A1 · Evidence key.** `streakfit-evidence-key.sh new` → store the
      passphrase somewhere independent of this machine → `verify` **in a new
      shell**. It refuses to certify until it has decrypted the file, passed
      gpg's integrity check, round-tripped a real Fernet operation and matched
      the fingerprint. Then `reveal` and set `STREAKFIT_EVIDENCE_KEY` in
      Render. *Inert until deploy.* **Gate: owner — key custody.**
- [ ] **A2 · Resend account.** Create it, generate an API key, set
      `RESEND_API_KEY`, `STREAKFIT_NOTIFY_FROM=onboarding@resend.dev`,
      `STREAKFIT_NOTIFY_TO=<owner address>`, `STREAKFIT_NOTIFY_CHANNEL=resend`,
      `STREAKFIT_PUBLIC_URL=https://streakfit.pro`. *Inert until deploy.*
      **Gate: owner — account authorization.**
- [ ] **A3 · Prove delivery reaches the provider.**
      `python scripts/notification_live_send.py` — one real send, from the new
      code, to the configured address, **locally**. It needs no deploy and
      never touches the production database, so the last untested link is
      closed before anything is public rather than after.

      It prints the exact message first, asks for a typed `SEND`, and exits
      non-zero on anything else. It never prints the API key. A receipt means
      Resend *accepted* it — a person still has to confirm it arrived.
      **Gate: owner — authorizes one real email.**
- [ ] **A4 · Preflight.** `python scripts/notification_preflight.py --live`
      confirms Resend accepts the key. Prints no values, sends no mail.

      **Done 2026-09-21: PASS.** The key is valid and correctly scoped to
      sending only, so the sending-domain check cannot run and the sender can
      only be confirmed by sending — which is A3's job.

      This step also found the defect that would have broken every alert:
      Cloudflare fronts `api.resend.com` and refused urllib's default
      signature with a 1010 before Resend saw the request. See
      [notification-rollout.md](notification-rollout.md) §7.

> Run A4 **before** A3. The preflight is free and catches configuration and
> transport problems without spending a send; with a send-only key it is also
> the only thing that can confirm the credential short of sending.

**Do not proceed to Phase B until A1–A4 are done.** A deploy without them is
the thing constraint 1 forbids.

### Phase B — the deploy

- [ ] **B1 · `streakfit-premigration.sh`, immediately before B2.** Fresh
      encrypted backup + proof it parses + the count of what the destructive
      migration discards, in one run from one moment. Its verdict is valid for
      30 minutes; past that, run it again. A backup taken hours earlier is not
      a backup of what the migration is about to change.
- [ ] **B2 · Push and deploy.** `git push origin main`, trigger the deploy in
      Render (Auto-Deploy stays **Off**). The start command runs the 12
      migrations, then gunicorn. **Gate: owner — production change.**
- [ ] **B3 · Verify.** `/api/build-identity` reports the new SHA and
      `migration.atHead` with 25 applied. `/api/verification/self` reports
      `moderation.evidence_key` **PASS** with the fingerprint from A1.
- [ ] **B4 · One real report, end to end.** File a throwaway `child_safety`
      report and confirm the email arrives. Only now does delivery work.

Expected and correct at this point: `ratelimit.shared_storage` **FAIL**,
because storage is still `memory://`. That is Phase C, not a regression.

### Phase C — after the deploy

- [ ] **C1 · Free Key Value**, same region, **`noeviction`**. Confirm at
      creation that `noeviction` is offered and the workspace's single free
      instance is free. If either is not true, **stop** — do not upgrade to a
      paid plan to work around it. **Gate: owner — service creation.**
- [ ] **C2 ·** Set `RATELIMIT_STORAGE_URI`, redeploy, confirm
      `shared backend counting (redis)`.
- [ ] **C3 ·** `STREAKFIT_RETENTION_SWEEPER=1` **inline on the start command**
      (as a global it fires during `flask db upgrade` and deadlocks the
      deploy). Then the hourly notify cron.

## What "done" does not mean

A green deploy with reporting live and any of A1–A4 skipped is **not a
completed release**, however clean the migration was. The reporting UI being
reachable is the promise; evidence encryption and a delivery path that has
actually delivered are what make that promise true.

`ratelimit.shared_storage` stays FAIL between B and C by design. Nothing
should be described as finished while it is red — it is the control in front
of an invite-code lookup with a measured 321 probes/second enumeration oracle.
