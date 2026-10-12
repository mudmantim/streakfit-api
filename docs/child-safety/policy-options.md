# Child-safety policy — two implementation options

**Status:** a proposal for the owner. Neither option is implemented or
activated. Written 2026-10-12 against production `6b6e1cd`.

**This is not legal advice.** Every item marked **[legal]** needs qualified
review before release (see also `requirements-matrix.md`, "Needs qualified
legal review before launch").

## Where production stands today

Verified in code:

- **No age boundary.**
  - `can()` (app.py, "Age bands, capabilities…") exists, denies by default,
    and has 15 test functions (`tests/test_child_account_permissions.py`). **No route calls it.**
  - Nothing writes `age_band`, `GuardianLink` or `Consent`.
  - Every account is `age_band = NULL`: "not asked", which `can()` would
    treat as a child.
- **Ask Rickie** (`POST /api/coach`) is open to every account. It sends the
  person's messages, recent replies, username and streak/level numbers to the
  AI provider.
- **Teams, chat and photos** are open to every account. They are protected by
  invite-only access, joined-at boundaries, blocking, reporting and operator
  moderation.
  - Correction to `architecture.md` §5, which still says there is "no
    blocking, reporting or moderation anywhere". That was true when written;
    `/api/blocks`, `/api/reports`, appeals and the operator queue now exist.
- **Data held:** username, optional display name, activity, team content,
  conversation text (30 days). **No** email, real name, date of birth or
  location.

Both options reuse the existing schema (`user.age_band`, `guardian_link`,
`consent`, `permission_audit`). Neither needs a new table for its first
release.

---

## Option A — adults-only, with enforced age eligibility

### Rule

StreakFit is for people **18 and over**.

- Registration requires an age-eligibility answer.
- An under-18 answer creates **no account**.
- `can()` is wired into every capability route, and `age_band = 'adult'` is
  the only band ever written.

### Age screen

- **Neutral:** "What year were you born?", choosing from a list with no
  default, or "Are you 18 or older? Yes / No" **[legal: which is acceptable
  as a neutral screen]**.
- **Nothing hints at the right answer.** The page does not say "you must be
  18" before the question.
- **Only the band is stored** (`adult`), never the year. The answer is
  checked and discarded.
- **Retry suppression:** after an under-18 answer, the client remembers the
  refusal (local storage) and does not re-offer the form in that browser. The
  server can't enforce this without collecting more; recorded as a known
  limit.

### Existing accounts

All are NULL today.

- On next sign-in, an **interstitial** asks the same question before anything
  else loads.
- **Until it's answered:** solo missions keep working. Ask Rickie and teams
  are refused through `can()`, which already denies NULL ("age not
  established").
- **"18 or older":** `age_band = 'adult'`, and everything works as today.
- **"Under 18":**
  - The account is **frozen immediately**: sign-in shows an explanation, and
    no further data is collected.
  - It is **deleted after a short notice period** (e.g. 14 days) through the
    existing `delete_user_account`. Teams owned by the account follow
    ADR-0007.
  - **[legal]** For a self-identified under-13, COPPA expects deletion of
    information collected without consent. Whether an export may be offered
    first, and how fast deletion must happen, needs review.
  - Deletion is irreversible, so the first run of this step needs its own
    owner go-ahead (a dry run counts first).

### Ask Rickie

`POST /api/coach` calls `can(user, CAP_ASK_RICKIE, record=True)`.

- Adults are allowed; anyone else gets 403 with the reason.
- The `PermissionAudit` row records the decision.

### Guardian relationships

None. `GuardianLink` and `Consent` stay unused, and families are adults
sharing teams.

### Data collected

One extra fact per account: `age_band = 'adult'`, plus a timestamp of when it
was confirmed.

### Privacy and legal **[legal]**

- **Actual knowledge vs "directed to children".** An adults-only screen
  addresses actual knowledge. It does **not** settle whether the service is
  "directed to children" under COPPA's factors. The content library is
  written for age 9 (`min_age` 9/13/16), and the product has been described
  as a family habit game. Those signals would need to change (marketing,
  copy, content `min_age`) for the screen to mean much.
- **Self-declaration is weak.** An adults-only service relying on it has to
  accept that some minors will lie, and decide what it does on learning that
  (the "Under 18" path above, triggered by a report).
- **App-store ratings, if ever distributed there.**

### Migration and rollback

- **No schema change** (`age_band` exists). Add `age_confirmed_at` only if a
  timestamp is wanted, as an additive migration with `lock_timeout` in both
  directions.
- **Flag:** `STREAKFIT_AGE_POLICY = off | adults_only`.
  - `off` = today's behaviour; routes skip `can()`.
  - Turning it off again restores today's behaviour.
- **Answers already given stay** (harmless).
- **Account deletions are not reversible.** The deletion step runs separately
  from the flag, with its own approval and a dry-run report.

### Tests before release

| # | Test |
|---|---|
| A1 | Registration without an eligibility answer → 400; "under 18" → no user row, no username in logs, generic message |
| A2 | "18+" → `age_band = 'adult'`; no year stored anywhere (column scan) |
| A3 | Every capability route (coach, coach memory, team create/join/chat/photos, challenges in teams) denies NULL and allows adult, with a `PermissionAudit` row; route list asserted against the URL map so a new route can't skip it |
| A4 | Existing NULL account: interstitial; solo mission works; coach/teams 403 with reason |
| A5 | "Under 18" on an existing account: frozen immediately; deletion job dry-run lists it; real run deletes and the FK suite holds |
| A6 | Flag `off`: the whole existing suite unchanged |
| A7 | Paid entitlement never grants (existing test kept) |
| A8 | `make uicheck` on the screen and interstitial; `verify_all` module on a throwaway adult account |

---

## Option B — family service with verified guardian consent

### Rule

Three bands, as `architecture.md` §1 designs them:

- **child** (under 13): an account exists only through a guardian. Each
  capability needs that guardian's recorded consent. Ask Rickie stays
  hard-blocked until the § 312.8(c) written assurance from the AI provider is
  on file.
- **teen** (13–17): defaults are an **owner decision**. `can()` currently
  denies them ("teen policy not set").
- **adult** (18+): unchanged.

### Establishing a guardian

The guardian is an adult account; there is no "guardian-only" account type.
The guardian **creates** the child account from their own Settings ("Add a
child"). So a child never holds an unlinked account, and the link exists from
the first second.

1. **Verifiable parental consent (VPC)** happens at creation, using one
   **[legal]**-approved method under 16 CFR 312.5(b).
   - Candidates: a small card transaction with notice, a signed form, a
     government-ID check with prompt deletion, a video call, knowledge-based
     questions.
   - The "email plus" shortcut is only for internal use. Ask Rickie
     discloses text to a third party, so it is not enough there.
   - The method is chosen with cost and drop-off in mind. A vendor may be
     needed: an owner decision.
2. `GuardianLink(child, guardian, method, evidence_ref)` is written.
   `evidence_ref` is the vendor's opaque receipt ID, never the evidence.
3. Consent is **per capability** (`Consent` rows), granted on a screen that
   says what each one means:

   | Capability | Child default | Grantable |
   |---|---|---|
   | Teams | Off | Per guardian |
   | Team chat | Off | Not at launch |
   | Team photos | Off | Not at launch |
   | Ask Rickie | Off | Hard-blocked until the 312.8(c) assurance |

4. **Revocation:** immediate, by any linked guardian. A second guardian
   (another parent) is a separate link, invited by the first and verified the
   same way.
5. **Guardian review right** (COPPA § 312.6):
   - a **logged access request** that produces the child's data export to the
     guardian;
   - never a live feed of conversations (`architecture.md` §6: the guardian is
     not always the safest person to notify).

### Existing accounts

- The same neutral screen as option A, but three answers.
- **Adult:** unchanged.
- **Teen:** the owner's teen defaults.
- **Child:**
  - frozen (no collection) and shown "ask a parent to add you";
  - deleted after a notice period unless a guardian claims it through the
    "Add a child" flow;
  - **[legal]** whether claiming a pre-existing child account is permissible
    at all, or the data must be deleted first.

### Ask Rickie

- **Children:** hard-blocked (the existing `_CHILD_HARD_BLOCKED` gate). It
  opens only when `STREAKFIT_CHILD_AI_ASSURANCE_ON_FILE=1`, and setting that
  flag is a claim that the written assurance exists. Even then, it also needs
  guardian consent.
- **Teens:** owner decision. Before any minor uses it: the provider's
  child-safety system prompt, AI disclosure, break reminders and the
  sexual-content rule (`architecture.md` §6).
- **A per-account "Rickie off" switch** for guardians reuses the coach's
  existing fail-closed 503 path (`minors-and-consent.md` decision 2).

### Data collected

- Age band.
- Guardian links, with the VPC method and an opaque reference.
- Consent rows.
- Permission audit rows.
- Guardian access-request log.
- Possibly a VPC vendor receipt. **[legal]** That vendor becomes a processor
  of a parent's data.
- Still no date of birth, no child email, no real name.

### Privacy and legal **[legal]**

- **The COPPA Rule as amended** (including the 2025 amendments): written
  retention policy, security program, separate consent for disclosure to
  third parties, the direct-notice content to parents.
- **Teen state laws** (`requirements-matrix.md` §"13–17").
- **The AI provider's terms for minors,** and the 312.8(c) assurance.
- **App-store "unknown persons" questions** for chat.
- Whether **teams mixing children and unrelated adults** are permissible at
  all. The proposed default is **not**: per-team guardian consent, no chat,
  no photos.

### Migration and rollback

- **Schema:** existing tables. Additive only (`consent_scope` details,
  `guardian_access_request`) if needed, each with deletion rules (the FK
  classification test forces it) and `lock_timeout` in both directions.
- **Flag:** `STREAKFIT_AGE_POLICY = off | family`.
- **Rollback is asymmetric.** Once a child is enrolled under a consent
  promise, turning enforcement **off** would break the promise to that
  guardian.
  - So the rollback after the first enrolment is **"freeze new enrolments"**,
    not "disable enforcement".
  - Disabling enforcement after enrolment would need an owner decision and
    notice to guardians.
- **Deleting a guardian's account** revokes their links and consents; the
  child keeps any other guardian's. With no guardian left, the child account
  is frozen. That is a rule for `delete_user_account`, with tests.

### Tests before release

| # | Test |
|---|---|
| B1 | Every capability route × every band × consent present/absent/revoked: decision and `PermissionAudit` match `can()`; route list asserted against the URL map |
| B2 | A child account cannot be created except by a verified guardian; no self-registration path accepts a child answer |
| B3 | VPC: the method is recorded; `evidence_ref` is never the evidence; a test double for the vendor only in tests |
| B4 | Revocation takes effect on the next request (no cached permission) |
| B5 | Two guardians: independent links, one revocation doesn't affect the other's grants |
| B6 | Ask Rickie: a child is denied with consent present while the assurance flag is unset; allowed only with both |
| B7 | Payment never grants (existing test); a sponsor is not a guardian |
| B8 | Guardian access request: logged; produces the export; no live conversation feed exists |
| B9 | Guardian account deletion: links and consents revoked; the orphaned child is frozen |
| B10 | Child in a team: per-team consent; chat and photo routes refuse; an invite code alone never admits a child |
| B11 | Existing NULL accounts: screen; child answer → frozen; deletion dry run |
| B12 | Flag `off`: the whole existing suite unchanged |
| B13 | `make uicheck` for every new screen; `verify_all` module with throwaway guardian+child accounts, cleaned up |

---

## Comparison

| | Option A — adults-only | Option B — family |
|---|---|---|
| Build size | Small: a screen, an interstitial, `can()` wiring, a deletion job | Large: VPC (likely a vendor), guardian flows, consent UI, review right, team rules |
| New data | One band | Band, links, consents, VPC reference, access log |
| Ask Rickie for minors | Never | Never for children until the 312.8(c) assurance; teens by owner policy |
| Existing NULL accounts | Interstitial; under-18 frozen, then deleted | Same, plus a guardian "claim" path **[legal]** |
| Rollback | Flag off restores today | After the first enrolment: freeze only |
| Fits "family habit game" | Only as adults-in-a-family | Yes |
| Legal review | Directed-to-children analysis, screen wording | Full COPPA program, VPC method, teen laws, provider terms |

**Common first step** (useful under either option, and harmless under both):

- wire `can()` into the capability routes behind `STREAKFIT_AGE_POLICY=off`
  (no behaviour change), with the URL-map test A3/B1;
- build the neutral screen without activating it.

That turns the policy into a switch the owner can flip once the decision and
the review are done. Nothing is activated without that decision.

## Owner decisions

1. **A or B.** Or A now, B later: A's screen and wiring are a subset of B's.
2. The screen wording, and the teen defaults (B).
3. The VPC method and vendor (B).
4. The notice period and process for under-18 or child accounts found among
   existing ones. This is irreversible deletion, so it gets a separate go-ahead.
5. Commissioning the **[legal]** review items above before activation.
