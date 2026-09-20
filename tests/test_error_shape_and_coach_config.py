"""Errors answer in one language, and the coach's model is configuration.

Three small things that each have the same shape: a value or a behaviour that
was correct but unreachable — a model buried in a retry loop, a 403 answering
in HTML while everything around it answered in JSON, a lost conversation whose
only trace was a log line.
"""
import app as appmod
from conftest import auth_headers, register_and_login


# ── Every error speaks JSON ────────────────────────────────────────────────

def test_the_admin_gate_refuses_in_json(client):
    """It returned Flask's default HTML. Any client parsing the body as JSON
    chokes on `<!doctype html>`, and the browser client does exactly that."""
    resp = client.get("/api/admin/stats")
    assert resp.status_code == 403
    assert resp.headers["Content-Type"].startswith("application/json")
    assert resp.get_json()["error"] == "Forbidden"


def test_an_oversized_body_refuses_in_json(client):
    """The photo ceiling is enforced by Werkzeug before any view runs, so the
    response has to be shaped by an error handler or it is HTML."""
    resp = client.post("/api/teams/1/photo", data=b"x" * (3 * 1024 * 1024),
                       content_type="application/octet-stream")
    assert resp.status_code == 413
    assert resp.headers["Content-Type"].startswith("application/json")
    assert resp.get_json()["error"]


def test_every_api_error_a_client_can_provoke_is_json(client):
    """The general rule, rather than three specific codes. A client that has
    to sniff the content type before parsing has no contract at all."""
    token = register_and_login(client, "shape_probe")
    probes = [
        ("GET", "/api/admin/stats", None, {}),              # 403
        ("GET", "/api/nope-not-a-route", None, {}),         # 404
        ("GET", "/api/me", None, {}),                       # 401, no auth
        ("POST", "/api/coach", {"message": ""}, auth_headers(token)),   # 400
    ]
    for method, path, body, headers in probes:
        resp = client.open(path, method=method, json=body, headers=headers)
        assert resp.status_code >= 400, (path, resp.status_code)
        assert resp.headers["Content-Type"].startswith("application/json"), \
            f"{method} {path} answered {resp.headers['Content-Type']}"
        assert isinstance(resp.get_json(), dict), path


# ── The coach's model is configuration, not a literal in a loop ────────────

def test_the_coach_model_and_budget_are_configurable():
    """A model bump is a routine operational act — a deprecation, a price
    change, pinning back after a regression. It should not require finding a
    literal inside a retry loop three thousand lines down."""
    import inspect

    assert appmod.COACH_MODEL
    assert isinstance(appmod.COACH_MAX_TOKENS, int) and appmod.COACH_MAX_TOKENS > 0

    src = inspect.getsource(appmod.coach)
    assert "model=COACH_MODEL" in src
    assert "max_tokens=COACH_MAX_TOKENS" in src
    assert "'claude-sonnet-5'" not in src, "the model is still hard-coded in the route"


def test_the_coach_model_can_be_overridden_by_environment():
    """Per-environment override is the point: a cheaper model while somebody
    is exercising the app, the pinned one while they are evaluating it.

    Checked in a SUBPROCESS rather than with importlib.reload. Reloading app.py
    re-executes it and rebinds every module global, while the Flask app object
    the test client already holds keeps closures over the OLD ones — so a
    reload here left the rest of this file asserting against a module that was
    no longer the one serving requests. It cost an hour of a fault injection
    that looked like a product bug and was a test bug.
    """
    import os
    import subprocess
    import sys
    from pathlib import Path

    root = Path(appmod.__file__).resolve().parent
    env = dict(os.environ,
               SECRET_KEY="x", JWT_SECRET_KEY="x",
               STREAKFIT_COACH_MODEL="claude-haiku-4-5-20251001",
               STREAKFIT_COACH_MAX_TOKENS="256")
    result = subprocess.run(
        [sys.executable, "-c",
         "import app; print(app.COACH_MODEL); print(app.COACH_MAX_TOKENS)"],
        cwd=str(root), env=env, capture_output=True, text=True, timeout=120)
    assert result.returncode == 0, result.stderr[-1500:]
    model, budget = result.stdout.strip().splitlines()[-2:]
    assert model == "claude-haiku-4-5-20251001"
    assert budget == "256"


# ── A lost conversation is visible ─────────────────────────────────────────

def test_a_lost_conversation_shows_up_in_the_health_surface(client):
    """It used to exist only as a log line. Memory loss is the one failure a
    user notices and the operator does not."""
    appmod._COACH_HEALTH["persist_failures"] = 0
    appmod._COACH_HEALTH["last_persist_failure"] = None
    checks = {c["id"]: c for c in client.get("/api/verification/self").get_json()["checks"]}
    assert "coach.memory_writes" in checks

    quiet = checks["coach.memory_writes"]
    assert quiet["status"] == "UNKNOWN", \
        "zero failures since boot is not evidence that the path works"
    assert quiet["level"] == "UNKNOWN"
    assert quiet["limitations"], "an UNKNOWN with no stated limitation explains nothing"

    appmod._COACH_HEALTH["persist_failures"] = 3
    appmod._COACH_HEALTH["last_persist_failure"] = "OperationalError"
    try:
        loud = {c["id"]: c for c in
                client.get("/api/verification/self").get_json()["checks"]}["coach.memory_writes"]
        assert loud["status"] == "FAIL"
        assert loud["level"] == "VERIFIED"
        assert "3" in loud["observed"] and "OperationalError" in loud["observed"]
        assert loud["failureReason"]
    finally:
        appmod._COACH_HEALTH["persist_failures"] = 0
        appmod._COACH_HEALTH["last_persist_failure"] = None


