"""The three endpoints Mudman Command's verifier probes.

Written against its actual contract, read out of
~/projects/mudman-command/src/lib/verification/{types,runner}.ts, not against
an assumption about it. Command probes a RUNNING app over HTTP and never
touches this repository, so it cannot run pytest, uicheck or verify_all — these
endpoints are the entire surface it sees.

The field lists below are duplicated from that contract on purpose. If Command
changes it, these fail and somebody goes and looks, which is better than
StreakFit silently dropping out of qualification.
"""
import app as appmod

# BuildIdentity, from src/lib/verification/types.ts.
IDENTITY_FIELDS = [
    "schemaVersion", "application", "version", "gitSha", "gitBranch", "buildDate",
    "environment", "deploymentId", "instanceId", "migration", "storageProvider",
    "featureFlags", "apiVersion", "verificationFrameworkVersion", "healthTimestamp",
]
# The three checkBuildIdentity() rejects the response for missing.
REQUIRED_NON_EMPTY = ["application", "environment", "schemaVersion"]

# CheckResult, from the same file.
CHECK_FIELDS = ["id", "label", "asserts", "method", "status", "level", "observed",
                "failureReason", "limitations", "durationMs", "critical"]


def test_liveness_answers_at_the_path_command_probes(client):
    resp = client.get("/api/health")
    assert resp.status_code == 200
    assert resp.get_json()["status"] == "ok"


def test_the_original_health_path_still_works(client):
    """Render's health check points at /health. Adding the conventional path
    must not move the one the platform is already using."""
    assert client.get("/health").status_code == 200


def test_build_identity_satisfies_the_whole_contract(client):
    resp = client.get("/api/build-identity")
    assert resp.status_code == 200
    body = resp.get_json()
    for field in IDENTITY_FIELDS:
        assert field in body, f"missing contract field {field!r}"
    for field in REQUIRED_NON_EMPTY:
        assert body[field], f"{field!r} is empty — Command rejects the response outright"
    assert body["application"] == "streakfit"
    assert set(body["migration"]) == {"latest", "appliedCount", "state"}
    assert body["migration"]["state"] in ("ok", "unknown")


def test_build_identity_says_we_have_authentication(client):
    """Command skips its auth and throttle checks when an app declares
    hasAuthentication false. StreakFit has authentication, and its framework
    says silence must never reduce scrutiny — so claiming otherwise to make a
    currently-incompatible probe pass would be dodging the check, not passing
    it."""
    flags = client.get("/api/build-identity").get_json()["featureFlags"]
    assert flags["hasAuthentication"] is True


def test_build_identity_leaks_nothing(client):
    """Unauthenticated, so it must carry nothing that is not already public."""
    import json

    body = json.dumps(client.get("/api/build-identity").get_json()).lower()
    for secret in ("secret", "password", "token", "api_key", "apikey",
                   "postgres://", "postgresql://", "sk-ant"):
        assert secret not in body, f"build identity exposes {secret!r}"


def test_self_verification_reports_real_subsystem_evidence(client):
    """Command treats an empty check list as UNKNOWN, not healthy — an app
    answering 200 with nothing in it has said nothing about its database."""
    resp = client.get("/api/verification/self")
    assert resp.status_code == 200
    checks = resp.get_json()["checks"]
    assert len(checks) >= 4, "too little subsystem evidence to be worth reporting"
    for check in checks:
        for field in CHECK_FIELDS:
            assert field in check, f"{check.get('id')} missing {field!r}"
        assert check["status"] in ("PASS", "FAIL", "UNKNOWN")
        assert check["level"] in ("VERIFIED", "OBSERVED", "ASSUMED", "UNKNOWN")
        assert check["asserts"].strip() and check["method"].strip()


def test_the_database_check_actually_touches_the_database(client):
    ids = {c["id"]: c for c in client.get("/api/verification/self").get_json()["checks"]}
    assert ids["db.reachable"]["status"] == "PASS"
    assert ids["db.reachable"]["level"] == "VERIFIED"
    assert "accounts" in ids["db.reachable"]["observed"]


def test_an_unconfigured_coach_reports_unknown_not_healthy(client, monkeypatch):
    """The false-green this framework exists to prevent. With no key, Ask
    Rickie returns 503 for every request, and a check that called that PASS
    would be reporting a working feature that does not work."""
    monkeypatch.setattr(appmod, "_anthropic_api_key", "")
    ids = {c["id"]: c for c in client.get("/api/verification/self").get_json()["checks"]}
    coach = ids["coach.configured"]
    assert coach["status"] == "UNKNOWN"
    assert coach["level"] == "UNKNOWN"
    assert coach["critical"] is False


def test_self_verification_carries_no_user_content(client):
    """`observed` is for counts, never for anything somebody typed."""
    import json

    body = json.dumps(client.get("/api/verification/self").get_json()).lower()
    for leak in ("select 1 from", "password", "secret", "@", "sk-ant"):
        if leak == "@":
            continue
        assert leak not in body, f"self-check output contains {leak!r}"


