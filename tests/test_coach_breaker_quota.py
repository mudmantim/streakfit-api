"""Ask Rickie: what spends a person's daily questions, and the breaker's
half-open state.

The daily allowance (10/day) is the AI-question quota: it is spent by a
question that reached the model, whatever came back. The per-minute limit
(3/min) is the cheap request throttle: it is spent by every request that
reaches the route, including ones refused before the model. A request the
server refuses only because the provider circuit is already open costs the
person nothing from their day -- it never reached the provider -- but still
counts against the minute, so refusals cannot be hammered.
"""
import time
import types

import pytest

import app as appmod
from conftest import register_and_login, auth_headers
from test_coach_hardening import HostileProvider, BUDGET_S


@pytest.fixture()
def limits_on():
    """Rate limiting is off for the rest of the suite; on (and empty) here."""
    appmod.limiter.enabled = True
    appmod.limiter.reset()
    yield
    appmod.limiter.reset()
    appmod.limiter.enabled = False


@pytest.fixture(autouse=True)
def _closed_breaker():
    appmod._coach_breaker_reset()
    yield
    appmod._coach_breaker_reset()


def _used(suffix):
    """Hits recorded against limits whose key ends in `suffix`."""
    store = appmod.limiter.storage
    store = getattr(store, "local", store)          # the D7 gate's mirror, if present
    return sum(v for k, v in store.storage.items() if k.endswith(suffix))


def day_used():
    return _used("/10/1/day")


def minute_used():
    return _used("/3/1/minute")


def _fake_ok(monkeypatch):
    class _Messages:
        def create(self, **kwargs):
            return types.SimpleNamespace(
                content=[types.SimpleNamespace(type="text", text="ok")], stop_reason="end_turn")

    class _Client:
        def __init__(self, *a, **kw):
            self.messages = _Messages()

    monkeypatch.setattr(appmod, "_anthropic_api_key", "test-key-not-real")
    monkeypatch.setattr(appmod._anthropic_lib, "Anthropic", _Client)


def _hostile(monkeypatch, mode, **opts):
    srv = HostileProvider(mode, **opts)
    monkeypatch.setenv("ANTHROPIC_BASE_URL", srv.url)
    monkeypatch.setattr(appmod, "_anthropic_api_key", "sk-ant-lab-dummy-not-real")
    monkeypatch.setattr(appmod, "_COACH_PROVIDER_BUDGET_S", BUDGET_S)
    return srv


def _ask(client, token, body=None):
    return client.post("/api/coach", headers=auth_headers(token),
                       json=body if body is not None else {"message": "hi"})


def _open_breaker():
    appmod._COACH_BREAKER["tripped_at"] = time.monotonic()


# ── what spends the daily allowance ──────────────────────────────────────────

def test_a_breaker_refusal_does_not_spend_a_daily_question(client, monkeypatch, limits_on):
    _fake_ok(monkeypatch)
    token = register_and_login(client, "q_breaker")
    _open_breaker()
    assert _ask(client, token).status_code == 503
    assert day_used() == 0
    assert minute_used() == 1           # still throttled per minute


def test_a_malformed_request_does_not_spend_a_daily_question(client, monkeypatch, limits_on):
    _fake_ok(monkeypatch)
    token = register_and_login(client, "q_bad")
    assert _ask(client, token, ["not", "an", "object"]).status_code == 400
    assert _ask(client, token, {"message": "x", "context": {
        "type": "insight", "insight_text": "a" * 5000}}).status_code == 400
    assert day_used() == 0
    assert minute_used() == 2


def test_a_successful_question_spends_one(client, monkeypatch, limits_on):
    _fake_ok(monkeypatch)
    token = register_and_login(client, "q_ok")
    assert _ask(client, token).status_code == 200
    assert day_used() == 1 and minute_used() == 1


@pytest.mark.parametrize("mode,opts", [("status", {"code": 500}), ("status", {"code": 429}),
                                       ("hang", {})])
def test_a_question_that_reached_the_provider_spends_one_even_if_it_failed(
        client, monkeypatch, limits_on, mode, opts):
    """The provider was called: it may bill a timed-out generation, and its
    rate limits are shared by everyone on this key."""
    srv = _hostile(monkeypatch, mode, **opts)
    try:
        token = register_and_login(client, f"q_fail_{mode}{opts.get('code', '')}")
        assert _ask(client, token).status_code == 503
        assert srv.requests == 1
        assert day_used() == 1
    finally:
        srv.close()


