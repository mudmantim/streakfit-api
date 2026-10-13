# Child safety, phase 0: implementation plan (server-side enforcement, switched off)

**Status: implemented on branch `feature/pw1-and-child-safety-phase0`, not
released, not activated.** `STREAKFIT_AGE_POLICY` defaults to `off`. Written
2026-10-12 against release candidate `1d7b2f2`. It is the first stage of the
family-service direction in `phased-plan.md`.

- Code: `requires_capability`, `_age_policy_mode`, and the self-check
  `child_safety.age_policy`.
- Tests: `tests/test_capability_routes.py`.

**Where the implementation differs from this plan, and why:**

1. **Only denials are audited.** The plan wrote a `PermissionAudit` row per
   decision under `enforce`. Team messages are polled, so that would be a
   write per poll for every adult, recording nothing new.
2. **A deleted account's token gets 404 under `enforce`,** instead of
   falling through to the route. Falling through would let `/api/coach` call
   the provider for an account that no longer exists. Under `off` and
   `shadow`, behaviour is unchanged.
3. **`_safe_display_name(for_child=True)` is deferred to phase 4.** It would
   be unused until then, and phase 0 changes nothing that reaches the AI
   provider. The phase-4 gate test is unchanged.
4. **The URL-map test classifies by path.** Any route whose path mentions
   coach, team(s), photo, message, chat or rickie must carry the guard, or
   be in an exempt list with a written reason. Admin routes are excluded.
   After the review corrections, that covers 10 guarded routes and 11 exempt
   ones.
5. **Review corrections (2026-10-12):**
   - **Moments no longer pass stored metadata through.**
     `_public_moment_metadata` allow-lists the keys per moment type and checks
     each value against what the server writes:
     - a preset challenge title;
     - a real campfire stage;
     - an integer total.

     Old `photo_shared` rows carrying a caption (written before R1 D4) can no
     longer reach any reader, scrubbed or not. Reproduced and fixed:
     `test_the_original_exploit_a_legacy_caption_no_longer_reaches_a_refused_member`.
   - **Team challenges now require `team_chat`.** Policy below.

Phase 0 must not change anything for any account:

- no restriction is activated;
- no eligibility changes;
- no new personal data is collected;
- no screen is added.

It only puts the enforcement point in place. Later phases then only need to
turn the policy on, rather than write code under pressure.

## What already exists (verified in `app.py` at `1d7b2f2`)

| Piece | Where | State |
|---|---|---|
| Capabilities: `teams`, `team_chat`, `team_photos`, `ask_rickie` | `CAP_*`, `CAPABILITIES` | Defined |
| `can(user, capability, record=False)` → `(allowed, reason)` | `app.py` `def can` | Default deny. Adult allowed. NULL band treated as a child. Teen denied ("teen policy not set"). Child needs consent. Ask Rickie hard-blocked for children unless `STREAKFIT_CHILD_AI_ASSURANCE_ON_FILE=1`. Never consults payment. |
| `user.age_band`, `GuardianLink`, `Consent`, `PermissionAudit` | models + migration `08681a9bd9f9` | In the schema; nothing writes them |
| Tests | `tests/test_child_account_permissions.py` (15) | The decision table |
| **Callers of `can()` from routes** | — | **None.** This is the gap. |

Because NULL means "child", calling `can()` today without a switch would deny
Ask Rickie and teams to **every existing account**. The switch is not
optional.

## 1. Server-side enforcement architecture

### The switch

`STREAKFIT_AGE_POLICY`, read on each request through one helper,
`_age_policy_mode()`:

| Value | Meaning |
|---|---|
| unset / `off` (default) | `can()` is not consulted. Behaviour identical to today. |
| `shadow` | `can()` is evaluated and a **deny is logged** (`event=capability_shadow_deny capability=… reason=…`), but the request proceeds. No `PermissionAudit` row. It lets the owner measure, using logs only, what turning it on would affect. |
| `enforce` | A deny answers `403 {"error":"capability_denied","capability":…,"reason_code":…}` and writes a `PermissionAudit` row. |

**Rules for the switch:**

- Any value other than these three is a configuration error, and the app
  fails safe:
  - it treats the value as `off` for behaviour;
  - `/api/verification/self` reports it as FAIL.

  Treating a typo as `enforce` would silently lock out every existing account;
  treating it as `off` and flagging it doesn't.
- `shadow` logs carry `user_id`, `capability` and a reason code, and nothing
  else (no band, no message text).
- **`enforce` must not be set in phase 0.** It is an owner action that
  belongs to phase 1/2. The self-check reports the mode, so it can't change
  unnoticed.

### The decorator

