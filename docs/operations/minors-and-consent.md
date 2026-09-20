# Minors and parental consent — what exists, and what does not

**Status: UNDESIGNED. This document does not establish compliance with anything,
and passing tests do not either.** It is an inventory of what the product
actually does today, written because the intended first tester is a child and
the gap should be visible before she is invited rather than after.

Nothing here is legal advice. Where it says "unresolved", that means a decision
is needed from the owner, possibly with advice this repository cannot supply.

## What the product already does well

These are real and verified by tests, and they materially reduce the surface:

| | |
|---|---|
| **No email address** | Registration is username + password. Nothing is sent anywhere, no verification mail, no address on file. |
| **No real name** | Not collected, not asked for. The display name is optional, and an email address is *refused* as one (`tests/test_display_name.py`). |
| **No date of birth** | Not collected — which is also why there is no age gate. See below. |
| **No phone number** | Not collected. |
| **No location** | Not collected. Photo EXIF, including GPS, is stripped before storage and the file is rebuilt segment by segment (`tests/test_photo_privacy.py`, 10 tests). |
| **Conversation retention is bounded** | ~10 turns, 30-day age limit, swept independently of the user (`docs/operations/privacy-retention.md`). |
| **Coach Notes store no user text** | A closed vocabulary of canonical tokens only — never the words a child typed (`tests/test_coach_notes_boundary.py`). |
| **Deletion is real and caller-scoped** | Forget Conversations and account deletion both work and reach only the caller's own rows. |
| **Photo sharing is honest about screenshots** | The composer says plainly that anyone who can see a photo can screenshot it. |

The data-minimisation posture is genuinely good: for most of the categories a
consent regime is concerned with, the answer is "we never had it."

## What does NOT exist

**1. No age gate.** Nothing asks how old a user is, at registration or ever. The
app therefore cannot know whether a given account belongs to a child, and cannot
behave differently if it does.

**2. No parental consent mechanism.** There is no verifiable-consent flow, no
parent account, no link between a child's account and an adult's. Sponsorship
(one Plus subscriber paying for up to five others) is a *billing* relationship
in the proposed model and explicitly **not** a supervisory one — a sponsor is
told nothing about the sponsored person's conversations, by design.

**3. No child-specific data handling.** Every account is treated identically.
There is no reduced-retention mode, no restriction on which features a minor
reaches, and no way to disable Ask Rickie for one account while leaving the rest
of the app working.

**4. Conversation text does leave the service.** This is the item most likely to
matter. A child's Ask Rickie messages, Rickie's replies from the recent window,
their username and their streak and level numbers are sent to Anthropic to
generate each reply. The data export says so in those words. There is no
mechanism for a parent to see, approve, or opt out of that separately from the
rest of the app.

**5. Team membership is not supervised.** A child can create or join a team with
an invite code from anyone who has it. There is no stranger discovery — codes
are shared out-of-band, which is a real protection — but nothing verifies who is
on the other end, and photos can be shared into a team.

## Unresolved decisions, before a minor is invited

These are the owner's calls. They are listed in the order that matters most:

1. **Is the first tester's account supervised in practice?** A parent sitting
   beside a child using their own device is a materially different situation
   from a child with an independent account. The product cannot tell the
   difference; the owner can.

2. **Should Ask Rickie be switchable off per account?** This is the one gap with
   a cheap technical answer. The coach already fails closed to a friendly 503
   when no key is configured; a per-account flag would reuse that exact path, so
   a parent could leave the whole app working with the model turned off. It is
   not built, and it is the single change that would most reduce the surface for
   a child account.

3. **What is the minimum age the product is willing to state?** Content carries
   `min_age` (9 / 13 / 16) and the library is written for nine, but the *account*
   has no stated minimum anywhere.

4. **Does an age gate help or hurt?** Asking for a date of birth would collect a
   category of data the product currently avoids entirely. That trade deserves a
   decision rather than a default.

5. **Backup retention is still UNKNOWN**, so "deleted" cannot be qualified for a
   child's data any more than an adult's. See `docs/operations/privacy-retention.md`.

## What must not be claimed

- That StreakFit is COPPA-compliant, GDPR-compliant, or compliant with any
  other regime. No assessment has been done.
- That passing tests establish compliance. They establish that specific
  behaviours are what the code says they are.
- That data minimisation is the same thing as consent. It reduces what is at
  stake; it does not answer who agreed to it.
