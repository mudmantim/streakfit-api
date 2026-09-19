# AI usage allowances and credit packs — proposed architecture

**Status: PROPOSED. Nothing here is implemented, no payments code exists, and
no prices are settled.** This document exists so the decision can be made on
measured numbers instead of guesses. The suggested 20 / 300 allowances and the
$1.99-per-100 pack were explicitly not approved, and the audit below says why at
least one of them cannot work as stated.

Scope note: this is planning. It has not diverted work from the core product —
the audit is arithmetic over usage already recorded, and `scripts/rickie_cost_audit.py`
makes no API calls.

---

## 1. What a Rickie reply actually costs

From `scripts/rickie_cost_audit.py`, which reads the real constants out of
`app.py` and calibrates against usage the provider reported during evaluations
this project already ran (82 real replies across three runs).

### Where the tokens go

| Share | Bytes | What |
|---|---|---|
| **63%** | 13,642 | The system prompt — personality, feature list, safety rules |
| 28% | 6,000 | Conversation history (10 turns × 600 chars) |
| 3% | 681 | Weather tool schema |
| 2% | 500 | The user's own message (capped) |
| 2% | 400 | User context block (streak, level, name) |
| 1% | 300 | Coach Notes block |

### Measured per-reply cost

| Run | Input/reply | Output/reply | Cost/reply |
|---|---|---|---|
| 56-prompt eval (prompt 8.4k) | 3,773 | 99 | **$0.0085** |
| 7-case retest (prompt 11.3k) | 4,507 | 89 | **$0.0099** |
| 19-case retest (prompt 12.7k) | 4,978 | 103 | **$0.0110** |

- typical reply: **$0.0099**
- worst case (full 10-turn history, 768-token output cap): **$0.0199**
- **planning figure: $0.0149**

### Two findings that matter before any price is set

**(a) The prompt is the cost, and it grew 62% this month.** Per-reply cost rose
from $0.0085 to $0.0110 — +29% — purely because the system prompt went from
8.4k to 12.7k characters as safety and feature rules were added. Every rule
added to Rickie is billed on every reply every user ever sends. Prompt size is
now a product cost decision, not just a quality one.

**(b) Prompt caching is the single big lever, and it is conditional.** The
stable prefix (system prompt + tool schema) is ~4,078 tokens, byte-identical on
every call. Cache reads price at ~10% of input:

| | Cost/reply | vs today |
|---|---|---|
| Cache **hit** | $0.0026 | **74% cheaper** |
| Cache **miss** | $0.0120 | **21% dearer** |
| Breakeven | hits must exceed **22%** of requests | |

A cache entry lives ~5 minutes (1 hour on the paid tier) and a write costs
~1.25× input. **At StreakFit's current traffic most requests would arrive cold
and cost more than they do today.** Caching pays only once requests arrive
closer together than the TTL. So it is not a free win to assume in pricing — it
is a thing to implement, measure with `usage.cache_read_input_tokens`, and only
then price against.

---

## 2. Usage profiles, including sponsorship

At the $0.0149 planning figure, uncached:

| Profile | Replies/mo | Cost/mo |
|---|---|---|
| Light | 5 | $0.07 |
| Typical | 30 | $0.45 |
| Engaged | 100 | $1.49 |
| Heavy | 300 | $4.48 |
| Extreme | 1,000 | $14.94 |
| Rate-limit ceiling (10/day) | 310 | $4.63 |

Note the existing per-user rate limit of **10/day** already caps any single
account at ~310 replies/month — roughly $4.63. That is the true worst case
today, and it is a useful backstop: no allowance can be exceeded catastrophically
by one account.

### Against revenue, at a 20% COGS budget

| Plan | Revenue | 20% budget | Replies it buys (uncached) | (cached hit) |
|---|---|---|---|---|
| Plus | $4.99 | $1.00 | **66** | ~380 |
| Sponsored | $0.99 | $0.20 | **13** | ~76 |

### The sponsorship problem, stated plainly

Sponsored users get *identical* Plus features and allowances — that is a
confirmed product decision, and it is the right one. But a sponsor at the cap
pays **$4.99 + 5 × $0.99 = $9.94/month for six accounts**, an average of
**$1.66 per account**.

If all six used a 300-reply allowance fully, that is 1,800 replies =
**$26.89/month against $9.94 revenue** — a 2.7× loss. Even at the 10/day rate
limit the ceiling is 1,860 replies ≈ $27.78.

