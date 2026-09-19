"""What Ask Rickie keeps, for how long, and who else sees it.

Coach Notes were rebuilt as a closed vocabulary and that work is sound — but
it only ever governed the small structured record. The CONVERSATION is a
separate system, and it stores exactly what was typed. Saying the feature is
child-safe because Coach Notes uses an allow-list would be saying a true thing
about the wrong table.

These tests are the review, written down so it stays true. Several of them
assert uncomfortable facts rather than reassuring ones: that is deliberate, and
the ones that remain uncomfortable are named as such in the docstrings.
"""
import datetime

import app as appmod
from app import CoachNote, CoachTurn, User, db
from conftest import auth_headers, register_and_login

SENSITIVE = "I think I am fat and I have been skipping meals"


def _user(username):
    u = User(username=username, password_hash="x")
    db.session.add(u)
    db.session.commit()
    return u


# ── What is actually stored ─────────────────────────────────────────────────

def test_the_conversation_stores_what_was_typed_word_for_word(app):
    """The uncomfortable one, and the reason this file exists.

    Coach Notes hold canonical tokens and nothing else. The conversation holds
    the sentence. Both halves are true at once, and the second half is not
    fixed by the first.
    """
    u = _user("stores_verbatim")
    appmod._record_coach_exchange(u.id, SENSITIVE, "That sounds hard.")

    stored = [t.content for t in CoachTurn.query.filter_by(user_id=u.id)]
    assert SENSITIVE in stored, "the conversation is stored verbatim — this is the finding"
    assert appmod._load_coach_note_block(u.id) == "", \
        "and the structured record correctly kept none of it"


def test_a_stored_turn_is_length_capped(app):
    u = _user("capped")
    appmod._record_coach_exchange(u.id, "x" * 5000, "y" * 5000)
    for t in CoachTurn.query.filter_by(user_id=u.id):
        assert len(t.content) <= appmod._COACH_TURN_MAX_LEN


# ── How long it survives ────────────────────────────────────────────────────

def test_the_ten_turn_window_is_actually_enforced(app):
    u = _user("window")
    for i in range(12):
        appmod._record_coach_exchange(u.id, f"message {i}", f"reply {i}")
    assert CoachTurn.query.filter_by(user_id=u.id).count() <= appmod._COACH_MEMORY_WINDOW


def test_a_count_limit_is_not_a_retention_policy(app):
    """The distinction the review was asked to make.

    Ten turns is how much context Rickie gets. It says nothing about time: the
    prune only ran when an eleventh turn arrived, so somebody who said
    something difficult and never opened the app again kept it indefinitely —
    exactly the person for whom it matters most. There is now an age bound too.
    """
    u = _user("dormant")
    appmod._record_coach_exchange(u.id, SENSITIVE, "ok")
    old = datetime.datetime.utcnow() - datetime.timedelta(
        days=appmod._COACH_TURN_MAX_AGE_DAYS + 1)
    for t in CoachTurn.query.filter_by(user_id=u.id):
        t.created_at = old
    db.session.commit()

    # No new message is ever written. Reading is what expires it.
    appmod._load_coach_messages(u.id)
    assert CoachTurn.query.filter_by(user_id=u.id).count() == 0, \
        "a dormant account's conversation never expired"


def test_recent_turns_are_not_expired(app):
    u = _user("recent")
    appmod._record_coach_exchange(u.id, "hello", "hi")
    appmod._load_coach_messages(u.id)
    assert CoachTurn.query.filter_by(user_id=u.id).count() == 2


# ── Who can see it ──────────────────────────────────────────────────────────

def test_history_follows_the_account_not_the_device(app):
    """Stated plainly because it is a privacy fact, not a bug: the history is
    server-side and keyed by user. Anyone holding that account's credentials,
    on any device, gets a Rickie primed with its conversation."""
    u = _user("across_devices")
    appmod._record_coach_exchange(u.id, "I hate PE", "That's fair.")
    assert any("I hate PE" in m["content"] for m in appmod._load_coach_messages(u.id))


def test_one_persons_conversation_never_reaches_another(app):
    a, b = _user("iso_a1"), _user("iso_b1")
    appmod._record_coach_exchange(a.id, SENSITIVE, "ok")
    assert appmod._load_coach_messages(b.id) == []


# ── Forgetting, and what the export admits to ───────────────────────────────

def test_forget_conversations_clears_both_systems(client):
    token = register_and_login(client, "forget_both")
    uid = User.query.filter_by(username="forget_both").first().id
    appmod._record_coach_exchange(uid, SENSITIVE, "ok")
    appmod._update_coach_note(uid, "I love walking")
    assert CoachTurn.query.filter_by(user_id=uid).count() > 0
    assert CoachNote.query.filter_by(user_id=uid).count() == 1

    assert client.delete("/api/coach/memory", headers=auth_headers(token)).status_code == 200
    assert CoachTurn.query.filter_by(user_id=uid).count() == 0
    assert CoachNote.query.filter_by(user_id=uid).count() == 0


def test_the_export_shows_the_conversation_that_is_still_held(client):
    token = register_and_login(client, "export_convo")
    uid = User.query.filter_by(username="export_convo").first().id
    appmod._record_coach_exchange(uid, SENSITIVE, "That sounds hard.")
    db.session.rollback()

    data = client.get("/api/me/data", headers=auth_headers(token)).get_json()
    contents = [t["content"] for t in data["coach_conversation"]]
    assert SENSITIVE in contents, \
        "the export must not be tidier than the database it describes"
    assert data["retention"]["coach_conversation_days"] == appmod._COACH_TURN_MAX_AGE_DAYS