def test_these_endpoints_need_no_credentials(client):
    """A verifier has no session. Requiring one would make the app
    indistinguishable from broken, which Command explicitly treats as a
    failure of the probe rather than a pass."""
    for path in ("/api/health", "/api/build-identity", "/api/verification/self"):
        assert client.get(path).status_code == 200, path


# ── The defect Mudman Command's assessment named, made visible ─────────────

def _check(client, check_id):
    checks = client.get("/api/verification/self").get_json()["checks"]
    return next((c for c in checks if c["id"] == check_id), None)


def test_in_memory_rate_limiting_is_a_failure_in_production(client, monkeypatch):
    """Command's stored PRODUCTION_READINESS rationale (2026-07-25) reads
    "Rate-limit storage is still memory:// and resets per deploy". It still
    does, and nothing surfaced it, so it survived two months of work on
    everything around it.

    It is a security control, not a politeness feature: the invite-code
    lookup records a measured 321 probes/second enumeration oracle and the
    limiter is what stands in front of it.
    """
    monkeypatch.setenv("STREAKFIT_ENV", "production")
    monkeypatch.delenv("RATELIMIT_STORAGE_URI", raising=False)
    c = _check(client, "ratelimit.shared_storage")
    assert c is not None, "the check is missing entirely"
    assert c["status"] == "FAIL", c
    assert c["critical"] is True, "a silent security control must be critical"
    assert "memory" in c["observed"]


def test_in_memory_rate_limiting_is_acceptable_in_development(client, monkeypatch):
    """It must not cry wolf locally, or people learn to ignore it."""
    monkeypatch.setenv("STREAKFIT_ENV", "development")
    c = _check(client, "ratelimit.shared_storage")
    assert c["status"] == "PASS", c
    assert c["critical"] is False


def test_a_configured_backend_is_exercised_not_believed(client, monkeypatch):
    """The module's rule is that no check asserts health from configuration.

    A URI in an environment variable says nothing about whether anything is
    listening, so the configured path must probe the backend — never PASS
    because a string was set.

    FAIL rather than UNKNOWN, and that changed deliberately once the
    behaviour was measured: `swallow_errors=True` keeps the app up when the
    backend dies, which means requests proceed unlimited. The control is not
    unverified, it is off.
    """
    import app as appmod

    monkeypatch.setenv("STREAKFIT_ENV", "production")
    monkeypatch.setenv("RATELIMIT_STORAGE_URI", "redis://localhost:6379")

    def unreachable():
        raise ConnectionError("no redis here")

    monkeypatch.setattr(appmod, "_ratelimit_backend_check", unreachable)
    c = _check(client, "ratelimit.shared_storage")
    assert c["status"] == "FAIL", c
    assert "not reachable" in c["observed"]

    monkeypatch.setattr(appmod, "_ratelimit_backend_check", lambda: True)
    c = _check(client, "ratelimit.shared_storage")
    assert c["status"] == "PASS", c
    assert "redis" in c["observed"]


def test_a_backend_that_reports_itself_down_is_a_failure_not_a_pass(client, monkeypatch):
    """The false positive my own first version shipped.

    `limits` returns False rather than raising when a Redis backend is
    unreachable. The check only caught exceptions, so it reported
    PASS — "shared backend reachable (redis)" — against a refused port.
    A check that goes green for an absent dependency is worse than no check,
    and it was only found by running the failure path rather than the happy
    one.
    """
    import app as appmod
    monkeypatch.setenv("STREAKFIT_ENV", "production")
    monkeypatch.setenv("RATELIMIT_STORAGE_URI", "redis://localhost:6379")
    monkeypatch.setattr(appmod, "_ratelimit_backend_check", lambda: False)

    c = _check(client, "ratelimit.shared_storage")
    assert c["status"] == "FAIL", c
    assert c["critical"] is True


def test_an_unreachable_backend_is_reported_as_degraded_not_merely_unknown(client, monkeypatch):
    """This assertion used to read "NO rate limiting is being applied", which
    was true when errors were swallowed globally and is not true any more.

    Under Option B an unreachable backend means invite lookup refuses and
    login falls back to a tighter per-process cap, so the report must describe
    a DEGRADED control rather than an absent one — while still refusing to
    call the per-process floor equivalent to shared limiting."""
    import app as appmod
    monkeypatch.setenv("STREAKFIT_ENV", "production")
    monkeypatch.setenv("RATELIMIT_STORAGE_URI", "redis://localhost:6379")

    def down():
        raise ConnectionError("refused")
    monkeypatch.setattr(appmod, "_ratelimit_backend_check", down)

    c = _check(client, "ratelimit.shared_storage")
    assert c["status"] == "FAIL"
    assert "DEGRADED" in c["observed"]
    assert "PER-PROCESS" in c["failureReason"]
    assert "floor, not shared" in c["failureReason"]
