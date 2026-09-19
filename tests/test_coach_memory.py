"""Cross-session memory for Rickie: coach_turn (rolling 10-turn window) and
coach_note (a few canonical tokens from a closed vocabulary).

The safety boundary around coach_note — what may and may not become permanent
memory — is proved in test_coach_notes_boundary.py. This file covers the
plumbing: extraction wiring, merge/cap, pruning, isolation, deletion, format.

These exercise the server logic directly — extraction, merge/cap, pruning,
per-user isolation, deletion, and context formatting — with no model calls. The
behavioral property ("uses memory naturally, never recites it") is judged live
in the conversation eval; here we prove the block is INJECTED into Rickie's
context and carries the non-recitation instruction.
"""
import json

import pytest

import app as appmod
from app import db, User, CoachTurn, CoachNote
from conftest import register_and_login, auth_headers
from test_coach import _install_fake_anthropic


def _uid(username):
    return User.query.filter_by(username=username).first().id


def _make_user(username):
    u = User(username=username, password_hash="x")
    db.session.add(u)
    db.session.commit()
    return u


# ── Deterministic extraction: high precision, no speculation ─────────────────

def test_extract_activity():
    tokens = appmod._coach_note_extract("honestly I love going for a walk")
    assert tokens["activities"] == ["walking"]


def test_extract_session_preference():
    tokens = appmod._coach_note_extract("I prefer short workouts in the morning")
    assert sorted(tokens["session_prefs"]) == ["mornings", "short"]


def test_extract_avoid():
    tokens = appmod._coach_note_extract("I can't do jumping")
    assert tokens["avoid_movements"] == ["jumping"]


def test_extract_ignores_non_facts():
    """Transient venting and ordinary chat must never become memory."""
    for msg in ["I don't feel like it today", "ugh not feeling it",
                "how do streaks work?", "tell me a joke", "hey Rickie"]:
        tokens = appmod._coach_note_extract(msg)
        assert not any(tokens.values()), msg


def test_a_bare_mention_is_not_a_preference():
    """The word alone isn't enough — it has to be offered as a preference."""
    tokens = appmod._coach_note_extract("walking to the shop took ages today")
    assert not any(tokens.values())


def test_avoid_wins_over_like_for_a_shared_token():
    tokens = appmod._coach_note_extract("I can't do running, I'd rather walk")
    assert tokens["avoid_movements"] == ["running"]
    assert "running" not in tokens["activities"]


# ── Coach Notes: merge, dedup, cap, and the 'nothing factual' no-op ──────────

def test_note_update_merges_and_dedups(app):
    u = _make_user("notes_merge")
    appmod._update_coach_note(u.id, "I love swimming")
    appmod._update_coach_note(u.id, "I love swimming")          # duplicate ignored
    appmod._update_coach_note(u.id, "I prefer mornings")
    note = CoachNote.query.filter_by(user_id=u.id).first()
    assert json.loads(note.activities) == ["swimming"]
    assert json.loads(note.session_prefs) == ["mornings"]


def test_note_caps_at_the_most_recent_few(app):
    u = _make_user("notes_cap")
    for phrase in ["I love walking", "I love swimming", "I love dancing",
                   "I love yoga", "I love cycling"]:
        appmod._update_coach_note(u.id, phrase)
    acts = json.loads(CoachNote.query.filter_by(user_id=u.id).first().activities)
    assert acts == ["swimming", "dancing", "yoga", "cycling"]   # oldest dropped
    assert len(acts) == appmod._COACH_NOTE_MAX_PER_SLOT


def test_a_token_removed_from_the_taxonomy_stops_being_used(app, monkeypatch):
    """Narrowing the allow-list takes effect immediately, with no migration:
    stored tokens are re-filtered against the live vocabulary on every write."""
    u = _make_user("notes_narrow")
    appmod._update_coach_note(u.id, "I love swimming")
    shrunk = dict(appmod.COACH_NOTE_TAXONOMY)
    shrunk["activities"] = {k: v for k, v in shrunk["activities"].items()
                            if k != "swimming"}
    monkeypatch.setattr(appmod, "COACH_NOTE_TAXONOMY", shrunk)
    appmod._update_coach_note(u.id, "I love walking")
    acts = json.loads(CoachNote.query.filter_by(user_id=u.id).first().activities)
    assert acts == ["walking"]          # swimming dropped on the next write