**This is the number that decides the allowance.** Three honest ways out:

1. **Set the allowance where the worst case is survivable.** At 150 replies,
   six accounts fully consuming = $13.44 vs $9.94. Still a loss at the extreme,
   but only for a household where all six are heavy users every month.
2. **Implement caching first, then set a higher allowance against measured hit
   rates.** At a 70% hit rate the effective cost is ~$0.0063/reply, and 300
   replies × 6 = $11.34 — close to break-even at the extreme, comfortable at
   realistic usage.
3. **Price sponsorship higher.** Not recommended; $0.99 is the point of it.

**Expected cost is far below worst case.** AI usage is long-tailed: most users
never approach an allowance. If a household of six averages the *typical* 30
replies, that is 180 replies = **$2.69/month against $9.94** — comfortable. The
allowance is a ceiling that protects against the tail, not a forecast.

---

## 3. Recommended allowances — and what they depend on

**Recommendation: do not set 300 for Plus until caching is measured.** At
today's uncached cost 300 replies is $4.48, which is **90% of a $4.99
subscription** before any other cost. That is not a margin, it is a rounding
error away from losing money on every engaged subscriber.

Proposed launch numbers, deliberately conservative and explicitly revisable:

| | Monthly allowance | Worst-case cost | As % of revenue |
|---|---|---|---|
| **Free** | **15** | $0.22 | n/a — acquisition cost |
| **Plus** | **150** | $2.24 | 45% of $4.99 |
| **Sponsored** | **150** (identical) | $2.24 | 226% of $0.99 alone, 22% of the $9.94 household |

Sponsored users must get the same allowance — anything else makes them
second-class, which defeats the feature. The economics work at the *household*
level, not per sponsored seat, and that is the right frame: a sponsor is buying
six seats for $9.94.

**Revisit trigger:** once prompt caching is live and the measured cache-hit rate
is known, recompute and raise. 300 is reachable at a sustained hit rate above
~60%.

Free at 15 is enough to meet Rickie properly (a real conversation is 3-6 turns)
without making the free tier the product. It costs $0.22/month per active free
user, which is a defensible acquisition cost.

---

## 4. Credit packs

**Never auto-charge.** When the allowance runs out the app keeps working and
offers a pack; it does not bill anyone automatically. That is a stated
requirement and it is also the right default.

Pricing must clear cost with room for payment fees. At $0.0149/reply, 100
replies cost **$1.49** — so a $1.99 pack for 100 leaves $0.50, which after
payment processing (~$0.36 on a $1.99 transaction at 2.9% + $0.30) is
**$0.14**. That is too thin, and it is worse on a cache miss.

| Pack | Cost to serve | Price | After fees | Margin |
|---|---|---|---|---|
| 100 replies | $1.49 | $1.99 | $1.63 | $0.14 — **too thin** |
| 100 replies | $1.49 | $2.99 | $2.60 | $1.11 — workable |
| 250 replies | $3.73 | $5.99 | $5.52 | $1.79 — better |
| 500 replies | $7.47 | $9.99 | $9.40 | $1.93 |

Small packs are punished by fixed payment fees. **Recommendation: no pack below
$2.99, and make the larger packs the obvious value** — this also reduces
transaction count, which reduces fees.

### Credit rules

- **Unused allowance does not roll over.** It resets on the renewal date. This
  is the norm, it is simple to explain, and rollover creates an unbounded
  liability.
- **Purchased credits DO roll over and never expire.** They were paid for
  separately. Expiring them is the thing users rightly resent.
- **Consumption order: monthly allowance first, then purchased credits.** Always.
  Otherwise a user burns money they paid for while a free allowance sits unused.
- **Cancellation:** purchased credits survive a downgrade to Free and remain
  spendable. The monthly allowance drops to the Free level at the end of the
  paid period.
- **Refunds:** unused *purchased* credits refundable pro-rata within 30 days;
  consumed credits are not. The allowance is not refundable — it is part of the
  subscription, not a separate purchase.
- **Sponsorship ends:** the sponsored user keeps their account, history, teams
  and any credits they bought themselves. Their allowance drops to Free. They
  can subscribe independently with no loss — a confirmed product requirement,
  and the credit system must not create an exception to it.
- **Sponsored users may buy their own packs.** Their credits are theirs and do
  not return to the sponsor.
