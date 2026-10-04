"""What happens when shared rate-limit storage goes away (D7 round 2).

Owner policy since 2026-10-04, replacing "Option B" (which switched the whole
limiter off while the backend was unhealthy):

  * every route keeps its own limits, from this process's memory;
  * /api/teams/lookup/<code> refuses on the FIRST failure -- blocking it stops
    somebody JOINING A TEAM for a few minutes, and it is the route with a
    measured 321 probes/second enumeration oracle, in a product where an
    invite is how an adult reaches a child;
  * /api/login tightens on the FIRST failure -- blocking it would lock out
    every user, including the people whose streaks depend on today.

These run in-process against the real routes, with the live limiter's storage
swapped for a real `_RateLimitGate` whose backend is a stub that can be made
to fail. The black-box version, against a fake Redis on a socket and run
unchanged against older commits, is test_ratelimit_outage_policy.py.

WHAT THIS IS NOT, and the tests say so out loud: the degraded counts are PER
PROCESS. With N workers an attacker would get N times the allowance, and they
reset when a worker restarts. One worker is a production requirement for that
reason, and test_the_fallback_is_not_shared_between_workers exists to stop
anybody reading it as equivalent to shared limiting.
"""
import pytest
import redis
from limits.strategies import FixedWindowRateLimiter

import app as appmod
from conftest import auth_headers, register_and_login
from test_ratelimit_gate import Clock, StubRedis


@pytest.fixture
def gate(monkeypatch):
    """The live limiter, backed by a gate over a stub backend."""
    g = appmod._RateLimitGate(
        "streakfit+redis://127.0.0.1:1",
        **appmod._limiter_storage_options("redis://127.0.0.1:1"))
    g.redis = StubRedis()
    g.clock = Clock()
    monkeypatch.setattr(appmod.limiter, "_storage", g)
    monkeypatch.setattr(appmod.limiter, "_limiter", FixedWindowRateLimiter(g))
    monkeypatch.setattr(appmod.limiter, "enabled", True)
    appmod._degraded_login_window._hits.clear()
    yield g
    appmod._degraded_login_window._hits.clear()


def _fail(g):
    """The backend goes away. Nothing is degraded until a request finds out."""
    g.redis.fail = redis.ConnectionError("refused")


def _team(client):
    token = register_and_login(client, "deg_joiner", "TestPass123!")
    other = register_and_login(client, "deg_owner", "TestPass123!")
    team = client.post("/api/teams", json={"name": "T"},
                       headers=auth_headers(other)).get_json()["team"]
    return token, team["invite_code"]


# ── Backend failure ─────────────────────────────────────────────────────────

def test_ordinary_routes_keep_working_when_the_backend_is_down(client, gate):
    """A rate limiter must not be able to take the product down.

    Measured before the first fix: with the backend refused, every limited
    route returned 500. The daily mission is the product.
    """
    token = register_and_login(client, "degraded_mover", "TestPass123!")
    _fail(gate)
    r = client.get("/api/daily", headers=auth_headers(token))
    assert r.status_code == 200, r.get_json()
    assert len(r.get_json()["exercises"]) == 5


def test_an_existing_session_still_works_when_the_backend_is_down(client, gate):
    token = register_and_login(client, "degraded_session", "TestPass123!")
    _fail(gate)
    assert client.get("/api/me", headers=auth_headers(token)).status_code == 200


def test_invite_lookup_refuses_on_the_first_failure(client, gate):
    """Fail closed, and not one lookup later. Nobody is harmed by not joining
    a team for a few minutes."""
    token, code = _team(client)
    assert client.get(f"/api/teams/lookup/{code}",
                      headers=auth_headers(token)).status_code == 200
    _fail(gate)
    r = client.get(f"/api/teams/lookup/{code}", headers=auth_headers(token))
    assert r.status_code == 503, r.get_json()
    # And it says something a person can act on, not a status code.
    assert "try again" in r.get_json()["message"].lower()


def test_login_keeps_working_but_tightens_on_the_first_failure(client, gate):
    client.post("/api/register",
                json={"username": "degraded_login", "password": "TestPass123!"})
    _fail(gate)
    ok = client.post("/api/login",
                     json={"username": "degraded_login", "password": "TestPass123!"})
    assert ok.status_code == 200, "a real user was locked out during an outage"
    codes = [client.post("/api/login",
                         json={"username": "degraded_login", "password": "wrong"}
                         ).status_code for _ in range(6)]
    # The successful login above used one of the three degraded attempts.
    assert codes.count(401) == appmod._DEGRADED_LOGIN_LIMIT - 1, codes
    assert set(codes) == {401, 429}, codes


def test_guessing_is_never_unrestricted_merely_because_redis_is_down(client, gate):
    """The owner's security invariant, stated as its own test."""
    _fail(gate)
    codes = [client.post("/api/login",
                         json={"username": "ghost", "password": "wrong"}
                         ).status_code for _ in range(12)]
    assert codes.count(401) == appmod._DEGRADED_LOGIN_LIMIT, codes


def test_every_other_limit_still_holds_from_process_memory(client, gate):
    """Option B served these UNLIMITED (12 of 12 registrations against 5 per
    minute, measured). Now each keeps its own limit."""
    _fail(gate)
    codes = [client.post("/api/register",
                         json={"username": f"deg_reg_{i}", "password": "TestPass123!"}
                         ).status_code for i in range(8)]
    assert codes.count(201) == 5 and codes[5:] == [429] * 3, codes
    assert gate.degraded and appmod.limiter.enabled is True


