"""API contract tests — systematic status-code and validation checks across the
main endpoints: unauthenticated access, malformed/missing bodies, boundary values,
and success. Meaningful invariants, not fragile snapshots.
"""
import app as appmod
from conftest import register_and_login, auth_headers


AUTH_REQUIRED = [
    ("GET", "/api/me"),
    ("POST", "/api/coach"),
    ("DELETE", "/api/coach/memory"),
    ("GET", "/api/teams/1"),
    ("GET", "/api/teams/1/messages"),
    ("GET", "/api/teams/1/moments"),
]


def test_protected_endpoints_require_auth(client):
    for method, path in AUTH_REQUIRED:
        r = client.open(path, method=method)
        assert r.status_code in (401, 422), f"{method} {path} -> {r.status_code}"


# ── register ─────────────────────────────────────────────────────────────────

def test_register_missing_fields_400(client):
    assert client.post("/api/register", json={}).status_code == 400


def test_register_short_password_400(client):
    assert client.post("/api/register",
                       json={"username": "u", "password": "short"}).status_code == 400


def test_register_success_then_duplicate(client):
    assert client.post("/api/register",
                       json={"username": "contract_u", "password": "ValidPass123"}).status_code == 201
    assert client.post("/api/register",
                       json={"username": "contract_u", "password": "ValidPass123"}).status_code == 400


# ── login ────────────────────────────────────────────────────────────────────

def test_login_missing_fields_400(client):
    assert client.post("/api/login", json={}).status_code == 400


def test_login_wrong_password_401(client):
    client.post("/api/register", json={"username": "contract_l", "password": "ValidPass123"})
    assert client.post("/api/login",
                       json={"username": "contract_l", "password": "WRONGpass1"}).status_code == 401


def test_login_success_returns_token(client):
    client.post("/api/register", json={"username": "contract_ok", "password": "ValidPass123"})
    r = client.post("/api/login", json={"username": "contract_ok", "password": "ValidPass123"})
    assert r.status_code == 200 and "access_token" in r.get_json()


# ── coach validation (before the API-key / model call) ───────────────────────

def test_coach_missing_message_400(client):
    token = register_and_login(client, "contract_c")
    r = client.post("/api/coach", json={"context": {"type": "general"}}, headers=auth_headers(token))
    assert r.status_code == 400


def test_coach_message_too_long_400(client):
    token = register_and_login(client, "contract_c2")
    r = client.post("/api/coach", json={"message": "x" * 600, "context": {"type": "general"}},
                    headers=auth_headers(token))
    assert r.status_code == 400


def test_coach_invalid_context_type_400(client):
    token = register_and_login(client, "contract_c3")
    r = client.post("/api/coach", json={"message": "hi", "context": {"type": "bogus"}},
                    headers=auth_headers(token))
    assert r.status_code == 400


def test_coach_unavailable_without_key_503(client, monkeypatch):
    monkeypatch.setattr(appmod, "_anthropic_api_key", None)
    token = register_and_login(client, "contract_c4")
    r = client.post("/api/coach", json={"message": "hi", "context": {"type": "general"}},
                    headers=auth_headers(token))
    assert r.status_code == 503


# ── coach memory delete ──────────────────────────────────────────────────────

def test_forget_memory_success_and_idempotent(client):
    token = register_and_login(client, "contract_f")
    assert client.delete("/api/coach/memory", headers=auth_headers(token)).status_code == 200
    assert client.delete("/api/coach/memory", headers=auth_headers(token)).status_code == 200


# ── team authorization: a non-member can't read a team ───────────────────────

def test_team_read_forbidden_for_non_member(client):
    # create a team owned by user A
    ta = register_and_login(client, "team_owner_x")
    created = client.post("/api/teams", json={"name": "Contract Team"}, headers=auth_headers(ta))
    assert created.status_code in (200, 201)
    team_id = created.get_json()["team"]["id"]
    # user B (not a member) is forbidden
    tb = register_and_login(client, "team_outsider_y")
    r = client.get(f"/api/teams/{team_id}", headers=auth_headers(tb))
    assert r.status_code == 403