- **Gifting** (sponsor buys credits for a sponsored user) requires the
  recipient's explicit acceptance, is a one-off purchase, and never creates a
  recurring charge.

---

## 5. Accounting correctness

This is where a usage system actually fails. Requirements:

**Never charge for a failure.** Reserve on request, commit only on a successful
reply. A 503, a timeout, a refusal, a rate limit, or any exception refunds the
reservation. Today's `coach()` already returns 503 on any exception — the
reservation must be released on that path, which means the release belongs in a
`finally`, not after the return.

**Idempotency.** Every request carries a client-generated idempotency key.
A retry with the same key returns the original reply and charges once. Without
this, a flaky mobile connection charges a user for one answer three times.

**Concurrency.** Two simultaneous requests must not both see "1 credit left" and
both proceed. The decrement must be atomic — a conditional UPDATE
(`... SET used = used + 1 WHERE used < allowance`) with the row count checked,
not a read-then-write. SQLAlchemy makes it easy to write the racy version.

**One ledger, append-only.** A `credit_event` table recording every grant,
consumption, refund and expiry with a reason and a timestamp. Balances are
derived from it, not stored as a mutable counter. When a user disputes a charge,
the ledger is the answer.

**Reset boundaries.** Store an explicit `allowance_period_start`; do not infer
"this month" from `created_at` arithmetic at read time. Timezones and month
lengths make that wrong at the edges, and the edges are where support tickets
come from.

---

## 6. Privacy — sponsors must not see conversations

**A sponsor pays for a seat. They do not get a window into it.** This is the
single hardest line in the feature and it must be structural, not a policy note:

- A sponsor may see **that** a sponsored seat exists, its cost, and whether it
  is active. Nothing else.
- A sponsor may **not** see the sponsored user's messages, Rickie's replies,
  Coach Notes, or per-day usage detail. Not aggregated, not summarized, not
  "just the count per day" — a daily count of a teenager's conversations with a
  companion is itself revealing.
- If a sponsor needs a spend signal, the correct one is **household spend**, not
  per-seat usage.
- A sponsored user's Forget Conversations must work exactly as it does for
  anybody else, with no sponsor visibility or veto.
- Teammates see what Teams already shows — name, today's status, streak. The
  credit system must add nothing to that surface.

This deserves its own tests before any sponsor UI exists, in the same spirit as
`tests/test_coach_privacy.py`.

---

## 7. What must NOT cost credits

Confirmed and worth writing into the code as a single gate: credits are consumed
**only** by a successful Ask Rickie model reply. Not by:

- completing exercises or missions, or anything in the Daily Mission;
- Brain Boost questions (the library is static content on disk — there is no
  model call, and there must never be one for serving a question);
- Today's Insight, riddles, experiments, Rickie's written asides — all static;
- teams, campfire, photos, challenges, memory book;
- streaks, XP, acorns, filters.

The clean way to guarantee this is that the only call site of the metering
function is inside `coach()`. If a second call site ever appears, that is the
review moment.

---

## 8. AI photo transformations — open question

Not built, and it changes the shape of this model if it is. An image generation
or transformation call is **one to two orders of magnitude more expensive than a
text reply**, so it cannot share a "responses" allowance without either
bankrupting it or making the allowance meaningless.

If it is built, the recommendation is a **separate, explicitly-priced credit
type** — "1 photo transformation = N Rickie replies", with N shown before the
user commits, and a confirmation step. Never silently draw a photo transform
from a conversation allowance.

**This needs a cost audit of its own before any number is chosen**, and that
audit needs a decision on which model/provider would do it.

---

## 9. Decisions needed

1. **Allowances.** 15 Free / 150 Plus as proposed, or different? The audit says
   300 Plus is not sustainable uncached.
2. **Caching first?** Implementing prompt caching and measuring the hit rate
   would let the allowance be roughly double. It is a contained change to
   `coach()`. Should it precede the pricing decision?
3. **Pack pricing.** $1.99/100 is too thin after payment fees. $2.99/100 or a
   larger-pack-only structure?
4. **Household worst case.** Is a $13.44-vs-$9.94 extreme acceptable as tail
   risk, given expected usage is ~$2.69?
5. **Photo transformations** — in scope at all? If so it needs its own audit.
6. **Prompt size budget.** Should there be one? Rickie's prompt grew 62% this
   month and every character is billed on every reply forever.
