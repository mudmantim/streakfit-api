# Child safety — recommended phased implementation (family-service path)

**Status: plan only.** Nothing here is activated. No age gate, no new personal
data, no account frozen, no eligibility changed. Written 2026-10-12. It builds
on `policy-options.md` (option B, family service) and `architecture.md`.

- **Decision gates** are marked **[OWNER]** and **[LEGAL]**. A phase does not
  start until its gates are cleared.
- **The invariant across every phase: a child account can never reach Ask
  Rickie** unless the server can prove all four of the following:
  - the § 312.8(c) written assurance from the AI provider is on file;
  - the child-safety prompt and disclosures are live and evaluated;
  - a verified guardian has consented to that specific capability;
  - legal sign-off for the release.
- **The gate lives in `can()` on the server.** A UI toggle is not a control.

---

## Review of option B: what to keep, what to change

**Keep:**

- the band model, with NULL treated as a child;
- `GuardianLink` and `Consent` as separate rows;
- consent per capability;
- the hard block that no consent can lift;
- "payment never grants" (already tested);
- guardian review as a logged request, not a live feed.

**Change or add:**

1. **Order of phases.** Option B as written enables several things at once.
   Split it so that each phase is reversible on its own until the first
   child is enrolled. After that, rollback can only *freeze* (see
   `policy-options.md`, "Rollback is asymmetric").
2. **Ask Rickie is the last capability, not one of several.** It is the only
   one that sends a child's free text to a third party. Teams (without chat
   or photos) can come first.
3. **The `can()` check must run before anything is stored or sent:**
   - before the provider call;
   - before the turn is saved;
   - before Coach Notes extraction.

   And:
   - "Forget our conversations" and the data export must stay available to a
     denied account;
   - a denial must never block deletion.
4. **Guardian-created child accounts need a recovery path** that doesn't
   depend on contact details: the guardian resets the child's password, under
   the same lock and token-version rules as `password-change.md`.
5. **Correct the stale statement** in `architecture.md` §5 ("no blocking,
   reporting or moderation anywhere"). Those now exist, and the mixed-age team
   rules should build on them.
6. **Teens:** `can()` currently denies them ("teen policy not set"). That is
   the right default, but it means switching on any policy flag will
   *remove* Ask Rickie and teams from self-declared teens until **[OWNER]**
   sets the teen defaults. That decision has to come before phase 2.

---

## Phases

### Phase 0 — wiring, no behaviour change (engineering only)

**What changes:**

- `can()` is called on every capability route:
  - `POST /api/coach`;
  - team create and join;
  - team chat;
  - team photo upload and view.
- All behind `STREAKFIT_AGE_POLICY=off`. With `off`, the routes skip the
  decision exactly as today. With anything else, `can()` decides.
- A test walks the Flask URL map, so a new capability route without a
  `can()` call fails CI.
- The child-safety system prompt, AI disclosure, break reminder and
  sexual-content rule are prepared as Rickie prompt variants:
  - covered by the Rickie eval suite (cost per run is known: about $0.48);
  - **unused**.

**What doesn't change:** no new data, no screens.

**Reversible:** yes. **Gates:** none beyond code review.

### Phase 1 — age band for new registrations only **[OWNER] [LEGAL]**

**Change:** a neutral age-band question at sign-up (`policy-options.md`
option A screen). Only the band is stored; no year.

| Answer | Result |
|---|---|
| Adult | As today |
| Teen | Per **[OWNER]** teen defaults |
| Under 13 | No account is created and nothing is stored; "ask a parent to set this up" |

**Existing accounts:** untouched; they keep today's behaviour.

**Gates:**

- **[LEGAL]** screen wording;
- whether a band is an acceptable neutral screen;
- the "directed to children" analysis;
- **[OWNER]** teen defaults.

**Reversible:** yes (flag off). Stored bands are harmless.

### Phase 2 — existing accounts **[OWNER] [LEGAL]**

**Change:** an interstitial asks existing accounts the same question.

- **Until it's answered:** Ask Rickie is paused for that account (`can()`
  denies NULL). Missions, streaks and existing teams continue. The data export
  and deletion are always available.
