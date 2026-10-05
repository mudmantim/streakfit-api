"""D64: request bodies are bounded by size whether or not their length is known.

The 256 KB per-route ceiling (2 MB for photo upload) was enforced only from
the Content-Length HEADER. A chunked upload carries no Content-Length, so it
skipped the check and was bounded only by the global 2 MB photo ceiling --
and Werkzeug's bounded stream returns the first 2 MB SILENTLY rather than
refusing. A chunked body of any size whose JSON finished inside that prefix
(trailing whitespace) was processed: users registered, messages posted, a
paid model call made. Two independent reproductions under real gunicorn:
e2e-campaign/evidence/d64/phase2/.

These tests present bodies exactly as gunicorn does for chunked transfer
encoding: no Content-Length, `wsgi.input_terminated` set.
"""
import io
import json

import pytest

import app as appmod
from conftest import register_and_login, auth_headers
from test_team_photos import VALID_JPEG

L = 256 * 1024
PHOTO = 2 * 1024 * 1024


def chunked(client, path, body, content_type="application/json", headers=None, method="POST"):
    """Send `body` the way gunicorn delivers a chunked request to the app."""
    return client.open(path, method=method, input_stream=io.BytesIO(body),
                       content_type=content_type,
                       headers={"Transfer-Encoding": "chunked", **(headers or {})},
                       environ_overrides={"wsgi.input_terminated": True})


def padded_json(obj, total):
    """Valid JSON followed by whitespace up to exactly `total` bytes."""
    raw = json.dumps(obj).encode()
    return raw + b" " * (total - len(raw))


def multipart(file_bytes, boundary="d64boundary"):
    head = (f"--{boundary}\r\nContent-Disposition: form-data; name=\"photo\"; "
            f"filename=\"p.jpg\"\r\nContent-Type: image/jpeg\r\n\r\n").encode()
    return head + file_bytes + f"\r\n--{boundary}--\r\n".encode(), \
        f"multipart/form-data; boundary={boundary}"


@pytest.fixture()
def family(client):
    """A parent who owns a team, a kid in it, and an outsider who is not."""
    parent = register_and_login(client, "body_parent")
    team = client.post("/api/teams", json={"name": "Body Family"},
                       headers=auth_headers(parent)).get_json()["team"]
    kid = register_and_login(client, "body_kid")
    client.post(f"/api/teams/{team['id']}/join", json={"code": team["invite_code"]},
                headers=auth_headers(kid))
    outsider = register_and_login(client, "body_outsider")
    return {"parent": parent, "kid": kid, "outsider": outsider, "team_id": team["id"]}


def _user(name):
    return appmod.User.query.filter_by(username=name).first()


# ── an over-limit chunked body is refused, not truncated and processed ───────

def test_an_oversized_chunked_body_cannot_register_anyone(client):
    """3 MB of valid JSON plus padding used to create the account from the
    first 2 MB."""
    body = padded_json({"username": "d64_trunc", "password": "WalkTest123!"}, 3 * 1024 * 1024)
    resp = chunked(client, "/api/register", body)
    assert resp.status_code == 413
    assert resp.get_json() == {"error": "payload_too_large"}
    assert _user("d64_trunc") is None


@pytest.mark.parametrize("size", [L + 1, PHOTO, PHOTO + 1, 8 * 1024 * 1024])
def test_the_json_ceiling_holds_for_chunked_bodies(client, size):
    body = padded_json({"username": f"d64_{size}", "password": "WalkTest123!"}, size)
    resp = chunked(client, "/api/register", body)
    assert resp.status_code == 413, size
    assert resp.get_json() == {"error": "payload_too_large"}
    assert _user(f"d64_{size}") is None


@pytest.mark.parametrize("size", [L - 1, L])
def test_a_chunked_body_up_to_the_ceiling_is_still_accepted(client, size):
    body = padded_json({"username": f"d64_ok_{size}", "password": "WalkTest123!"}, size)
    resp = chunked(client, "/api/register", body)
    assert resp.status_code == 201, (size, resp.get_data(as_text=True)[:200])
    assert _user(f"d64_ok_{size}") is not None


def test_an_oversized_chunked_body_cannot_post_a_team_message(client, family):
    body = padded_json({"body": "D64-TRUNC-MSG"}, 3 * 1024 * 1024)
    resp = chunked(client, f"/api/teams/{family['team_id']}/messages", body,
                   headers=auth_headers(family["kid"]))
    assert resp.status_code == 413
    msgs = client.get(f"/api/teams/{family['team_id']}/messages",
                      headers=auth_headers(family["kid"])).get_json()
    assert "D64-TRUNC-MSG" not in json.dumps(msgs)