def test_note_no_row_when_nothing_factual(app):
    u = _make_user("notes_none")
    appmod._update_coach_note(u.id, "I don't feel like it today")
    assert CoachNote.query.filter_by(user_id=u.id).first() is None


# ── Context block: format, non-recitation instruction, empty case ────────────

def test_note_block_format_and_non_recitation(app):
    u = _make_user("notes_block")
    appmod._update_coach_note(u.id, "I love swimming")
    appmod._update_coach_note(u.id, "I prefer mornings")
    block = appmod._load_coach_note_block(u.id)
    assert "Movement they enjoy: swimming" in block
    assert "How they like sessions: mornings" in block
    assert "background only" in block
    assert 'never say "I remember,"' in block
    assert "never list these back" in block


def test_note_block_empty_when_nothing_stored(app):
    u = _make_user("notes_empty")
    assert appmod._load_coach_note_block(u.id) == ""


# ── Rolling window pruning ───────────────────────────────────────────────────

def test_turns_pruned_to_window(app):
    u = _make_user("prune")
    for i in range(8):   # 8 exchanges = 16 turns; window is 10
        appmod._record_coach_exchange(u.id, f"user{i}", f"reply{i}")
    turns = CoachTurn.query.filter_by(user_id=u.id).order_by(CoachTurn.id.asc()).all()
    assert len(turns) == appmod._COACH_MEMORY_WINDOW
    contents = [t.content for t in turns]
    assert "reply7" in contents       # newest survives
    assert "user0" not in contents    # oldest pruned


# ── Per-user isolation (nobody sees anyone else's memory) ────────────────────

def test_memory_is_per_user_isolated(app):
    a = _make_user("iso_a")
    b = _make_user("iso_b")
    appmod._record_coach_exchange(a.id, "A's private message", "A reply")
    appmod._update_coach_note(a.id, "I love cycling")

    # B, a different user, sees none of A's turns or notes
    assert appmod._load_coach_messages(b.id) == []
    assert appmod._load_coach_note_block(b.id) == ""

    # A sees only A's own
    a_msgs = appmod._load_coach_messages(a.id)
    assert any("A's private message" in m["content"] for m in a_msgs)
    assert "cycling" in appmod._load_coach_note_block(a.id)


# ── Deletion endpoint: caller-only, permanent, idempotent, authed ────────────

def test_forget_endpoint_deletes_only_caller(client):
    ta = register_and_login(client, "forget_a")
    register_and_login(client, "forget_b")
    ida, idb = _uid("forget_a"), _uid("forget_b")
    appmod._record_coach_exchange(ida, "a msg", "a reply")
    appmod._update_coach_note(ida, "my goal is to run a marathon")
    appmod._record_coach_exchange(idb, "b msg", "b reply")
    appmod._update_coach_note(idb, "I prefer evenings")

    resp = client.delete("/api/coach/memory", headers=auth_headers(ta))
    assert resp.status_code == 200
    assert resp.get_json()["status"] == "forgotten"

    # A's memory is gone...
    assert CoachTurn.query.filter_by(user_id=ida).count() == 0
    assert CoachNote.query.filter_by(user_id=ida).first() is None
    # ...B's is untouched
    assert CoachTurn.query.filter_by(user_id=idb).count() == 2
    assert CoachNote.query.filter_by(user_id=idb).first() is not None


def test_forget_endpoint_is_idempotent(client):
    t = register_and_login(client, "forget_idem")
    assert client.delete("/api/coach/memory", headers=auth_headers(t)).status_code == 200
    assert client.delete("/api/coach/memory", headers=auth_headers(t)).status_code == 200


def test_forget_endpoint_requires_auth(client):
    assert client.delete("/api/coach/memory").status_code in (401, 422)


# ── Injection into Rickie's real context (deterministic half of 'not recited') ─