- **Under 13:**
  - the account stops collecting (frozen);
  - the guardian-claim path (phase 3) or deletion follows, per **[LEGAL]**
    advice on claiming pre-existing child data.
- **This changes eligibility for current users**, so it needs its own
  go-ahead, a dry-run count first, and notice text.

**Reversible:** yes, up to the first deletion. Deletions are not.

### Phase 3 — guardian-created child accounts, without AI **[OWNER] [LEGAL]**

**Change:**

- An adult account adds a child, with verifiable parental consent through an
  approved method. **[LEGAL]** decides the method; **[OWNER]** decides the
  vendor and cost.
- `GuardianLink(method, evidence_ref)` and per-capability `Consent` rows are
  written.

**Capabilities:**

| Capability | Phase 3 |
|---|---|
| Missions | Allowed |
| Guardian-approved teams | Per-team consent; no chat, no photos |
| Ask Rickie | **Still hard-blocked** (`_CHILD_HARD_BLOCKED`) |

Guardian review is through a logged export request (§ 312.6).

**Reversible:** after the first enrolment, freeze only.

### Phase 4 — Ask Rickie for children, only with every safeguard **[OWNER] [LEGAL]**

The gate opens for a specific child only when **all** of these hold:

1. **[LEGAL]** The provider's § 312.8(c) written assurance is on file. Only
   then is `STREAKFIT_CHILD_AI_ASSURANCE_ON_FILE=1` set; setting it is that
   claim.
2. The child-safety prompt variant is live for child accounts, with:
   - the AI disclosure;
   - the break reminder;
   - the sexual-content rule;
   - the crisis protocol (already exists).

   The Rickie eval suite must pass on that variant, with the cost recorded.
3. A linked guardian has granted `ask_rickie` consent for this child, and it
   is revocable instantly.
4. **Minimisation is checked.** Today `_safe_display_name()` falls back to
   the **username** when no display name is set and the username looks like
   a name (20 characters or fewer, no unsafe characters), and that name goes
   into the prompt.
   - For child accounts, the fallback is removed: the display name, or no
     name at all.
   - Plus streak, level and mission count.
5. **[OWNER]** release sign-off.

**Reversible:** revoking consent or unsetting the flag stops it on the next
request. A test proves this.

---

## Tests that gate each phase

| Phase | Gate tests |
|---|---|
| 0 | URL-map test (every capability route calls `can()`); flag `off` leaves the whole suite unchanged; prompt variants pass the Rickie eval |
| 1 | No user row and no log line for an under-13 answer; only the band is stored (column scan for any year/DOB); teen defaults as decided |
| 2 | NULL account: Ask Rickie 403 with reason, missions OK, export and deletion OK; dry-run report of affected accounts; frozen account collects nothing (writes refused) |
| 3 | Child creation only through a verified guardian; consent revocation takes effect on the next request; two guardians independent; an invite code never admits a child; chat and photo routes refuse children |
| 4 | Child + consent + **no** assurance flag → denied; flag + **no** consent → denied; both → allowed with the child prompt variant; revoke → denied on the next request; the context payload for a child never contains the username, even a name-like one |

---

## Decisions

| # | Decision | Owner / legal |
|---|---|---|
| 1 | Family service (option B) as the direction, phased as above | OWNER |
| 2 | Teen defaults (needed before phase 1) | OWNER |
| 3 | Age-screen wording and acceptability | LEGAL |
| 4 | "Directed to children" analysis of content and marketing | LEGAL |
| 5 | Treatment of existing accounts, incl. claiming vs deleting child data | OWNER + LEGAL |
| 6 | VPC method and vendor | LEGAL + OWNER |
| 7 | Obtaining the § 312.8(c) assurance from the AI provider | OWNER (request), LEGAL (review) |
| 8 | Mixed-age team rules | OWNER + LEGAL |