def test_the_allowance_carries_over_into_the_outage(client, gate):
    """Counts made while the backend was healthy are still counted after it
    fails. Flask-Limiter's own fallback restarted them from zero."""
    codes = [client.post("/api/register",
                         json={"username": f"carry_{i}", "password": "TestPass123!"}
                         ).status_code for i in range(3)]
    _fail(gate)
    codes += [client.post("/api/register",
                          json={"username": f"carry_b{i}", "password": "TestPass123!"}
                          ).status_code for i in range(5)]
    assert codes.count(201) == 5, codes


def test_flask_limiters_zero_based_fallback_is_never_engaged(client, gate):
    _fail(gate)
    for i in range(3):
        client.post("/api/register",
                    json={"username": f"nofb_{i}", "password": "TestPass123!"})
    assert appmod.limiter._storage_dead is False


# ── Recovery ────────────────────────────────────────────────────────────────

def test_the_shared_limiter_resumes_only_after_qualifying(client, gate):
    token, code = _team(client)
    _fail(gate)
    assert client.get(f"/api/teams/lookup/{code}",
                      headers=auth_headers(token)).status_code == 503

    gate.redis.fail = None                     # the backend is back
    statuses = []
    for _ in range(gate.QUALIFY_SUCCESSES):
        gate.clock.t += gate.PROBE_INTERVAL_S
        statuses.append(client.get(f"/api/teams/lookup/{code}",
                                   headers=auth_headers(token)).status_code)
    # Still refusing until the last qualifying probe; that request is served.
    assert statuses == [503] * (gate.QUALIFY_SUCCESSES - 1) + [200], statuses
    assert not gate.degraded


def test_while_degraded_requests_reach_the_backend_only_as_the_paced_probe(client, gate):
    """Option B probed the backend from a hook in front of EVERY request.
    Now /api/health (itself throttled) and everything else are answered from
    memory; the only backend call is one probe per interval."""
    _fail(gate)
    client.post("/api/register", json={"username": "h1", "password": "TestPass123!"})
    calls = len(gate.redis.calls)
    gate.clock.t += 10 * gate.PROBE_INTERVAL_S
    for _ in range(8):
        assert client.get("/api/health").status_code == 200
        client.get("/api/brain-boost/today")
    new = gate.redis.calls[calls:]
    assert new == [("incr", appmod._RATELIMIT_PROBE_KEY, 1)], new


# ── The honesty tests ───────────────────────────────────────────────────────

def test_the_fallback_is_not_shared_between_workers():
    """Says the quiet part out loud, so nobody reads the floor as the ceiling.

    Each worker holds its own counter, so N workers give an attacker N times
    the allowance. This asserts that weakness exists rather than pretending
    it does not -- if the fallback ever does become shared, this test should
    fail and be rewritten.
    """
    worker_a = appmod._ProcessLocalWindow()
    worker_b = appmod._ProcessLocalWindow()
    key = "login:1.2.3.4"
    for _ in range(appmod._DEGRADED_LOGIN_LIMIT):
        assert worker_a.over(key, appmod._DEGRADED_LOGIN_LIMIT, 60) is False
    assert worker_a.over(key, appmod._DEGRADED_LOGIN_LIMIT, 60) is True
    assert worker_b.over(key, appmod._DEGRADED_LOGIN_LIMIT, 60) is False
    mirror_a, mirror_b = appmod._LocalWindows(), appmod._LocalWindows()
    mirror_a.incr("register:1.2.3.4", 60)
    assert mirror_b.get("register:1.2.3.4") == 0


def test_memory_storage_is_not_treated_as_an_outage(client, monkeypatch):
    """Treating "no shared backend configured" as degraded would fail invite
    lookup closed on every deployment that has not provisioned Redis."""
    monkeypatch.setattr(appmod.limiter, "enabled", True)
    appmod.limiter.reset()
    assert appmod._rate_limit_degraded() is False
    token, code = _team(client)
    assert client.get(f"/api/teams/lookup/{code}",
                      headers=auth_headers(token)).status_code == 200
    appmod.limiter.reset()


def test_the_self_check_calls_the_degradation_what_it_is(client, gate, monkeypatch):
    monkeypatch.setenv("STREAKFIT_ENV", "production")
    monkeypatch.setenv("RATELIMIT_STORAGE_URI", "redis://localhost:6379")
    _fail(gate)
    client.post("/api/register", json={"username": "sc1", "password": "TestPass123!"})
    calls = len(gate.redis.calls)

    checks = {c["id"]: c for c in client.get("/api/verification/self").get_json()["checks"]}
    c = checks["ratelimit.shared_storage"]
    assert c["status"] == "FAIL"
    assert "DEGRADED" in c["observed"] and "in-process authority" in c["observed"]
    assert "every limit is enforced from this process" in c["observed"]
    assert "not shared rate limiting" in c["failureReason"]
    assert len(gate.redis.calls) == calls          # reported, not probed
    assert checks["ratelimit.outage_policy"]["status"] == "PASS"


def test_the_self_check_passes_a_healthy_gate_by_writing_through_it(client, gate, monkeypatch):
    monkeypatch.setenv("RATELIMIT_STORAGE_URI", "redis://localhost:6379")
    checks = {c["id"]: c for c in client.get("/api/verification/self").get_json()["checks"]}
    assert checks["ratelimit.shared_storage"]["status"] == "PASS"
    assert "shared backend is the authority" in checks["ratelimit.shared_storage"]["observed"]
    assert ("incr", appmod._RATELIMIT_PROBE_KEY, 1) in gate.redis.calls