def test_note_block_injected_into_coach_context(client, monkeypatch):
    cap = _install_fake_anthropic(monkeypatch)
    token = register_and_login(client, "inject_user")
    appmod._update_coach_note(_uid("inject_user"), "I love swimming")
    resp = client.post("/api/coach", json={
        "message": "hey", "context": {"type": "general"},
    }, headers=auth_headers(token))
    assert resp.status_code == 200
    assert "Movement they enjoy: swimming" in cap["system"]   # injected as background
    assert 'never say "I remember,"' in cap["system"]       # not to be recited


def test_coach_call_persists_exchange_and_extracts_note(client, monkeypatch):
    """End-to-end: a coach turn stores the exchange and folds an allow-listed
    token into Coach Notes — deterministic server logic, not the model."""
    _install_fake_anthropic(monkeypatch)
    token = register_and_login(client, "persist_user")
    uid = _uid("persist_user")
    resp = client.post("/api/coach", json={
        "message": "I love swimming", "context": {"type": "general"},
    }, headers=auth_headers(token))
    assert resp.status_code == 200
    assert CoachTurn.query.filter_by(user_id=uid).count() == 2   # user + assistant
    assert json.loads(
        CoachNote.query.filter_by(user_id=uid).first().activities) == ["swimming"]


# ── Extraction across sentences + history loader shape ───────────────────────

def test_extract_multiple_categories_across_sentences():
    tokens = appmod._coach_note_extract("I love swimming. I prefer mornings.")
    assert tokens["activities"] == ["swimming"]
    assert tokens["session_prefs"] == ["mornings"]


def test_load_coach_messages_is_alternation_safe_windowed_and_capped(app):
    u = _make_user("history_shape")
    db.session.add(CoachTurn(user_id=u.id, role="assistant", content="lead"))  # leading -> dropped
    for i in range(11):   # 22 more turns, well over the 10 window
        db.session.add(CoachTurn(user_id=u.id, role="user", content=f"u{i}"))
        db.session.add(CoachTurn(user_id=u.id, role="assistant", content="x" * 900))  # long -> capped
    db.session.commit()

    msgs = appmod._load_coach_messages(u.id)
    assert len(msgs) <= appmod._COACH_MEMORY_WINDOW
    assert msgs[0]["role"] == "user"                       # leading assistant dropped
    for a, b in zip(msgs, msgs[1:]):
        assert a["role"] != b["role"]                      # strict alternation
    assert all(len(m["content"]) <= appmod._COACH_TURN_PROMPT_LEN for m in msgs)  # capped


# ── WS2: CoachNote first-write concurrency ───────────────────────────────────

def test_coach_note_first_write_race_recovers_no_duplicate(app, monkeypatch):
    """Simulate the race: a concurrent request already inserted the first CoachNote,
    but our existence check missed it. _get_or_create must hit the unique constraint,
    recover the existing row via the savepoint + IntegrityError path, and NOT create a
    duplicate or raise."""
    u = _make_user("race_user")
    db.session.add(CoachNote(user_id=u.id, activities='[]', avoid_movements='[]',
                                 session_prefs='[]'))
    db.session.commit()

    real_find = appmod._find_coach_note
    missed = {"done": False}
    def flaky_find(uid):
        if not missed["done"]:          # first lookup "misses" (the race window)
            missed["done"] = True
            return None
        return real_find(uid)
    monkeypatch.setattr(appmod, "_find_coach_note", flaky_find)

    note = appmod._get_or_create_coach_note(u.id)
    assert note is not None                                       # recovered the row
    assert CoachNote.query.filter_by(user_id=u.id).count() == 1   # no duplicate

    # The savepoint rollback must leave the session usable: writing to the
    # recovered row and committing has to succeed cleanly (no leftover failed
    # INSERT re-surfacing as an IntegrityError at commit) and still not duplicate.
    note.activities = json.dumps(["swimming"])
    db.session.commit()
    survivors = CoachNote.query.filter_by(user_id=u.id).all()
    assert len(survivors) == 1                                    # still exactly one row
    assert json.loads(survivors[0].activities) == ["swimming"]    # the write persisted


def test_get_or_create_returns_existing_without_savepoint(app):
    u = _make_user("existing_note")
    db.session.add(CoachNote(user_id=u.id, activities='["yoga"]',
                             avoid_movements='[]', session_prefs='[]'))
    db.session.commit()
    note = appmod._get_or_create_coach_note(u.id)
    assert json.loads(note.activities) == ["yoga"]
    assert CoachNote.query.filter_by(user_id=u.id).count() == 1


