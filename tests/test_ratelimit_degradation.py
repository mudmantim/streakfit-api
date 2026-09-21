"""What happens when shared rate-limit storage goes away (Option B).

The owner chose this over the two simpler policies, and the reason is that
the two sensitive endpoints cost very different things when blocked:

  /api/teams/lookup/<code>  blocking it stops somebody JOINING A TEAM for a
                            few minutes. It is also the route with a measured
                            321 probes/second enumeration oracle, and in a
                            product where an invite is how an adult reaches a
                            child, that is a child-safety control.

  /api/login                blocking it locks out every user, including the
                            people whose streaks depend on showing up today.

So: invite lookup refuses, login tightens, everything else carries on.

WHAT THIS IS NOT, and the tests say so out loud: the login fallback is a
PER-PROCESS counter. With N workers an attacker gets N times the allowance
and it resets when a worker restarts. It is a floor, not shared limiting, and
test_the_fallback_is_not_shared_between_workers exists to stop anybody
reading it as equivalent.
"""
import pytest

from conftest import auth_headers, register_and_login


@pytest.fixture(autouse=True)
def _limiter_on():
    import app as appmod
    appmod.limiter.reset()
    appmod.limiter.enabled = True
    appmod._shared_rl_state.update(checked_at=None, healthy=True)
    appmod._degraded_login_window._hits.clear()
    yield
    appmod.limiter.enabled = False
    appmod.limiter.reset()
    appmod._shared_rl_state.update(checked_at=None, healthy=True)


def _degrade(monkeypatch, healthy=False):
    """Configure a shared backend and control whether it answers."""
    import app as appmod
    monkeypatch.setenv("RATELIMIT_STORAGE_URI", "redis://localhost:6379")
    monkeypatch.setattr(appmod, "_ratelimit_backend_check", lambda: healthy)
    appmod._shared_rl_state.update(checked_at=None, healthy=True)


# ── Backend failure ─────────────────────────────────────────────────────────

def test_ordinary_routes_keep_working_when_the_backend_is_down(client, monkeypatch):
    """A rate limiter must not be able to take the product down.

    Measured before this change: with the backend refused, every limited route
    returned 500. The daily mission is the product.
    """
    _degrade(monkeypatch)
    token = register_and_login(client, "degraded_mover", "TestPass123!")
    r = client.get("/api/daily", headers=auth_headers(token))
    assert r.status_code == 200, r.get_json()
    assert len(r.get_json()["exercises"]) == 5


def test_an_existing_session_still_works_when_the_backend_is_down(client, monkeypatch):
    token = register_and_login(client, "degraded_session", "TestPass123!")
    _degrade(monkeypatch)
    assert client.get("/api/me", headers=auth_headers(token)).status_code == 200


def test_invite_lookup_refuses_while_the_backend_is_down(client, monkeypatch):
    """Fail closed. Nobody is harmed by not joining a team for five minutes."""
    token = register_and_login(client, "degraded_joiner", "TestPass123!")
    other = register_and_login(client, "degraded_owner", "TestPass123!")
    team = client.post("/api/teams", json={"name": "T"},
                       headers=auth_headers(other)).get_json()["team"]

    _degrade(monkeypatch)
    r = client.get(f"/api/teams/lookup/{team['invite_code']}",
                   headers=auth_headers(token))
    assert r.status_code == 503, r.get_json()
    # And it says something a person can act on, not a status code.
    assert "try again" in r.get_json()["message"].lower()


def test_login_keeps_working_but_tightens_while_the_backend_is_down(client, monkeypatch):
    """The whole point of Option B over failing closed globally."""
    client.post("/api/register",
                json={"username": "degraded_login", "password": "TestPass123!"})
    _degrade(monkeypatch)

    ok = client.post("/api/login",
                     json={"username": "degraded_login", "password": "TestPass123!"})
    assert ok.status_code == 200, "a real user was locked out during an outage"

    codes = [client.post("/api/login",
                         json={"username": "degraded_login", "password": "wrong"}
                         ).status_code for _ in range(6)]
    assert 429 in codes, f"guessing was unlimited during the outage: {codes}"
    assert codes.index(429) <= 3, codes