def test_deleting_the_account_takes_the_conversation_with_it(client):
    token = register_and_login(client, "delete_convo")
    uid = User.query.filter_by(username="delete_convo").first().id
    appmod._record_coach_exchange(uid, SENSITIVE, "ok")
    resp = client.delete("/api/me", json={"password": "WalkTest123!"},
                         headers=auth_headers(token))
    assert resp.status_code == 200, resp.get_json()
    assert CoachTurn.query.filter_by(user_id=uid).count() == 0


# ── What reaches the logs ───────────────────────────────────────────────────

def test_a_database_error_never_writes_the_message_into_an_exception(app):
    """A SQLAlchemy error normally carries its bound parameters into the
    message — "[parameters: ('I think I am fat', ...)]" — and anything logging
    that with a traceback writes a child's words into the application log.
    `hide_parameters` on the engine removes the values at the source.
    """
    from sqlalchemy import text

    u = _user("logleak")
    appmod._record_coach_exchange(u.id, SENSITIVE, "ok")
    try:
        db.session.execute(text("INSERT INTO coach_turn (user_id, role, content) "
                                "VALUES (:u, :r, :c)"),
                           {"u": 10 ** 12, "r": "user", "c": SENSITIVE})
        db.session.execute(text("SELECT nonexistent_column FROM coach_turn"))
    except Exception as exc:
        rendered = str(exc)
        assert SENSITIVE not in rendered, \
            f"a database error carried the message text: {rendered[:200]}"
    finally:
        db.session.rollback()


def test_the_coach_failure_path_logs_a_type_not_a_traceback(app):
    """Belt and braces behind hide_parameters: the handler that runs when
    saving a coach turn fails logs the exception class and nothing else."""
    import inspect

    src = inspect.getsource(appmod.coach)
    marker = "coach memory persist failed"
    assert marker in src
    line = next(ln for ln in src.splitlines() if marker in ln)
    assert "exc_info" not in line, line.strip()
    assert "type(exc).__name__" in src


def test_no_coach_log_line_interpolates_message_content(app):
    """Every logger call in the coach path, checked by reading it."""
    import inspect
    import re

    for fn in (appmod.coach, appmod._persist_coach_interaction,
               appmod._record_coach_exchange, appmod._update_coach_note):
        for line in inspect.getsource(fn).splitlines():
            if "logger." not in line:
                continue
            for banned in ("message", "user_msg", "reply", "content", "text"):
                assert not re.search(r"\b%s\b" % banned, line), \
                    f"{fn.__name__} may log message content: {line.strip()}"


# ── Spend, limits and failure, verified before anyone spends money ──────────

def test_the_coach_limits_are_what_we_think_they_are(app):
    """Pinned because the live evaluation is about to run against a real key
    and these are the only things standing between it and an open tab."""
    import inspect

    src = inspect.getsource(appmod.coach)
    assert '"10 per day"' in src or "'10 per day'" in src
    assert '"3 per minute"' in src or "'3 per minute'" in src
    assert "max_tokens=768" in src
    assert "len(message) > 500" in src
    # The tool loop is a bounded backstop, not an open loop.
    assert "for _ in range(3)" in src


def test_one_request_can_cost_more_than_one_api_call(app):
    """Documented rather than changed, because the bound is deliberate.

    The rate limit counts REQUESTS. A weather question runs the tool loop, so a
    single request can be up to three calls to Anthropic — the daily cap is
    "10 requests", not "10 calls", and a spend estimate that reads it as calls
    is out by up to 3x.
    """
    import inspect

    assert "for _ in range(3)" in inspect.getsource(appmod.coach)


def test_no_key_fails_closed_and_says_so(client, monkeypatch):
    monkeypatch.setattr(appmod, "_anthropic_api_key", "")
    token = register_and_login(client, "nokey")
    resp = client.post("/api/coach", json={"message": "hi", "context": {"type": "general"}},
                       headers=auth_headers(token))
    assert resp.status_code == 503
    assert resp.get_json()["error"] == "coach_unavailable"


def test_an_upstream_failure_is_logged_without_the_message(client, monkeypatch, caplog):
    """The failure path has to leave a trace — and the trace must not be the
    person's sentence."""
    from test_coach import _install_fake_anthropic

    _install_fake_anthropic(monkeypatch)

    class Boom:
        def __init__(self, *a, **k):
            raise RuntimeError(SENSITIVE)

    monkeypatch.setattr(appmod._anthropic_lib, "Anthropic", Boom)
    token = register_and_login(client, "upstream_boom")
    with caplog.at_level("WARNING"):
        resp = client.post("/api/coach",
                           json={"message": SENSITIVE, "context": {"type": "general"}},
                           headers=auth_headers(token))
    assert resp.status_code == 503
    assert "coach_call_failed" in caplog.text, "an upstream failure left no trace at all"
    assert "RuntimeError" in caplog.text, "the trace does not say what went wrong"
    assert SENSITIVE not in caplog.text, "the failure log carried the message"


def test_an_over_long_message_is_refused_before_it_costs_anything(client):
    token = register_and_login(client, "toolong")
    resp = client.post("/api/coach",
                       json={"message": "x" * 501, "context": {"type": "general"}},
                       headers=auth_headers(token))
    assert resp.status_code == 400
    assert resp.get_json()["error"] == "message_too_long"
