"""Production must not be able to describe itself as development.

The defect this file exists for: `ratelimit.shared_storage` decided whether it
was in production by reading STREAKFIT_ENV, and the live Render service does
not set it. So production reported `memory://` rate limiting as
"(development; acceptable here)", non-critical -- while, in the very same
response, /api/build-identity reported `environment: production`.

One process, one request, two answers.

That mattered because this is a SECURITY check. The limiter stands in front of
an invite-code lookup with a measured 321 probes/second enumeration oracle,
and with `memory://` its counters reset on every deploy and are multiplied by
the worker count. A check that a missing environment variable can silence is
not a check.

The fix routes both through `_detect_environment()`, which derives production
from markers the live service actually has, and keeps an explicit
STREAKFIT_ENV=production as an additional trigger -- never as the only one.
"""
import pytest

import app as appmod


ENV_MARKERS = ("RENDER", "RENDER_SERVICE_ID", "STREAKFIT_ENV",
               "STREAKFIT_ENFORCE_DB_HEAD", "RATELIMIT_STORAGE_URI")


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    """Start every test from a known-empty environment."""
    for k in ENV_MARKERS:
        monkeypatch.delenv(k, raising=False)


def rl_check(client):
    body = client.get("/api/verification/self").get_json()
    return {c["id"]: c for c in body["checks"]}["ratelimit.shared_storage"]


def env_reported(client):
    return client.get("/api/build-identity").get_json()["environment"]


# ── The exact production shape that was silently passing ────────────────────

def test_render_production_without_streakfit_env_fails(client, monkeypatch):
    """THE REGRESSION. This is Render's real environment, verified 2026-09-21:
    RENDER is set, STREAKFIT_ENV is not."""
    monkeypatch.setenv("RENDER", "true")

    check = rl_check(client)
    assert check["status"] == "FAIL", "production called memory:// acceptable"
    assert check["critical"] is True
    assert "acceptable here" not in check["observed"]
    assert check["failureReason"] and "shared storage" in check["failureReason"].lower()


def test_render_service_id_alone_is_enough(client, monkeypatch):
    monkeypatch.setenv("RENDER_SERVICE_ID", "srv-abc123")
    assert rl_check(client)["status"] == "FAIL"


def test_the_inline_db_head_guard_marks_production(client, monkeypatch):
    """The production start command sets STREAKFIT_ENFORCE_DB_HEAD=1 inline on
    gunicorn, so it is a production marker even with RENDER absent."""
    monkeypatch.setenv("STREAKFIT_ENFORCE_DB_HEAD", "1")
    assert rl_check(client)["status"] == "FAIL"


def test_explicit_production_configuration_still_works(client, monkeypatch):
    """STREAKFIT_ENV is kept as an ADDITIONAL trigger so a non-Render
    deployment can declare itself. It is no longer the only one."""
    monkeypatch.setenv("STREAKFIT_ENV", "production")
    check = rl_check(client)
    assert check["status"] == "FAIL"
    assert check["critical"] is True


def test_genuine_local_development_still_passes(client):
    """The fix must not turn every developer's laptop red."""
    check = rl_check(client)
    assert check["status"] == "PASS"
    assert check["critical"] is False
    assert "development" in check["observed"]


# ── The two endpoints must agree ────────────────────────────────────────────

@pytest.mark.parametrize("marker", ["RENDER", "RENDER_SERVICE_ID",
                                    "STREAKFIT_ENFORCE_DB_HEAD"])
def test_build_identity_and_the_rate_limit_check_agree(client, monkeypatch, marker):
    """The defect was that they disagreed. Whatever build-identity calls
    production, this check must treat as production."""
    monkeypatch.setenv(marker, "1")

    assert env_reported(client) == "production"
    assert rl_check(client)["status"] == "FAIL", \
        "build-identity says production but the rate-limit check does not"


def test_removing_streakfit_env_cannot_silence_the_check(client, monkeypatch):
    """The precise failure mode: forgetting one variable used to disable it."""
    monkeypatch.setenv("RENDER", "true")
    monkeypatch.delenv("STREAKFIT_ENV", raising=False)

    check = rl_check(client)
    assert check["status"] == "FAIL"
    assert check["critical"] is True


# ── Configured shared storage, reachable and not ────────────────────────────

def test_production_with_working_shared_storage_passes(client, monkeypatch):
    """The check must still be satisfiable -- the fix must not make production
    permanently red no matter what the operator does."""
    monkeypatch.setenv("RENDER", "true")
    monkeypatch.setenv("RATELIMIT_STORAGE_URI", "redis://localhost:6379")
    monkeypatch.setattr(appmod, "_ratelimit_backend_check", lambda: True)

    check = rl_check(client)
    assert check["status"] == "PASS"
    assert check["critical"] is True, "a passing production check is still critical"
    assert "counting" in check["observed"]


def test_production_with_unreachable_shared_storage_fails_as_degraded(client, monkeypatch):
    """Configured is not working. The probe writes rather than pings, so this
    covers both an unreachable backend and a reachable one that refuses the
    write -- a full memory-capped plan does the latter. Either way the shared
    counters are not being kept, so it is FAIL rather than UNKNOWN."""
    monkeypatch.setenv("RENDER", "true")
    monkeypatch.setenv("RATELIMIT_STORAGE_URI", "redis://localhost:6379")
    monkeypatch.setattr(appmod, "_ratelimit_backend_check", lambda: False)

    check = rl_check(client)
    assert check["status"] == "FAIL"
    assert check["critical"] is True
    assert "DEGRADED" in check["observed"]


def test_a_backend_that_raises_is_also_degraded(client, monkeypatch):
    def boom():
        raise ConnectionError("connection refused")

    monkeypatch.setenv("RENDER", "true")
    monkeypatch.setenv("RATELIMIT_STORAGE_URI", "redis://localhost:6379")
    monkeypatch.setattr(appmod, "_ratelimit_backend_check", boom)

    check = rl_check(client)
    assert check["status"] == "FAIL"
    assert "DEGRADED" in check["observed"]
    assert "ConnectionError" in check["observed"]


def test_the_degraded_message_names_no_credential(client, monkeypatch):
    """A storage URI can carry a password; the observed string is served
    without a credential."""
    monkeypatch.setenv("RENDER", "true")
    monkeypatch.setenv("RATELIMIT_STORAGE_URI", "redis://user:SUPERSECRET@host:6379/0")
    monkeypatch.setattr(appmod, "_ratelimit_backend_check", lambda: False)

    import json
    blob = json.dumps(rl_check(client))
    assert "SUPERSECRET" not in blob
    assert "redis://user" not in blob


# ── The roll-up must feel it ────────────────────────────────────────────────

def test_a_critical_production_failure_reaches_the_roll_up(client, monkeypatch):
    monkeypatch.setenv("RENDER", "true")
    body = client.get("/api/verification/self").get_json()
    assert body["status"] == "FAIL"
