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
| Connect | ≤ 3 s, and ≤ the time left; every resolved address shares the deadline | `_COACH_CONNECT_TIMEOUT_S`, `_DeadlineBackend.connect_tcp` |
| SDK retries | 0 (no retry-after sleeps) | `max_retries=0` |
| Response size | ≤ 4 MiB per HTTP exchange | `_COACH_MAX_RESPONSE_BYTES` |
| After a slow failure | 503 at once, with no provider call, for 60 s | `_COACH_BREAKER_S` |
| DB transaction during the wait | none (committed before the call) | `coach()` |
| Reply ceiling (`STREAKFIT_COACH_MAX_TOKENS`) | ≤ 1024 (a non-streamed 2048-token reply cannot finish in 20 s) | `COACH_MAX_TOKENS_CEILING` |
| `message` | 1–500 code points after strip; NUL and lone surrogates → 400 `invalid_message`; nothing else altered | `_coach_parse_request` |
| Body / field types | a non-object body, or wrong-typed `message`/`context`/insight fields → 400 `invalid_request` | `_coach_parse_request` |
| Insight | client text > 1000 characters → 400 `context_too_long`. Otherwise it only *selects* the person's own insight (today's or yesterday's, else today's); the server's text and category reach the prompt, never the client's | `_coach_insight_for` |
| Assembled request | ≤ 32,000 characters (worst legal case about 26.5 k); the oldest history is dropped first | `_COACH_CONTEXT_MAX_CHARS` |

The self-check `coach.provider_bounds` reports the live values and the breaker state. Verification Suite module `coach.py` checks the refusals; it never makes a model call.

## How the deadline works

httpx's timeouts are per socket wait, and httpcore reads the read timeout once per response phase. A peer sending one byte a second never trips them: 24.5 s under a "3 s timeout" was measured.

The deadline is therefore enforced in an **httpcore network backend** (`_DeadlineBackend`, `_DeadlineStream`), httpcore's public extension point:
- every connect, TLS handshake, read and **each individual send** is clamped to the time left;
- nothing proceeds once the time is gone.

It is injected by replacing `httpx.HTTPTransport._pool`, the one private name touched. `httpx==0.28.1` and `httpcore==1.0.9` are pinned. `tests/test_coach_hardening.py` drives a real hostile socket server, so an upgrade that bypasses the backend turns those tests red.

Passing `transport=` makes httpx ignore proxy environment variables. None are configured in production. If a proxy is ever needed, it must be added to the transport deliberately.

## The breaker

**It trips** only when a single model call that had at least half the budget of its own still timed out or failed to connect.

**It does not trip** on:
- fast errors (4xx, 5xx, 429, 529): they cost the worker nothing, and one person's bad request must not lock everybody out;
- running out of the shared budget late in a long weather turn.

Its state is per process. That is global with one worker, the same reasoning as D7's mirror. A worker restart closes it.

While it is open, Rickie answers 503 and the client shows "Rickie stepped away".

## Semantics of failure

| case | provider calls | rate-limit hit | turn saved |
|---|---|---|---|
| 400 (shape, size, unstorable text) | 0 | yes | no |
| 503 breaker open / context over budget | 0 | yes | no |
| 503 timeout / provider error | 1 per model call (≤ 3 per question) | yes | no |
| 200 | 1–3 | yes | yes |

The rate limits (3/min, 10/day) are decorators and count on entry, as before. A 413 from the body-size hook does not count. The client never retries automatically.

## Residual risks

- **DNS resolution** (`getaddrinfo`) is not bounded, the same as D7's residual. A hung resolver holds the worker until gunicorn's kill.
- **Whole-site stall.** A provider that is slow but within budget still occupies the only worker for that long (≤ 20 s per question, about one question per 60 s while the breaker is open). That is inherent to one sync worker; the breaker limits it but does not remove it.
- **The 20 s budget is reasoned, not measured.** No production latency data existed. `event=coach_call_ok ... ms=` is now logged on every success. Check its p99 before trusting the budget, and lower the reply ceiling or raise the budget (≤ 25 s) if long replies time out (`event=coach_call_failed error=APITimeoutError`).
- **Bodies that bypass the size check.** Chunked bodies still bypass the 256 KB hook on every route (ledger D64). That is outside this change, and no longer reaches the prompt.
- **The display name** (≤ 40 characters, the person's own) still appears in the system prompt.

## Rollback

Code only, with no schema step. Rolling back restores D48, the 30 s whole-site freezes, and D53, client text in the system prompt.
