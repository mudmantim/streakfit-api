# Request body limits (D64), and what they do not cover (D67)

## The boundary
| route | limit | enforced for |
|---|---|---|
| `POST /api/teams/<id>/photos` (multipart) | 2 MiB (2,097,152 bytes) | known and unknown length |
| every other route, including unmatched ones | 256 KiB (262,144 bytes) | known and unknown length |

An over-limit body is **refused with JSON 413 `{"error":"payload_too_large"}` before any view code, authentication, rate limiting, database work or provider call**. It is never truncated and processed.

## How
- **Known length (Content-Length):** `_enforce_route_body_limit` (before_request) refuses from the header; nothing is read.
- **Unknown length (chunked):** gunicorn delivers these with no Content-Length and sets `wsgi.input_terminated`.
  - Werkzeug wraps the input in its own `LimitedStream` of `request.max_content_length`. In Flask 3.0 that is the single global `MAX_CONTENT_LENGTH`, so `_BoundedRequest` (`app.request_class`) makes it the route's limit + 1.
  - The hook reads the body once (`get_data(cache=True)`, at most limit + 1 bytes) and refuses it if more than the limit arrived. The +1 is necessary: the bounded stream returns its prefix silently, so reading one byte past the limit is the only proof a body is too big.
  - The view then parses the cached bytes (JSON, urlencoded and multipart alike).
- **Unmatched routes (404/405/redirect):** an unknown-length body is not read at all.
- **Any 413 Werkzeug raises itself** (e.g. more than 1000 multipart parts) is JSON too (`errorhandler(413)`).
- **Ordering:** the hook is registered before the Limiter is constructed, so it runs before Flask-Limiter's own before_request hook. Do not move the Limiter above it.

**Before (885c303):** a chunked body of any size was read as its first 2 MiB and processed whenever that prefix was valid. Measured: users registered, team messages posted, profile writes, analytics rows, and an Ask Rickie model call. Exactly 2 MiB on the photo route got an HTML 413.

**Cost:**
- A chunked request to a known route is now read (≤ limit + 1 bytes) before authentication. A slow chunked sender therefore holds the worker on 401/403/429 paths that previously answered without reading.
- A chunked photo upload holds up to 2 MiB as cached bytes in addition to Werkzeug's spooled file (about +4.6 MB worker high-water mark, measured). Browsers send FormData with Content-Length, so this applies only to non-browser clients.

Verification Suite module `body_limits.py` checks this end to end over the network.

## Not covered: slow delivery (ledger D67, open, owner/infrastructure decision)
With one sync gunicorn worker, a client that sends headers slowly, sends nothing, or sends a body slowly holds the worker until gunicorn's 30 s timeout kills it. The whole site is unavailable meanwhile. One reconnecting attacker sustained about 96% unavailability locally.
- Gunicorn reads the headers before any application code runs and sets no socket timeout, so application code cannot bound it.
- The mitigation is topological:
  - a buffering proxy with read timeouts in front;
  - or an async/gthread worker with its own read timeouts;
  - or more workers, which conflicts with the one-worker security invariant (D7 and others).
- Whether Render's proxy already buffers requests in production is **unknown**. It cannot be tested locally and must not be tested against production without a decision.
