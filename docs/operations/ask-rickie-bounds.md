# Ask Rickie: provider deadline and input boundary (D48, D53)

## Why

Production runs one instance with one sync gunicorn worker (a security invariant since D7). `/api/coach` used to call the model with the SDK's defaults:
- 600 s per socket read;
- 2 retries that sleep on `retry-after` inside the request;
- no total limit at all.

Each of these held the only worker until gunicorn killed it at 30 s, and the whole site, including `/health`, queued behind it (measured):
- a provider that hung;
- a provider that trickled bytes;
- a provider that answered slowly;
- a provider that returned 429 with `retry-after`.

N stalls cost N×30 s.

Separately, the insight a person taps "tell me more" on was pasted from the request into Rickie's SYSTEM prompt. It was uncapped (256 KB, or 2 MB as a chunked body) and unescaped, so quotes and newlines could forge system-prompt lines.

## What holds now

| property | value | where |
|---|---|---|
| Total time per question (every model call, every weather lookup) | ≤ 20 s from the start of `coach()` | `_COACH_PROVIDER_BUDGET_S` |
| Connect | ≤ 3 s per address, and ≤ the time left; all resolved addresses share the one deadline | `_COACH_CONNECT_TIMEOUT_S`, `_DeadlineBackend.connect_tcp` |
| SDK retries | 0 (no retry-after sleeps) | `max_retries=0` |
| Response size | ≤ 4 MiB per response body; `Accept-Encoding: identity`, and any compressed response is refused (httpx would otherwise inflate it) | `_COACH_MAX_RESPONSE_BYTES`, `_BoundedTransport` |
| After two slow failures within 60 s | 503 at once, with no provider call, for 60 s | `_COACH_BREAKER_S`, `_COACH_BREAKER_STRIKES` |
| DB transaction during the wait | none (committed before the call) | `coach()` |
| Reply ceiling (`STREAKFIT_COACH_MAX_TOKENS`) | ≤ 1024 (a non-streamed 2048-token reply cannot finish in 20 s) | `COACH_MAX_TOKENS_CEILING` |
| `message` | 1–500 code points after strip; NUL and lone surrogates → 400 `invalid_message`; nothing else altered | `_coach_parse_request` |
| Body / field types | a non-object body, or wrong-typed `message`/`context`/insight fields → 400 `invalid_request` | `_coach_parse_request` |
| Insight | client text > 1000 characters → 400 `context_too_long`. Otherwise it only *selects* the person's own insight (today's or yesterday's; else today's), and only kinds the app offers "tell me more" on (fact, movement, experiment). A riddle or aside is never used. The server's text and category reach the prompt, never the client's | `_coach_insight_for` |
| Assembled request | ≤ 32,000 characters on every model call (worst legal case 25.6 k measured). The oldest history is dropped first on the first call; a tool round that would exceed it stops instead | `_COACH_CONTEXT_MAX_CHARS` |
| Name in the prompt | display name or short username, whitespace collapsed to single spaces (a newline in a username forged a system line) | `_safe_display_name` |

The self-check `coach.provider_bounds` reports the live values and the breaker state. Verification Suite module `coach.py` checks the refusals; it never makes a model call.

## How the deadline works

httpx's timeouts are per socket wait, and httpcore reads the read timeout once per response phase. A peer sending one byte a second never trips them: 24.5 s under a "3 s timeout" was measured.

The deadline is therefore enforced in an **httpcore network backend** (`_DeadlineBackend`, `_DeadlineStream`), httpcore's public extension point:
- every connect, TLS handshake, read and **each individual send** is clamped to the time left;
- nothing proceeds once the time is gone.

It is injected by replacing `httpx.HTTPTransport._pool`, the one private name touched. `httpx==0.28.1` and `httpcore==1.0.9` are pinned. `tests/test_coach_hardening.py` drives a real hostile socket server, so an upgrade that bypasses the backend turns those tests red.

Passing `transport=` makes httpx ignore proxy environment variables. None are configured in production. If a proxy is ever needed, it must be added to the transport deliberately.

## The breaker

**A strike** is a model call that itself ran for at least half the budget (10 s) and then failed in any way: a timeout, a lost connection, or a 5xx/529 that arrived late. How long the call held the worker decides, not what kind of error it was.

**Two strikes within 60 s trip it.** One is not enough: a non-streamed reply sends nothing until it is complete, so a single timeout cannot tell a stalled provider from one unusually long answer, and one person's long answer must not lock everybody out. Two slow failures can come from the same person. That needs a provider slow enough that a ≤ 1024-token reply cannot finish in 20 s, which is itself a brownout signal.

**It never counts:**
- fast errors of any kind (4xx, 5xx, 429, 529, a reset, an oversized or compressed body): they cost the worker nothing;
- running out of the shared budget late in a long weather turn.

**Its state** is per process. That is global with one worker, the same reasoning as D7's mirror. A worker restart closes it.

**While it is open**, Rickie answers 503 and the client shows "Rickie stepped away".

**Cost of the trade-off.** A real stall costs two 20 s whole-site freezes before the breaker opens, not one.

## Semantics of failure

| case | provider calls | rate-limit hit | turn saved |
|---|---|---|---|
| 400 (shape, size, unstorable text) | 0 | yes | no |
| 503 breaker open / context over budget | 0 | yes | no |
| 503 timeout / provider error | 1 per model call (≤ 3 per question) | yes | no |
| 503 tool round cut short (deadline or ceiling) | 1–2 | yes | no (the preamble is not saved as an answer) |
| 200 | 1–3 | yes | yes |

The rate limits (3/min, 10/day) are decorators and count on entry, as before. A 413 from the body-size hook does not count. The client never retries automatically.

**A breaker-open 503 still uses the person's daily allowance.** One person retrying through an outage spends their questions on instant 503s. On the 4th try in a minute they see the existing "You've reached today's question limit" copy, which is wrong for the per-minute limit (ledger D65).

## Residual risks

- **DNS resolution** (`getaddrinfo`) is not bounded, the same as D7's residual. A hung resolver holds the worker until gunicorn's kill.
- **Whole-site stall.** A provider that is slow but within budget still occupies the only worker for that long (≤ 20 s per question, about one question per 60 s while the breaker is open). That is inherent to one sync worker; the breaker limits it but does not remove it.
- **The 20 s budget is reasoned, not measured.** No production latency data existed. `event=coach_call_ok ... ms=` is now logged on every success. Check its p99 before trusting the budget, and lower the reply ceiling or raise the budget (≤ 25 s) if long replies time out (`event=coach_call_failed error=APITimeoutError`).
- **Bodies that bypass the size check.** Chunked bodies still bypass the 256 KB hook on every route (ledger D64). That is outside this change, and no longer reaches the prompt.
- **The person's own name** (display name ≤ 40, or a short username, both collapsed to one line) still appears in the system prompt.
- **The provider's own reply text is not validated.** A reply containing NUL or a lone surrogate still returns 200 but is not saved. This is provider-controlled and pre-existing.
- **Sustained brownout.** With the breaker at 60 s, each window can cost about 2×20 s of whole-site freeze. Derived (not load-tested): roughly a third to two-thirds of the time unavailable during a long provider outage with steady Rickie traffic. Better than N×30 s with worker kills, but not free. A shorter budget or a longer breaker window trades Rickie's success rate for site availability. Streaming responses would let a stall be told from a long answer by the first byte; that is a larger change and not done here.

## Rollback

Code only, with no schema step. Rolling back restores D48, the 30 s whole-site freezes, and D53, client text in the system prompt.
