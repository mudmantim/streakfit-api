# Requirements matrix — children, teens, platforms, provider

**Researched 2026-09-20.** Legend: **[LAW]** binding · **[GUIDE]** agency
guidance or enforcement discretion · **[CONTRACT]** platform/vendor terms ·
**[OPEN]** unresolved interpretation.

**Nothing here establishes compliance.** It narrows what to build and what to
ask a lawyer. Where a source could not be verified, it says so rather than
asserting.

---

## The one finding that changes the build

**[LAW] 16 CFR § 312.8(c)** — before releasing a child's personal information
to a service provider, the operator must take reasonable steps to determine
the provider can protect it **and obtain written assurances** that it will.

StreakFit has **no such assurance from Anthropic**. Until it does, sending a
known under-13's free text to that API is a violation on its face.

This is now encoded as a gate in `can()` rather than written down and hoped
for: `CAP_ASK_RICKIE` is hard-blocked for children **regardless of guardian
consent**, because consent is not the thing that is missing. It opens only
when `STREAKFIT_CHILD_AI_ASSURANCE_ON_FILE=1`, and setting that flag is a
claim that the assurance exists.

**Action for the owner:** ask Anthropic in writing for (a) the § 312.8(c)
assurance and (b) zero-data-retention on the Ask Rickie key. That is a
commercial negotiation, not a research question.

---

## Michigan — nothing in force

| Item | Status |
|---|---|
| Comprehensive privacy act (SB 359) | **[OPEN]** Reported out of committee Jun 2025; not passed |
| MI Kids Code (HB 5357) | **[OPEN]** Introduced Dec 2025; in committee |
| Companion-chatbot act (SB 760) | **[OPEN]** **Passed Senate 20–17, Apr 2026**; in House. Would bar companion chatbots from encouraging self-harm or disordered eating to minors |
| Children's Protection Registry Act (MCL 752.1061) | **[LAW]** In force, but only bars *advertising* age-restricted products. StreakFit sends no email and advertises nothing — does not apply |
| Consumer Protection Act (MCL 445.901) | **[LAW]** General UDAP. A false claim in our own privacy policy is actionable here |

**Being in Michigan buys nothing.** State privacy statutes attach to where
*users* are, not where the owner sits. The practical exposure is every state
with a StreakFit child in it.

## Federal — COPPA

- **[LAW]** Amended Rule fully in force since 22 Apr 2026. Adds: separate
  unbundled consent for third-party disclosure, a **written** children's
  security programme with a named coordinator, a **published** retention
  policy, and a ban on indefinite retention.
- **[LAW] § 312.5(c)(1)** — a guardian's contact information may be collected
  **without prior consent, solely** to give notice and obtain consent.
  **[GUIDE] FAQ C.9** adds the deletion trigger: if consent never arrives, the
  contact information must be deleted.
- **[GUIDE] FAQ D.7** — a neutral age screen must not telegraph the answer
  (no "you must be 13+"), and should resist back-button retries.
- **[GUIDE]** FTC Enforcement Policy Statement, 25 Feb 2026 — enforcement
  discretion, **not law**: collecting data *solely* to determine age, used for
  nothing else and promptly deleted, will not be pursued. This makes a
  third-party age check safe to add. **It does not lower the consent bar.**
- **COPPA does not reach 13–17 at all.**

## 13–17 — state law and the one that threatens the core loop

- **[LAW] Connecticut CTDPA as amended (SB 1295), effective 1 Jul 2026.**
  Teen band extended to 13–17. Flat ban — not a consent regime — on targeted
  advertising to and sale of a known 13–17's data. And it **prohibits "any
  system design feature to significantly increase, sustain or extend any
  minor's use of an online service."**

  **Streaks, daily-mission nudges and campfire mechanics are exactly the
  shape of feature that clause names.** This is the most directly threatening
  in-force provision found, and it is a product-design question rather than
  paperwork. **[OPEN]** Whether a streak that *never punishes a missed day*
  reads differently from one that does is precisely the argument, and it
  needs counsel.