def test_the_failure_counter_never_carries_what_somebody_typed(client):
    """Same rule as the log line it sits beside: the exception CLASS, never
    the message, because a database error carries the row it was inserting."""
    appmod._COACH_HEALTH["persist_failures"] = 1
    appmod._COACH_HEALTH["last_persist_failure"] = "IntegrityError"
    try:
        import json

        body = json.dumps(client.get("/api/verification/self").get_json())
        assert "IntegrityError" in body
        for leak in ("INSERT INTO", "parameters:", "VALUES ("):
            assert leak not in body
    finally:
        appmod._COACH_HEALTH["persist_failures"] = 0
        appmod._COACH_HEALTH["last_persist_failure"] = None


def test_a_real_persist_failure_actually_increments_the_counter(client, monkeypatch):
    """Drives the failure through /api/coach rather than setting the counter.

    The test above sets `_COACH_HEALTH` by hand, which proves the health
    endpoint REPORTS a count — and fault injection showed it passes happily
    with the increment removed, because nothing in it ever exercises the line
    that counts. A test that only checks the reporting shares the blind spot of
    the thing it is testing.
    """
    from test_coach import _install_fake_anthropic

    _install_fake_anthropic(monkeypatch)
    monkeypatch.setattr(
        appmod, "_stage_coach_note",
        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("write failed")))

    appmod._COACH_HEALTH["persist_failures"] = 0
    appmod._COACH_HEALTH["last_persist_failure"] = None
    token = register_and_login(client, "persist_counter")
    try:
        # "I love walking" so the Coach Notes extractor actually finds a token
        # — the staging call this patches only runs when there is something to
        # stage, so a message with nothing in the closed vocabulary would have
        # sailed through and the test would have proved nothing.
        resp = client.post("/api/coach",
                           json={"message": "I love walking",
                                 "context": {"type": "general"}},
                           headers=auth_headers(token))
        # The reply still returns — memory is best-effort and must never take
        # the conversation down with it.
        assert resp.status_code == 200
        assert resp.get_json()["reply"]

        assert appmod._COACH_HEALTH["persist_failures"] == 1, \
            "a conversation was lost and nothing counted it"
        assert appmod._COACH_HEALTH["last_persist_failure"] == "RuntimeError"

        surfaced = {c["id"]: c for c in
                    client.get("/api/verification/self").get_json()["checks"]}
        assert surfaced["coach.memory_writes"]["status"] == "FAIL"
    finally:
        appmod._COACH_HEALTH["persist_failures"] = 0
        appmod._COACH_HEALTH["last_persist_failure"] = None


def test_the_reply_budget_cannot_be_raised_without_limit():
    """Making the budget configurable without a ceiling turned an environment
    variable into an unbounded spending control.

    Output tokens are the expensive half of a reply. A typo, a pasted value or
    a bad rollout must not be able to multiply the bill, so the value is
    clamped rather than merely defaulted.
    """
    assert appmod.COACH_MAX_TOKENS_CEILING <= 4096, "the ceiling is not a ceiling"
    assert appmod.COACH_MAX_TOKENS_FLOOR >= 1
    assert (appmod.COACH_MAX_TOKENS_FLOOR
            <= appmod.COACH_MAX_TOKENS_DEFAULT
            <= appmod.COACH_MAX_TOKENS_CEILING)
    # The live value is inside the band whatever the environment says.
    assert (appmod.COACH_MAX_TOKENS_FLOOR
            <= appmod.COACH_MAX_TOKENS
            <= appmod.COACH_MAX_TOKENS_CEILING)


def test_an_absurd_or_malformed_budget_lands_somewhere_safe():
    """Checked in a subprocess: reloading the app module in-process rebinds
    globals the live Flask app still closes over. See
    tests/test_error_shape_and_coach_config.py's model-override test."""
    import os
    import subprocess
    import sys
    from pathlib import Path

    root = Path(appmod.__file__).resolve().parent
    cases = {
        "200000": appmod.COACH_MAX_TOKENS_CEILING,   # clamped down
        "0": appmod.COACH_MAX_TOKENS_FLOOR,          # clamped up
        "-5": appmod.COACH_MAX_TOKENS_FLOOR,
        "abc": appmod.COACH_MAX_TOKENS_DEFAULT,      # unparseable -> default
        "": appmod.COACH_MAX_TOKENS_DEFAULT,
        "1500": 1500,                                # a legitimate override
    }
    for raw, expected in cases.items():
        env = dict(os.environ, SECRET_KEY="x", JWT_SECRET_KEY="x",
                   STREAKFIT_COACH_MAX_TOKENS=raw)
        result = subprocess.run(
            [sys.executable, "-c", "import app; print(app.COACH_MAX_TOKENS)"],
            cwd=str(root), env=env, capture_output=True, text=True, timeout=120)
        assert result.returncode == 0, (
            f"STREAKFIT_COACH_MAX_TOKENS={raw!r} stopped the app booting:\n"
            f"{result.stderr[-800:]}")
        got = int(result.stdout.strip().splitlines()[-1])
        assert got == expected, f"{raw!r} -> {got}, expected {expected}"


def test_the_configured_model_is_announced_at_boot():
    """A model name carries no price, so it cannot be bounded by arithmetic.
    It is logged instead: an unexpected model is a cost change and belongs in
    the first lines of a deploy, not on an invoice."""
    import inspect

    src = inspect.getsource(appmod)
    assert "coach configured: model=%s max_tokens=%d" in src