def test_guessing_is_never_unrestricted_merely_because_redis_is_down(client, monkeypatch):
    """The owner's third criterion, stated as its own test."""
    _degrade(monkeypatch)
    codes = [client.post("/api/login",
                         json={"username": "ghost", "password": "wrong"}
                         ).status_code for _ in range(12)]
    assert codes.count(429) >= 8, codes


# ── Recovery ────────────────────────────────────────────────────────────────

def test_the_shared_limiter_resumes_when_the_backend_comes_back(client, monkeypatch):
    import app as appmod
    _degrade(monkeypatch)
    token = register_and_login(client, "recovery_user", "TestPass123!")
    other = register_and_login(client, "recovery_owner", "TestPass123!")
    team = client.post("/api/teams", json={"name": "R"},
                       headers=auth_headers(other)).get_json()["team"]
    assert client.get(f"/api/teams/lookup/{team['invite_code']}",
                      headers=auth_headers(token)).status_code == 503

    # Backend returns. The cached probe must expire rather than latch.
    monkeypatch.setattr(appmod, "_ratelimit_backend_check", lambda: True)
    appmod._shared_rl_state.update(checked_at=None, healthy=False)

    assert client.get(f"/api/teams/lookup/{team['invite_code']}",
                      headers=auth_headers(token)).status_code == 200


def test_health_is_not_probed_on_every_request(client, monkeypatch):
    """A probe per request puts a round trip — and, when it is down, a
    connection timeout — in front of every call."""
    import app as appmod
    calls = {"n": 0}

    def counted():
        calls["n"] += 1
        return False

    monkeypatch.setenv("RATELIMIT_STORAGE_URI", "redis://localhost:6379")
    monkeypatch.setattr(appmod, "_ratelimit_backend_check", counted)
    appmod._shared_rl_state.update(checked_at=None, healthy=True)

    for _ in range(8):
        client.get("/api/health")
    assert calls["n"] == 1, f"probed {calls['n']} times for 8 requests"


# ── The honesty tests ───────────────────────────────────────────────────────

def test_the_fallback_is_not_shared_between_workers(client, monkeypatch):
    """Says the quiet part out loud, so nobody reads the floor as the ceiling.

    Each worker holds its own counter, so N workers give an attacker N times
    the allowance. This asserts that weakness exists rather than pretending
    it does not — if the fallback ever does become shared, this test should
    fail and be rewritten.
    """
    import app as appmod
    worker_a = appmod._ProcessLocalWindow()
    worker_b = appmod._ProcessLocalWindow()
    key = "login:1.2.3.4"
    for _ in range(appmod._DEGRADED_LOGIN_LIMIT):
        assert worker_a.over(key, appmod._DEGRADED_LOGIN_LIMIT, 60) is False
    assert worker_a.over(key, appmod._DEGRADED_LOGIN_LIMIT, 60) is True
    # A second worker knows nothing about the first.
    assert worker_b.over(key, appmod._DEGRADED_LOGIN_LIMIT, 60) is False


def test_memory_storage_is_not_treated_as_an_outage(client, monkeypatch):
    """Production runs memory:// today.

    Treating "no shared backend configured" as degraded would fail invite
    lookup closed on every deployment that has not provisioned Redis — which
    is currently all of them.
    """
    monkeypatch.delenv("RATELIMIT_STORAGE_URI", raising=False)
    token = register_and_login(client, "memory_joiner", "TestPass123!")
    other = register_and_login(client, "memory_owner", "TestPass123!")
    team = client.post("/api/teams", json={"name": "M"},
                       headers=auth_headers(other)).get_json()["team"]
    assert client.get(f"/api/teams/lookup/{team['invite_code']}",
                      headers=auth_headers(token)).status_code == 200


def test_the_self_check_calls_the_degradation_what_it_is(client, monkeypatch):
    import app as appmod
    monkeypatch.setenv("STREAKFIT_ENV", "production")
    monkeypatch.setenv("RATELIMIT_STORAGE_URI", "redis://localhost:6379")

    def down():
        raise ConnectionError("refused")
    monkeypatch.setattr(appmod, "_ratelimit_backend_check", down)

    checks = client.get("/api/verification/self").get_json()["checks"]
    c = next(x for x in checks if x["id"] == "ratelimit.shared_storage")
    assert c["status"] == "FAIL"
    assert "DEGRADED" in c["observed"]
    assert "PER-PROCESS" in c["failureReason"]
    assert "floor, not shared" in c["failureReason"]