```python
def requires_capability(capability):
    """Route guard. With the policy off it does nothing — by test."""
    def wrap(fn):
        fn._streakfit_capability = capability         # read by the URL-map test
        @functools.wraps(fn)
        def inner(*a, **kw):
            mode = _age_policy_mode()
            if mode == 'off':
                return fn(*a, **kw)
            user = _current_user_or_none()             # one PK read, cached on g
            if user is None:                           # deletion contract unchanged
                return fn(*a, **kw)
            allowed, reason = can(user, capability, record=(mode == 'enforce'))
            if not allowed:
                if mode == 'enforce':
                    db.session.commit()                # the audit row
                    return _capability_denied(capability, reason)
                app.logger.info("event=capability_shadow_deny user_id=%s capability=%s reason=%s",
                                user.id, capability, _reason_code(reason))
            return fn(*a, **kw)
        inner._streakfit_capability = capability
        return inner
    return wrap
```

**Placement:** below `@jwt_required()` and **above** the route's rate
limiters' effect. A denial must not spend a user's Ask Rickie daily
allowance; `deduct_when=_coach_reached_provider` already guarantees that,
because a denial never reaches the provider. A test pins it.

**Ordering inside `/api/coach`:** the guard runs before the handler body. That
means before:

- `_build_rickie_context`;
- `_load_coach_note_block`;
- `_load_coach_messages` (which also expires old turns);
- the provider call;
- the turn save;
- Coach Notes extraction.

A denied request stores nothing and sends nothing. A test asserts this, with
the provider client replaced by a spy.

### Routes and capabilities

| Route | Capability | Notes |
|---|---|---|
| `POST /api/coach` | `ask_rickie` | |
| `POST /api/teams` (create) | `teams` | |
| `POST /api/teams/<id>/join` | `teams` | |
| `GET /api/teams/lookup/<code>` | `teams` | It reveals a team's name and size to anyone with a code |
| `GET`/`POST /api/teams/<id>/messages` | `team_chat` | |
| `POST /api/teams/<id>/photos`; `GET …/photos/<public_id>` | `team_photos` | |

**Deliberately not guarded,** and listed in an allow-list that the test reads,
each with its reason:

| Routes | Reason |
|---|---|
| `DELETE /api/coach/memory` ("forget our conversations"), `DELETE /api/me`, data export | Always available to a denied account |
| `POST /api/teams/<id>/leave`, member removal, `DELETE …/photos/<id>` | Leaving and removing must never be blocked |
| `GET /api/teams`, team detail, campfire, moments, challenges | Membership that already exists; `phased-plan.md` phase 2 keeps existing teams working. Phase 3 revisits it for children |
| Admin and moderation routes | Not user capabilities |

### The URL-map test (the control that keeps it complete)

`tests/test_capability_routes.py` walks `app.url_map`. For each endpoint it
asserts:

- one of three things holds:
  - the view carries `_streakfit_capability`;
  - the view is in `_CAPABILITY_EXEMPT`, with a reason string;
  - the view is in an explicit "not a capability route" list;
- **a new route under `/api/teams/`, `/api/coach` or any path containing
  `photo`/`message`/`chat` fails CI** until someone classifies it.

This mirrors the FK-classification test for deletion rules.

## 2. Child-account restrictions (what phase 0 encodes; nothing applied)

- No code change to `can()`'s decision table. It already encodes:
  - default deny;
  - NULL = child;
  - teens pending;
  - consent per capability;
  - the Ask Rickie hard block;
  - payment never grants.
- **Add:** `_reason_code(reason)`, a stable short code
  (`age_not_established`, `teen_policy_not_set`, `no_guardian_consent`,
  `child_ai_assurance_missing`, `adult`). Responses and logs use codes, not
  the prose. The prose stays for `PermissionAudit.reason`.
- **Add (prepared, unused):** `_safe_display_name(user, for_child=False)`.
  With `for_child=True` it never falls back to the username. Phase 4 uses it.
  Phase 0 only adds the parameter and a test; every current caller keeps the
  default, so the prompt is byte-identical (a test compares
  `_build_rickie_context` output before and after).
- **Child prompt variant:** prepared as a separate constant, evaluated with
  the Rickie eval suite (≈ $0.48 per run, owner-approved spend). It is not
  selected by any code path in phase 0.

## 3. Guardian consent requirements (data model ready; no flow in phase 0)

The existing schema is enough for phase 3:

- `GuardianLink(guardian_user_id, child_user_id, method, evidence_ref,
  revoked_at)`;
- `Consent(guardian_link_id, child_user_id, capability, granted_at,
  revoked_at)`.

Requirements carried forward, with tests to be written in phase 3, not now:

1. A link is created only through a verified-parental-consent method chosen by
   **[LEGAL]**. `method` and `evidence_ref` are mandatory; `evidence_ref`
   points to the vendor's record and never stores the evidence itself.
2. Consent is per capability and per child. Revoking a link or a consent takes
   effect on the next request; `_active_consent` already joins both
   `revoked_at` columns.
3. Two guardians are independent: revoking one link does not revoke the
   other's consents.
4. A team invite code never admits a child, whatever consent exists, until the
   mixed-age team rules are decided (**[OWNER] + [LEGAL]**).

## 4. Ask Rickie access controls (four conditions; phase 0 builds the first)

For a child account, Ask Rickie needs all four:

