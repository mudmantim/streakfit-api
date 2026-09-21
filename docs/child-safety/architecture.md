# Child-account architecture

**Status: foundations implemented, policy undecided.** The permission layer
exists and denies by default. No age screen exists, nothing writes a
`GuardianLink`, and no route consults `can()` yet — deliberately, because the
decisions those would encode are still open. See §7.

---

## 1. Three bands, and why those numbers

| Band | Boundary | Why this number, not one we liked |
|---|---|---|
| `child` | under 13 | COPPA's threshold. Below it, verifiable parental consent is required before collecting personal information. |
| `teen` | 13–17 | Outside COPPA entirely. Reached by state law, and by Anthropic's Usage Policy, which defines a minor as anyone under 18 "regardless of jurisdiction". |
| `adult` | 18+ | Today's behaviour, unchanged. |
| `NULL` | not asked | **Treated as a child.** Every existing account is NULL. |

That last row is the one that matters most in code. Treating NULL as adult
would grant the entire existing user base adult standing *by doing nothing*,
which is the hardest kind of bug to notice.

**A band, never a date of birth.** The product's strongest privacy property
today is that it holds no email, no real name, no DOB and no location. A DOB
would immediately be the most sensitive column in the database, and every rule
below is expressible from a band.

## 2. Guardianship is its own object

```
GuardianLink(child_user_id, guardian_user_id, method, evidence_ref,
             established_at, revoked_at)
Consent(child_user_id, guardian_link_id, capability, method,
        granted_at, revoked_at)
```

Kept separate from `User` so that **who pays**, **who is on this team** and
**who may authorise** can never collapse into each other. A Plus subscription,
a sponsorship, a team invite or a shared household establishes none of it.

- `method` records **how** the link was made. A consent record that cannot say
  how it was obtained is not evidence of anything.
- `evidence_ref` is an **opaque pointer** — a receipt id, a verification id.
  Never the evidence: no ID images, no card numbers, no signed forms live in
  this database.
- **Multiple guardians are supported.** Two parents, or a parent and a
  grandparent, hold independent links. Revoking one does not revoke the
  other's decisions — tested.
- **Guardians need no Plus.** Nothing in the model or in `can()` reads a
  payment field.

**Consent is per capability**, not one blanket flag: "you may use teams" and
"you may send free text to a third-party model" are different decisions and a
guardian should not have to take them together.

**Absence of a row is denial.** There is no pending or assumed state.

## 3. One function decides

Everything goes through `can(user, capability) -> (allowed, reason)`.

Not for tidiness. The failure this product has already had **twice** is a rule
that lived in one place and not another — a display name Rickie honoured and
the team roster did not; a join boundary applied to the message list but not
to the photo bytes. A permission in two places is a permission that will
disagree with itself.

It returns a reason as well as a boolean, because "no" and "no, and here is
what would change it" are different products.

**No payment field is consulted anywhere in it.** Plus and sponsorship buy
allowances; they do not buy age. Asserted by a test parametrised across every
capability, because the tempting shortcut in any quota check is
`if user.is_plus: allow` — and there, that hands a child everything for $4.99.

## 4. The legal blocker is a gate, not a note

`CAP_ASK_RICKIE` is **hard-blocked for children regardless of consent**,
because of § 312.8(c): we hold no written assurance from the AI provider, and
consent is not the thing that is missing. It opens only when
`STREAKFIT_CHILD_AI_ASSURANCE_ON_FILE=1`, and setting that flag is a claim
that the assurance exists.

A test proves the gate **opens**, so it is wired to something rather than
hard-coded to refuse.

## 5. Teams and communication — designed, not enabled

The existing boundaries stay and are the foundation: no stranger discovery,
invite codes shared out of band, no direct messages, no content from before
you joined, and an abandoned team refuses entry.

Proposed for children, **not implemented**:

| Capability | Proposed child default |
|---|---|
| Join a guardian-approved team | Consent per team, not once globally |
| Create a team | Guardian consent |
| Team chat (free text) | **Off** until the Play "unknown persons" question is answered |
| Team photos | **Off** at launch — the hardest COPPA surface and the least reversible |
| See pre-join history | Already impossible for anyone |

**An invite code must never establish that an adult may talk to a child.**
That is the load-bearing sentence. A private team of eight is not eight
trusted adults, and the design must assume a teammate is a stranger to the
child's guardian.

**Not built, and it is the largest gap in the whole product:** there is **no
blocking, reporting or moderation anywhere** — zero routes. A child in a team
with an adult behaving badly has no in-app recourse. Nothing about mixed-age
teams should be enabled before that exists.

## 6. Age-appropriate Rickie — building on what exists

The crisis-response work (`docs/safety/crisis-response-protocol.md`) already
covers self-harm, abuse, bullying, emergencies, the refusal to keep secrets,
and the refusal to be the only person somebody talks to. That stands.

What a child-specific architecture adds, **none of it implemented**:

- Anthropic's **child-safety system prompt**, which their Guidelines require.
- **AI disclosure**, a **break reminder**, and an explicit sexual-content rule
  — three of the four SB 243 / New York duties still outstanding.
- **Minimising what leaves.** The context block already sends only a safe
  name, streak, level and mission count — no username, no email, nothing
  identifying. The free text is the exposure and always will be.

**On guardian visibility, the point the brief is right to press:** COPPA
§ 312.6 gives a parent the right to *review* their child's information, so for
under-13s a guardian access path is a **requirement**, not a values choice.
But it must be a **deliberate, logged access request**, not a live feed —
because **the guardian is not always the safest person to notify.** A child
disclosing abuse may be disclosing it about the person who would receive the
notification.

So: **no automatic disclosure of a conversation to anyone** because a child
raised something sensitive. What the right escalation is instead — and whether
any automated escalation is appropriate at all without a human on the other
end — needs a child-safety specialist, not an engineer. Recorded as an open
question rather than guessed at.

## 7. What is deliberately not built

Each of these is blocked on a decision, not on effort:

| Not built | Blocked on |
|---|---|
| Age screen | Whether a self-declared **band** is a compliant neutral screen, or whether month-and-year is the floor |
| Any VPC flow | Which approved method, and its cost and drop-off |
| Guardian dashboard | What a guardian may see — §6 |
| Teen defaults | Owner decision. `can()` currently says "teen policy not set" and denies, which is the safe direction to be wrong in |
| Child Ask Rickie | The § 312.8(c) assurance |
| Blocking / reporting | Nothing; this one is only unbuilt for want of time, and it gates mixed-age teams |

**No cosmetic toggle exists anywhere.** Every control described here is a
server-side check or it does not exist.