def test_an_oversized_chunked_body_never_reaches_the_model(client, monkeypatch):
    calls = []

    class _Messages:
        def create(self, **kw):
            calls.append(kw)
            raise AssertionError("the model must not be called")

    class _Client:
        def __init__(self, *a, **kw):
            self.messages = _Messages()

    monkeypatch.setattr(appmod, "_anthropic_api_key", "test-key-not-real")
    monkeypatch.setattr(appmod._anthropic_lib, "Anthropic", _Client)
    token = register_and_login(client, "d64_coach")
    resp = chunked(client, "/api/coach", padded_json({"message": "hi"}, 3 * 1024 * 1024),
                   headers=auth_headers(token))
    assert resp.status_code == 413
    assert calls == []


def test_an_unknown_route_does_not_read_a_chunked_body_at_all(client):
    """No view will run and nothing parses it: 404 without consuming it."""
    stream = io.BytesIO(b" " * (L + 1))
    resp = client.open("/api/no-such-route", method="POST", input_stream=stream,
                       content_type="application/json", headers={"Transfer-Encoding": "chunked"},
                       environ_overrides={"wsgi.input_terminated": True})
    assert resp.status_code == 404
    assert stream.tell() == 0


def test_the_size_refusal_comes_before_authentication(client):
    """Nothing about the request -- not even who sent it -- is looked at
    first, exactly as with an oversized Content-Length body (413, not 401)."""
    resp = chunked(client, "/api/me", padded_json({"display_name": "x"}, L + 1), method="PATCH",
                   headers={"Authorization": "Bearer not-a-real-token"})
    assert resp.status_code == 413


# ── the photo route keeps its larger ceiling under both framings ─────────────

def test_a_chunked_photo_upload_still_works(client, family):
    body, ctype = multipart(VALID_JPEG)
    resp = chunked(client, f"/api/teams/{family['team_id']}/photos", body, content_type=ctype,
                   headers=auth_headers(family["kid"]))
    assert resp.status_code == 201, resp.get_data(as_text=True)[:200]


def test_a_chunked_photo_body_larger_than_the_json_ceiling_is_accepted(client, family):
    body, ctype = multipart(VALID_JPEG + b"\x00" * 400_000)
    resp = chunked(client, f"/api/teams/{family['team_id']}/photos", body, content_type=ctype,
                   headers=auth_headers(family["kid"]))
    assert resp.status_code == 201


def test_a_chunked_photo_body_at_exactly_the_photo_ceiling_is_accepted(client, family):
    """It used to get Werkzeug's HTML 413 at exactly 2 MB."""
    body, ctype = multipart(b"")
    body, ctype = multipart(VALID_JPEG + b"\x00" * (PHOTO - len(body) - len(VALID_JPEG)))
    assert len(body) == PHOTO
    resp = chunked(client, f"/api/teams/{family['team_id']}/photos", body, content_type=ctype,
                   headers=auth_headers(family["kid"]))
    assert resp.status_code in (201, 400), resp.get_data(as_text=True)[:200]
    assert resp.headers["Content-Type"].startswith("application/json")
    assert resp.status_code != 413


def test_a_chunked_photo_body_over_the_photo_ceiling_is_a_json_413(client, family):
    body, ctype = multipart(VALID_JPEG + b"\x00" * PHOTO)
    resp = chunked(client, f"/api/teams/{family['team_id']}/photos", body, content_type=ctype,
                   headers=auth_headers(family["kid"]))
    assert resp.status_code == 413
    assert resp.get_json() == {"error": "payload_too_large"}


def test_an_outsider_with_a_huge_chunked_photo_is_refused_for_size_before_any_lookup(client, family):
    """The membership and suspension queries used to run before the body was
    looked at; the size refusal now comes first, as it does with Content-Length."""
    body, ctype = multipart(b"\x00" * (PHOTO + 1))
    resp = chunked(client, f"/api/teams/{family['team_id']}/photos", body, content_type=ctype,
                   headers=auth_headers(family["outsider"]))
    assert resp.status_code == 413


# ── Content-Length behaviour is unchanged ─────────────────────────────────────

def test_content_length_bodies_keep_their_exact_boundaries(client):
    ok = padded_json({"username": "d64_cl_ok", "password": "WalkTest123!"}, L)
    assert client.post("/api/register", data=ok, content_type="application/json").status_code == 201
    over = padded_json({"username": "d64_cl_over", "password": "WalkTest123!"}, L + 1)
    resp = client.post("/api/register", data=over, content_type="application/json")
    assert resp.status_code == 413 and resp.get_json() == {"error": "payload_too_large"}
    assert _user("d64_cl_over") is None