| # | Condition | Phase |
|---|---|---|
| 1 | The route guard calls `can(user, 'ask_rickie')` before any read or send | **0** (this plan) |
| 2 | `STREAKFIT_CHILD_AI_ASSURANCE_ON_FILE=1`, set only once the § 312.8(c) written assurance is on file | 4, **[LEGAL]** |
| 3 | An active `ask_rickie` consent from a linked guardian | 3/4 |
| 4 | The child prompt variant and `for_child=True` context (no username) | 4 |

Plus **[OWNER]** sign-off. Adults are unaffected in every phase, and teens
depend on the teen defaults.

## 5. Existing-account migration considerations

- **Phase 0 needs no migration and no data change.** All columns exist.
- **Every existing account has `age_band IS NULL`.** That is why `enforce`
  must not be turned on before phase 2's interstitial exists: under `enforce`,
  NULL is denied, which would remove Ask Rickie and teams from every current
  user overnight.
- **`shadow` is safe to turn on** whenever the owner likes. It gives the
  phase-2 dry-run count from logs, without querying user data:
  - how many distinct accounts would be denied per capability per day.
- Before phase 2, the owner decides:
  - the notice text;
  - whether existing accounts keep Ask Rickie until they answer
    (`phased-plan.md` says paused);
  - what happens to an existing account that answers "under 13" (**[LEGAL]**:
    claim vs delete).
- **Rollback of phase 0:** redeploy the previous build. There is nothing to
  undo in data. With the policy at `off`, there is also no behaviour to roll
  back.

## 6. Legal-review dependencies

| Item | Blocks | Phase 0 blocked? |
|---|---|---|
| § 312.8(c) written assurance from the AI provider | Ask Rickie for children (phase 4) | No |
| Age-screen wording; whether a band is a neutral screen | Phase 1 | No |
| "Directed to children" analysis of content and marketing | Phase 1 | No |
| Claim vs delete for existing under-13 accounts | Phase 2 | No |
| VPC method and vendor | Phase 3 | No |
| Mixed-age team rules | Phase 3 | No |
| Teen defaults (**[OWNER]**, informed by state law) | Phases 1–2 | No |

Phase 0 has no legal gate: it changes no behaviour and collects nothing. It
does depend on code review.

## 7. Phase 0 test list

| # | Test | Layer |
|---|---|---|
| C1 | Policy `off` or unset: the whole suite passes unchanged; every guarded route behaves identically for NULL, teen, child and adult accounts | U |
| C2 | Policy `off`: `can()` is never called (spy) | U |
| C3 | Policy `shadow`: a NULL account's request succeeds; one `capability_shadow_deny` log line with a code, no band or text; no audit row | U |
| C4 | Policy `enforce` (test only): NULL → 403 `capability_denied` + one audit row; adult → 200; a child with consent but no assurance → Ask Rickie 403 | U |
| C5 | `enforce` + `/api/coach` denied: the provider spy is not called, no turn row, no note row, and the daily Ask Rickie allowance is not spent | U |
| C6 | `enforce`: export, deletion, "forget our conversations", leave team and delete photo still work for a denied account | U |
| C7 | Unknown policy value: behaves as `off`; the self-check reports FAIL | U |
| C8 | URL-map test: every route classified; a synthetic unclassified `/api/teams/x` route makes it fail | U |
| C9 | `_build_rickie_context` output is byte-identical to before for an adult; `for_child=True` never contains the username, even a name-like one | U |
| C10 | `verify_all`: the self-check reports `age_policy: off` | V |
| C11 | Migrations: no change (`test_migrations*.py` green on both dialects) | U, PG |

Verification-suite note (CLAUDE.md standard): the self-check line belongs to
the existing `verification` subsystem module. No production-visible behaviour
changes, so there is no new end-to-end user-flow check until phase 1.

## Policy: team challenges and the team thread

**Decision implemented:** starting or completing a *team* challenge requires
`team_chat`, the same as posting a message.

- A team challenge **is** a thread object:
  - starting one writes a chat row ("Started a challenge: 20 squats",
    attributed to the starter);
  - completing one makes Rickie post in the thread;
  - the card other members act on is rendered from that chat row;
  - a member who cannot read the thread cannot see a challenge to complete.
- So a "challenge without the announcement" would be a challenge nobody can
  see, and "announcement without chat permission" is posting to the thread
  under another name. The review reproduced the second: a member refused
  `team_chat` added a line to the thread.
- **Preserved:**
  - anyone allowed the team thread (adults, and children whose guardian
    consented to `team_chat`) starts and completes team challenges as
    before;
  - **personal challenges** (`/api/challenges`) are not team features and
    are untouched;
  - with the switch `off`, nothing changes for anyone.
- **Tested:**
  - `test_a_member_refused_team_chat_cannot_post_into_the_thread_through_a_challenge`
  - `test_a_child_with_chat_consent_takes_part_in_challenges`
  - `test_personal_challenges_are_untouched_by_the_policy`

**For a later owner decision:** a separate `team_challenges` capability,
which a guardian could grant without free-text chat. Challenges are preset-only
with no free text, so that is a defensible product option. It needs a
challenge view outside the thread, so it is a feature, not a fix.
