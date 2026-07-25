# ADR-0005: In-process weather cache
- **Status:** Accepted
- **Date:** 2026-07-25
- **Deciders:** StreakFit engineering

## Context / Problem
Rickie's one tool is `get_weather`, backed by Open-Meteo (free, no API key). Each
uncached lookup makes two outbound calls: `_geocode_city` (city → coordinates)
then `_forecast(lat, lon)`. Open-Meteo's free tier is rate-limited **per IP**
(≈600/min, 10k/day). StreakFit runs on Render behind a **shared egress IP**, so
even though our own weather usage is tiny, other tenants sharing that IP can
exhaust the per-IP quota and our lookups intermittently `429`. We needed to cut
our own contribution to that shared budget without adding infrastructure.

## Decision
Add two in-process caches with a lock:

- `_GEOCODE_CACHE` — normalized city name → place dict, TTL `_GEOCODE_TTL` = **30
  days** (a city's coordinates don't move).
- `_FORECAST_CACHE` — `(lat, lon)` rounded to 4 decimals → current weather, TTL
  `_FORECAST_TTL` = **10 minutes** (forecasts change slowly).

Each cache is hard-capped at `_CACHE_MAX_ENTRIES` = **512** to bound memory, and
all reads/writes are guarded by a single `threading.Lock` (`_CACHE_LOCK`) so the
threaded gunicorn worker is safe. Eviction (`_cache_evict_one`) makes room by
dropping the **oldest expired** entry if one exists (dicts preserve insertion
order, so the first expired entry found is the oldest), otherwise the oldest
inserted entry (FIFO). Geocode *misses* are deliberately not cached, so a typo
today doesn't poison the cache.

## Alternatives considered
- **Redis / shared cache across workers.** Why it lost: adds a paid service and a
  new dependency and a network hop for a best-effort optimization. The problem is
  "reduce our own call volume," which a per-process cache already achieves; a
  shared cache is a larger commitment than the problem warrants right now.
- **Keyed provider** (an Open-Meteo API key with a per-account quota). Why it
  lost *for now*: it costs money and its own signup/ops, and it's the **documented
  escalation** if 429s persist after caching — the real fix for a shared-IP quota
  problem, but not the cheapest first step. Caching is tried first.
- **No cache** (call Open-Meteo every time). Why it lost: maximizes our share of
  the shared-IP budget and the 429 exposure — the opposite of what we need.
- **Retry-on-429** (catch the 429 and retry). Why it lost: retries against a
  per-IP limit add *more* calls to the exhausted budget and add latency; they
  treat the symptom while making the cause worse. The graceful-failure path
  already returns a warm in-character error when a lookup fails, so a hard retry
  buys nothing.

## Why the current solution won
The caches directly attack the controllable variable — *our* outbound call count
— with zero new infrastructure, zero dependencies, and no cost. The two TTLs
match the data's real volatility (coordinates are effectively static; forecasts
drift over minutes), so the hit rate is high for what StreakFit actually does. It
is explicitly a **first step, not a cure**: it cannot stop other tenants on the
shared IP from hitting the quota, which is why the keyed-provider escalation is
documented rather than forgotten.

## Consequences & future tradeoffs
- **Makes easy:** cutting our provider volume immediately with no ops; bounded
  memory (512-entry cap); thread-safe under the current worker model.
- **Makes hard:** the cache is **per gunicorn worker and lost on restart** — no
  cross-worker or cross-deploy sharing, so effectiveness scales down with more
  workers and resets on every deploy. It is best-effort by design and cannot
  guarantee we stay under the shared-IP limit.
- **When we'd revisit:** if 429s persist despite caching, escalate to a keyed
  provider (per-account quota) — the documented next move. A move to many workers
  or a need for durable cross-process caching would argue for a shared backend.

## Code references
- `app.py:3659-3670` — cache dicts, `_GEOCODE_TTL` (30 days), `_FORECAST_TTL`
  (10 min), `_CACHE_MAX_ENTRIES` (512), `_CACHE_LOCK` (`threading.Lock`); the
  comment states the caches are best-effort and name the keyed-provider escalation.
- `app.py:3673-3707` — `_cache_get` (TTL check + lazy expiry under lock),
  `_cache_evict_one` (oldest-expired-else-FIFO), `_cache_put` (cap + evict).
- `app.py:3717-3736` — `_geocode_city` (cached 30 days; misses not cached).
- `app.py:3739-3760` — `_forecast` (cached 10 min by rounded coords).
- `app.py:3763-3802` — `_weather_tool_result`: never raises; every failure
  (unknown city, missing data, network/429) returns a warm `is_error` string and
  logs a warning so provider issues stay visible.
- See [`../architecture/coach-subsystem.md`](../architecture/coach-subsystem.md)
  and [`../architecture/deployment.md`](../architecture/deployment.md) (shared
  egress IP).