def test_the_daily_limit_still_stops_the_eleventh_question(client, monkeypatch, limits_on):
    _fake_ok(monkeypatch)
    token = register_and_login(client, "q_cap")
    # Spend nine through the real route, a few at a time to stay under 3/min.
    store = appmod.limiter.storage
    user = appmod.User.query.filter_by(username="q_cap").first()
    for i in range(9):
        assert _ask(client, token).status_code == 200, i
        for k in [k for k in getattr(store, "local", store).storage if k.endswith("/3/1/minute")]:
            getattr(store, "local", store).storage.pop(k)     # reset ONLY the minute window
    assert day_used() == 9 and user is not None
    assert _ask(client, token).status_code == 200      # the tenth
    for k in [k for k in getattr(store, "local", store).storage if k.endswith("/3/1/minute")]:
        getattr(store, "local", store).storage.pop(k)
    assert _ask(client, token).status_code == 429      # the eleventh


def test_breaker_refusals_cannot_be_hammered_past_the_minute_limit(client, monkeypatch, limits_on):
    _fake_ok(monkeypatch)
    token = register_and_login(client, "q_hammer")
    _open_breaker()
    statuses = [_ask(client, token).status_code for _ in range(4)]
    assert statuses == [503, 503, 503, 429]
    assert day_used() == 0


def test_someone_out_of_daily_questions_is_refused_before_anything_else(client, monkeypatch, limits_on):
    """The day limit is still CHECKED on entry; only its deduction moved."""
    _fake_ok(monkeypatch)
    token = register_and_login(client, "q_spent")
    store = appmod.limiter.storage
    local = getattr(store, "local", store)
    for _ in range(10):
        assert _ask(client, token).status_code == 200
        for k in [k for k in local.storage if k.endswith("/3/1/minute")]:
            local.storage.pop(k)
    _open_breaker()
    assert _ask(client, token).status_code == 429
    assert _ask(client, token, ["bad"]).status_code == 429


# ── the breaker's half-open state ─────────────────────────────────────────────

def _past_window():
    """The breaker tripped long enough ago that its open window is over."""
    appmod._COACH_BREAKER["tripped_at"] = time.monotonic() - appmod._COACH_BREAKER_S - 0.1


def test_after_the_open_window_one_slow_failure_reopens_it(client, monkeypatch):
    """The trial question after an open window IS the recovery check: if the
    provider is still stalled, one more 20 s stall is evidence enough. Needing
    two fresh strikes doubled the site freeze on every cycle of an outage."""
    srv = _hostile(monkeypatch, "hang")
    try:
        token = register_and_login(client, "h_reopen")
        _past_window()
        assert _ask(client, token).status_code == 503        # the trial, stalls
        assert srv.requests == 1
        assert appmod._coach_breaker_open()
        t0 = time.monotonic()
        assert _ask(client, token).status_code == 503        # refused at once
        assert time.monotonic() - t0 < 0.5 and srv.requests == 1
    finally:
        srv.close()


def test_a_successful_trial_closes_it_fully(client, monkeypatch):
    _fake_ok(monkeypatch)
    token = register_and_login(client, "h_close")
    _past_window()
    assert _ask(client, token).status_code == 200
    assert appmod._COACH_BREAKER["tripped_at"] is None
    assert appmod._COACH_BREAKER["strikes"] == []


def test_a_fast_failure_during_the_trial_does_not_reopen_it(client, monkeypatch):
    srv = _hostile(monkeypatch, "status", code=500)
    try:
        token = register_and_login(client, "h_fast")
        _past_window()
        _ask(client, token)
        assert not appmod._coach_breaker_open()
        _ask(client, token)
        assert srv.requests == 2
    finally:
        srv.close()


def test_long_after_a_trip_a_single_slow_failure_is_only_a_strike_again(client, monkeypatch):
    """Half-open is a bounded period, not a permanent hair trigger: a day
    later, one long answer must not lock everybody out."""
    srv = _hostile(monkeypatch, "hang")
    try:
        token = register_and_login(client, "h_expired")
        appmod._COACH_BREAKER["tripped_at"] = (time.monotonic() - appmod._COACH_BREAKER_S
                                               - appmod._COACH_BREAKER_HALF_OPEN_S - 0.1)
        _ask(client, token)
        assert not appmod._coach_breaker_open()
        _ask(client, token)
        assert srv.requests == 2
    finally:
        srv.close()


def test_a_success_between_two_slow_failures_means_no_trip(client, monkeypatch):
    """A reply in between proves the provider is answering."""
    srv = _hostile(monkeypatch, "hang")
    try:
        token = register_and_login(client, "h_between")
        _ask(client, token)                                   # strike 1
        assert len(appmod._COACH_BREAKER["strikes"]) == 1
        appmod._coach_breaker_success()
        assert appmod._COACH_BREAKER["strikes"] == []
        _ask(client, token)                                   # strike 1 again, not 2
        assert not appmod._coach_breaker_open()
    finally:
        srv.close()


def test_the_self_check_names_the_half_open_state(client):
    _past_window()
    c = {c["id"]: c for c in client.get("/api/verification/self").get_json()["checks"]}
    assert "breaker half-open" in c["coach.provider_bounds"]["observed"]