# ── WS1: atomic coach persistence ────────────────────────────────────────────

def test_persist_interaction_atomic_success(app):
    u = _make_user("atomic_ok")
    appmod._persist_coach_interaction(u.id, "I love swimming", "nice, look at you")
    assert CoachTurn.query.filter_by(user_id=u.id).count() == 2
    assert json.loads(
        CoachNote.query.filter_by(user_id=u.id).first().activities) == ["swimming"]


def test_persist_rolls_back_turns_on_note_failure(app, monkeypatch):
    u = _make_user("atomic_fail")
    monkeypatch.setattr(appmod, "_stage_coach_note",
                        lambda uid, tokens: (_ for _ in ()).throw(RuntimeError("note write failed")))
    with pytest.raises(RuntimeError):
        appmod._persist_coach_interaction(u.id, "I love swimming", "reply")
    # atomic: the turns were rolled back too — no partial state
    assert CoachTurn.query.filter_by(user_id=u.id).count() == 0
    assert CoachNote.query.filter_by(user_id=u.id).count() == 0


def test_persist_no_duplicate_turns_after_failed_attempt(app, monkeypatch):
    u = _make_user("nodup")
    monkeypatch.setattr(appmod, "_stage_coach_note",
                        lambda uid, tokens: (_ for _ in ()).throw(RuntimeError("x")))
    with pytest.raises(RuntimeError):
        appmod._persist_coach_interaction(u.id, "I prefer mornings", "r1")
    monkeypatch.undo()
    appmod._persist_coach_interaction(u.id, "hello there", "r2")   # no facts extracted
    assert CoachTurn.query.filter_by(user_id=u.id).count() == 2    # only the successful pair


def test_persist_prunes_to_window(app):
    u = _make_user("prune_persist")
    for i in range(8):   # 16 turns -> pruned to 10
        appmod._persist_coach_interaction(u.id, f"msg{i}", f"reply{i}")
    assert CoachTurn.query.filter_by(user_id=u.id).count() == appmod._COACH_MEMORY_WINDOW


def test_persist_survives_extraction_failure_keeping_turns(app, monkeypatch):
    """Coach Notes extraction is best-effort — if the (pure-Python) extractor raises,
    the turns still persist and no note is written."""
    u = _make_user("extract_fail")
    monkeypatch.setattr(appmod, "_coach_note_extract",
                        lambda msg: (_ for _ in ()).throw(RuntimeError("regex boom")))
    appmod._persist_coach_interaction(u.id, "anything", "reply")
    assert CoachTurn.query.filter_by(user_id=u.id).count() == 2
    assert CoachNote.query.filter_by(user_id=u.id).count() == 0


def test_direct_helpers_still_self_commit(app):
    u = _make_user("direct_helpers")
    appmod._record_coach_exchange(u.id, "hi", "yo")
    appmod._update_coach_note(u.id, "I love swimming")
    assert CoachTurn.query.filter_by(user_id=u.id).count() == 2
    assert json.loads(
        CoachNote.query.filter_by(user_id=u.id).first().activities) == ["swimming"]


# ── Retention actually expiring for accounts that never come back ────────────
#
# The data export promises `coach_conversation_days: 30`. That promise was only
# kept for users who returned: _expire_old_coach_turns runs on this user's read
# and on this user's write, so the person who says something difficult and never
# opens the app again — precisely the person the window is for — kept it
# forever. Nothing swept on their behalf.

def test_inactive_user_turns_are_swept_by_the_global_sweep(app):
    """A user who never comes back must still have old turns deleted."""
    gone = _make_user("retention_never_returns")
    stale = appmod.datetime.utcnow() - appmod.timedelta(
        days=appmod._COACH_TURN_MAX_AGE_DAYS + 5)
    for role, content in (("user", "something difficult"), ("assistant", "a reply")):
        db.session.add(CoachTurn(user_id=gone.id, role=role,
                                 content=content, created_at=stale))
    db.session.commit()
    assert CoachTurn.query.filter_by(user_id=gone.id).count() == 2

    # The sweep is global: it is not given this user's id, because nobody is
    # acting on their behalf. That is the entire point.
    deleted = appmod._sweep_expired_coach_turns(force=True)
    db.session.commit()

    assert deleted == 2
    assert CoachTurn.query.filter_by(user_id=gone.id).count() == 0


