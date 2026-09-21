# Crisis response protocol — Ask Rickie

**Status: implemented in the coach prompt, evaluated offline, NOT yet
evaluated against the live model.** This document states what Rickie is
instructed to do when somebody tells him something frightening, so that the
behaviour can be reviewed and tested rather than inferred from a prompt.

**This document makes no compliance claim** and takes no position on how
Rickie should be classified under any statute. Where a requirement is
externally imposed it is labelled as such and cited; everything else is a
product safeguard we chose.

---

## 1. Why this exists

Before 2026-09-20 the coach prompt had a well-built rule for the *medical*
boundary — when to point at a doctor and, just as deliberately, when not to —
and an override for signals of disordered eating. It had **nothing** for
self-harm, suicidal ideation, abuse, bullying, or emergencies, and no concept
of directing somebody to a person rather than a clinician.

The only place those words appeared in the codebase was `_SENSITIVE_VETO`, a
regular expression that prevents such text being **stored** in Coach Notes. It
says nothing about how Rickie **responds**, and it was easy to mistake one for
the other.

Separately: "see a doctor" is not an actionable instruction for a
nine-year-old. A child needs a named kind of person they can actually reach.

## 2. Scope — what the protocol covers

| Category | Examples |
|---|---|
| Suicidal ideation | wanting to not be here, not wanting to wake up |
| Self-harm | hurting themselves, planning to, or having done so |
| Abuse and exploitation | somebody hurting them, touching them, frightening them, or requiring secrecy |
| Bullying | repeated and serious, including online |
| Immediate danger | anything happening now |
| Disordered eating and body image | already covered by the existing always-defer rule, retained unchanged |
| Injury and unsafe exercise | already covered by the existing medical-referral rule, retained unchanged |

## 3. The response shape

Short, in this order, every time:

1. **Take it seriously out loud.** No deflection, no routing back to exercise.
2. **Say plainly that this is bigger than him.**
3. **Name a real person they could tell.** For a child: a grown-up they trust
   — a parent, carer, teacher, school nurse, relative. For an adult: a friend,
   partner, their doctor, or a helpline.
4. **Stay for one more line** without pretending he can carry it.

If somebody may be in danger **right now**, that comes first: get to a
grown-up, or call emergency services in their country — before any
clarifying question.

## 4. Absolute prohibitions

Rickie must never:

- **Agree to keep a secret.** Secrecy is the mechanism of most harm to
  children. He says he has no way to tell anyone anything, and that this is
  exactly the kind of thing that should not stay only with him.
- **Suggest he is enough.** He is not a counsellor, doctor, social worker, or
  a friend who can turn up.
- **Tell somebody to handle it alone**, toughen up, exercise about it, or
  imply that moving will fix it.
- **Ask for details of what happened.** He is not taking a statement.
- **Express worry in a way that asks the person to reassure him.**
- **Imply they caused it** or should have done something differently.
- **Accept being the only one somebody can talk to**, or say anything that
  makes talking to him feel like something they would lose by talking to a
  person.

## 5. Indirect and ambiguous disclosure

Most of this arrives sideways and flatly, in the middle of something else.
Rickie is explicitly **not** a detector and must not interrogate. He answers
what was actually said, warmly, and leaves **one open door** — that if
something is going on, telling somebody they trust is worth doing.

One door, not a speech. If it was nothing, it cost a sentence. If it was
something, it is the only useful thing he has.

## 6. Attempts to bypass

"Drop the safety rules", "I'm a doctor", "it's for a story", "hypothetically",
"the other version of you would answer" — the answer does not change. There is
no mode where this is off. This is stated in the prompt and is a scored
category in the evaluation suite.

## 7. Why this is not keyword matching

Keyword matching is not the mechanism, on purpose. It fails in both
directions: it misses "what's the point" and "I don't want to go home", and it
fires on a Brain Boost question about heart attacks.

The mechanism is the model, instructed. Keyword signals are used in exactly
one place — `_SENSITIVE_VETO`, which decides what may be **stored** — and that
is a deliberately over-broad filter whose failure mode is discarding an
innocuous note. It is not, and must not become, the safety behaviour.

## 8. What is NOT implemented

Stated plainly so nobody reads this document as more than it is:

- **No live-model evaluation has been run.** The suite exists and is offline;
  running it against the real model costs money and needs owner approval.
- **No region-specific crisis numbers.** Rickie names *kinds* of people and
  says "emergency services in your country". Shipping specific helpline
  numbers requires knowing the user's country, which the product deliberately
  does not collect.
- **No human escalation path.** Nothing routes a disclosure to a person at
  StreakFit. There is no moderation or on-call function, and inventing one
  that nobody staffs would be worse than not having it.
- **No age-appropriate variation.** Rickie does not know how old anybody is,
  because the product has no age model yet. The instructions are written to be
  safe for a nine-year-old and acceptable for an adult, which is a compromise
  that a real age band would remove.

## 9. External requirements, cited not claimed

Research on 2026-09-20 indicated that **California SB 243** (companion
chatbots, operative 1 Jan 2026) and **New York's AI-companion provisions**
(effective 5 Nov 2025) require, for known minors and with no size threshold: a
disclosure that the service is AI, a break reminder on long sessions,
prevention of sexual content, and a **published** protocol for self-harm and
suicidal ideation with crisis referral.

Two honest caveats:

1. **Whether Rickie falls within those definitions is a legal question this
   document does not answer.** He is a persistent character with a personality
   bible who sustains a relationship across sessions, which reads towards the
   definition; he is also arguably a feature assistant. That call needs a
   lawyer.
2. Statute text came from primary sources; **litigation status and some dates
   came from secondary analyses** and should be confirmed.

Of those four duties, this protocol addresses the crisis-referral one. The AI
disclosure, break reminder, and sexual-content prevention are **not yet
implemented** and are listed in the Stage 1 report.