def test_requests_without_a_body_are_untouched(client):
    assert client.get("/health").status_code == 200
    resp = chunked(client, "/health", b"", method="GET")
    assert resp.status_code == 200


def test_any_framework_413_is_json(client):
    """Werkzeug raises its own 413 in places the hook cannot see (it did at
    exactly 2 MB on the photo route); it must not come back as an HTML page."""
    from werkzeug.exceptions import RequestEntityTooLarge
    with appmod.app.test_request_context("/api/x", method="POST"):
        resp = appmod.app.make_response(
            appmod.app.handle_http_exception(RequestEntityTooLarge()))
    assert resp.status_code == 413
    assert resp.headers["Content-Type"].startswith("application/json")
    assert resp.get_json() == {"error": "payload_too_large"}


# ── rate limits: an oversized chunked body costs nothing, like Content-Length ─

def test_an_oversized_chunked_body_is_not_counted_by_the_rate_limiter(client):
    appmod.limiter.enabled = True
    appmod.limiter.reset()
    try:
        for _ in range(12):        # /api/events is limited; oversize must not use it up
            resp = chunked(client, "/api/events", padded_json({"name": "x"}, L + 1))
            assert resp.status_code == 413
        store = appmod.limiter.storage
        store = getattr(store, "local", store)
        assert sum(store.storage.values()) == 0
    finally:
        appmod.limiter.reset()
        appmod.limiter.enabled = False


# ── from the candidate review ─────────────────────────────────────────────────

class _ExplodingStream(io.BytesIO):
    """A stream whose read fails the way gunicorn's chunked parser does on a
    malformed trailer (its InvalidHeader is not an OSError). Seekable, because
    the test client seeks its input; every READ raises `error`."""

    def __init__(self, error=None):
        super().__init__(b"x")
        self.error = error or ValueError("Invalid HTTP Header: 'no colon here'")

    def read(self, *a):
        raise self.error

    readinto = read
    readline = read


def test_a_body_that_cannot_be_read_is_a_json_400_not_a_500(client):
    resp = client.open("/api/register", method="POST", input_stream=_ExplodingStream(),
                       content_type="application/json", headers={"Transfer-Encoding": "chunked"},
                       environ_overrides={"wsgi.input_terminated": True})
    assert resp.status_code == 400
    assert resp.headers["Content-Type"].startswith("application/json")


def test_a_body_parser_error_that_is_not_an_oserror_is_a_400_too(client):
    class _ParserError(Exception):
        pass

    resp = client.open("/api/register", method="POST",
                       input_stream=_ExplodingStream(_ParserError("LimitRequestHeaders")),
                       content_type="application/json", headers={"Transfer-Encoding": "chunked"},
                       environ_overrides={"wsgi.input_terminated": True})
    assert resp.status_code == 400


@pytest.mark.parametrize("method", ["GET", "HEAD", "OPTIONS"])
def test_a_body_sent_with_a_bodiless_method_is_not_read(client, method):
    """No GET/HEAD/OPTIONS view reads a body, so reading one before routing
    only gives a slow sender the worker (D67)."""
    stream = io.BytesIO(b" " * (L + 1))
    resp = client.open("/health", method=method, input_stream=stream,
                       content_type="application/json", headers={"Transfer-Encoding": "chunked"},
                       environ_overrides={"wsgi.input_terminated": True})
    assert resp.status_code in (200, 204)
    assert stream.tell() == 0


def test_no_bodiless_method_view_reads_a_body():
    """The rule above is only safe while it is true: a GET view that parsed a
    body would get it silently truncated at the limit, which is D64 again."""
    import ast
    import re
    from pathlib import Path
    src = (Path(appmod.__file__)).read_text(encoding="utf-8")
    offenders = []
    for node in ast.walk(ast.parse(src)):
        if not isinstance(node, ast.FunctionDef):
            continue
        decs = " ".join(ast.get_source_segment(src, d) or "" for d in node.decorator_list)
        if "app.route" not in decs:
            continue
        m = re.search(r"methods=\[([^\]]*)\]", decs)
        methods = m.group(1) if m else "'GET'"
        if any(x in methods for x in ("POST", "PUT", "PATCH", "DELETE")):
            continue
        body = ast.get_source_segment(src, node) or ""
        if any(k in body for k in ("get_json", "request.form", "request.files", "get_data",
                                   "request.data", "request.values", "request.stream")):
            offenders.append(node.name)
    assert offenders == [], offenders