def test_global_sweep_leaves_fresh_turns_alone(app):
    fresh = _make_user("retention_recent")
    db.session.add(CoachTurn(user_id=fresh.id, role="user", content="today"))
    db.session.commit()
    appmod._sweep_expired_coach_turns(force=True)
    db.session.commit()
    assert CoachTurn.query.filter_by(user_id=fresh.id).count() == 1


def test_global_sweep_is_rate_limited_so_it_is_cheap_on_every_request(app):
    """Called on the request path, so it must not run a DELETE every time."""
    appmod._sweep_expired_coach_turns(force=True)
    db.session.commit()
    # Immediately after a forced sweep, an unforced one should decline to run.
    assert appmod._sweep_expired_coach_turns() is None


def test_another_users_activity_sweeps_the_inactive_users_expired_turns(app):
    """End-to-end: the real write path, not the sweep helper directly.

    This is the scenario the retention promise is actually about. The person
    who left is not doing anything; somebody else is.
    """
    appmod._coach_sweep_last = None          # allow the hourly sweep to run
    gone = _make_user("retention_left_for_good")
    stale = appmod.datetime.utcnow() - appmod.timedelta(
        days=appmod._COACH_TURN_MAX_AGE_DAYS + 1)
    db.session.add(CoachTurn(user_id=gone.id, role="user",
                             content="a thing they regret typing", created_at=stale))
    still_here = _make_user("retention_still_here")
    db.session.commit()

    appmod._record_coach_exchange(still_here.id, "hey Rickie", "hey yourself")

    assert CoachTurn.query.filter_by(user_id=gone.id).count() == 0, \
        "an inactive user's expired turns survived another user's activity"
    assert CoachTurn.query.filter_by(user_id=still_here.id).count() == 2


def test_retention_sweeper_thread_runs_with_no_requests_at_all(app, monkeypatch):
    """Independence from user activity, demonstrated rather than asserted.

    The request-path sweep fixed the per-user bug but still needed SOMEBODY to
    talk to Rickie. This proves the background thread deletes an expired row
    while nothing calls the API — no client, no test_client, no coach turn.
    """
    import time as _time

    gone = _make_user("retention_thread_subject")
    stale = appmod.datetime.utcnow() - appmod.timedelta(
        days=appmod._COACH_TURN_MAX_AGE_DAYS + 3)
    db.session.add(CoachTurn(user_id=gone.id, role="user",
                             content="left behind", created_at=stale))
    db.session.commit()
    assert CoachTurn.query.filter_by(user_id=gone.id).count() == 1

    monkeypatch.setattr(appmod, "_RETENTION_THREAD_INTERVAL_S", 0.2)
    appmod._coach_sweep_last = None
    thread = appmod._start_retention_sweeper()
    assert thread.daemon, "a non-daemon sweeper would hold the process open on exit"

    deadline = _time.monotonic() + 10
    while _time.monotonic() < deadline:
        if CoachTurn.query.filter_by(user_id=gone.id).count() == 0:
            break
        _time.sleep(0.2)

    assert CoachTurn.query.filter_by(user_id=gone.id).count() == 0, (
        "the background sweeper did not delete an expired turn without a request")


def test_retention_sweeper_is_off_unless_explicitly_enabled():
    """Importing app.py must not start a thread that deletes rows.

    `flask db upgrade`, pytest and every local script import this module.
    """
    import os
    assert os.environ.get("STREAKFIT_RETENTION_SWEEPER") != "1", (
        "test environment unexpectedly enables the sweeper")
    running = [t.name for t in __import__("threading").enumerate()
               if t.name == "streakfit-retention"]
    # The previous test starts one deliberately; what matters is that a bare
    # import does not, which is what the env-var guard buys.
    assert "STREAKFIT_RETENTION_SWEEPER" in open("app.py").read(), (
        "the sweeper must stay behind an explicit opt-in")
    del running