- **[LAW] App Store Accountability Acts reach DEVELOPERS, not only stores.**
  Texas SB 2420 enforceable now (SCOTUS declined to block, Jul 2026);
  Louisiana live Jul 2026 with **no reliance safe harbour**; Utah delayed to
  2027. Duties: act on the store's age category, verify parental-consent
  status, notify the store of material changes. **These apply only if
  distributing through an app store** — and they are the strongest argument
  *for* doing so, because the store then supplies an age signal and a consent
  record we would otherwise have to build.

- **[LAW]** California SB 243 (companion chatbots, operative Jan 2026) and
  New York's AI-companion provisions (Nov 2025) reach **any operator of any
  size** for known minors under 18: AI disclosure, break reminders, no sexual
  content, and a **published** crisis protocol. Of those four, only the
  crisis protocol is done (`docs/safety/crisis-response-protocol.md`).

## Platforms — only if we ever ship to a store

- **[CONTRACT] Apple 5.1.4(b)** applies to *any* app a minor can use, **not
  only the Kids Category**, and names "the ability to chat" explicitly.
  **5.1.2** requires explicit permission before sharing personal data with
  third parties **including third-party AI** — a privacy-policy line is
  unlikely to suffice; Ask Rickie needs an in-app consent gate.
- **[CONTRACT]** StreakFit should **not** enter the Kids Category: it brings
  parental gates on every outbound link and a near-total ban on third-party
  analytics, for no benefit here.
- **[CONTRACT] Google Play** — declaring any under-18 band triggers the full
  Families Policy. **[OPEN]** The "must not target children if the main focus
  is chatting with people they do not know" rule has **no documented
  carve-out for invite-code-only private groups**. The reading that StreakFit
  is outside it is reasonable and **untested** — worth asking Google before
  submission, because the answer is a launch gate.
- **A web PWA avoids all of the above.** Shipping to a store is what pulls it
  in — alongside the age signals that would make consent easier.

## The AI provider — permitted, conditionally

- **[CONTRACT] Anthropic Usage Policy** (eff. 15 Sep 2025) defines a minor as
  **anyone under 18, regardless of jurisdiction**, and requires products
  serving minors to follow its Guidelines. **It sets no minimum end-user age
  for API-built products.** The 18+ rule is Claude.ai's, not the API's.
- **[CONTRACT] Guidelines for Organizations Serving Minors** (16 Mar 2026)
  require an age-verification system, content filtering, monitoring and
  reporting, **their child-safety system prompt**, and disclosure that the
  user is talking to an AI. Anthropic **reserves the right to audit and
  suspend**.
- **[OPEN]** Anthropic's Privacy Policy § 7 says its services are "not
  directed towards, and we do not knowingly collect... any information from
  children under the age of 18". It most naturally reads as governing
  Anthropic's own consumer relationship rather than API payloads, given the
  separate business-customer carve-out — **but that is the interpretive point
  a lawyer must close**, and it is the only clause that could bar this
  outright.

---

## Needs qualified legal review before launch

1. Does Anthropic's Privacy Policy § 7 bar sending a known child's data
   through the Commercial API? **And will Anthropic issue the § 312.8(c)
   assurance?** Until answered, under-13 AI cannot ship.
2. Is a self-declared **age band** (no date of birth) a compliant neutral
   screen, or does the FTC's month-and-year illustration set a floor?
3. Does CTDPA's "sensitive data" definition include data from a known child
   — such that **one Connecticut under-13 user** pulls a small operator into
   full CTDPA scope at zero volume?
4. **Do streaks and daily missions constitute a prohibited engagement-
   extending design feature** under CTDPA for 13–17s?
5. Does an invite-code-only private team of 8–25 count as "chat with people
   they do not know" under Google Play? (Google, not counsel, may be the
   right addressee.)
6. Do the Texas/Louisiana developer duties reach a web PWA distributed
   outside any app store?

**Could not verify:** a COPPA-specific Anthropic configuration or public DPA
text addressing children; verbatim Google text mandating an in-app
"report AI output" control; the Declared Age Range API contract; Michigan
SB 359's thresholds (certificate error on the legislature site).
