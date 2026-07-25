"""Cross-session memory for Rickie: coach_turn (rolling 10-turn window) and
coach_note (small, deterministic, factual).

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

def test_extract_goal():
    facts = appmod._coach_note_extract("honestly my goal is to run a 5k this fall")
    assert facts["goals"] == ["run a 5k this fall"]


def test_extract_preference():
    facts = appmod._coach_note_extract("I prefer short morning workouts")
    assert facts["preferences"] == ["short morning workouts"]


def test_extract_note():
    facts = appmod._coach_note_extract("just so you know, I travel every other week")
    assert facts["notes"] == ["I travel every other week"]


def test_extract_ignores_non_facts():
    """Transient venting and ordinary chat must never be stored as facts."""
    for msg in ["I don't feel like it today", "ugh not feeling it",
                "how do streaks work?", "tell me a joke", "hey Rickie"]:
        facts = appmod._coach_note_extract(msg)
        assert not any(facts.values()), msg


def test_extract_takes_first_clause_only():
    facts = appmod._coach_note_extract("I prefer mornings. Also I hate burpees.")
    assert facts["preferences"] == ["mornings"]


# ── Coach Notes: merge, dedup, cap, and the 'nothing factual' no-op ──────────

def test_note_update_merges_and_dedups(app):
    u = _make_user("notes_merge")
    appmod._update_coach_note(u.id, "my goal is to run a 5k")
    appmod._update_coach_note(u.id, "my goal is to run a 5k")   # duplicate ignored
    appmod._update_coach_note(u.id, "I prefer mornings")
    note = CoachNote.query.filter_by(user_id=u.id).first()
    assert json.loads(note.goals) == ["run a 5k"]
    assert json.loads(note.preferences) == ["mornings"]


def test_note_caps_at_five_most_recent(app):
    u = _make_user("notes_cap")
    for i in range(7):
        appmod._update_coach_note(u.id, f"I prefer option{i}")
    prefs = json.loads(CoachNote.query.filter_by(user_id=u.id).first().preferences)
    assert prefs == ["option2", "option3", "option4", "option5", "option6"]


def test_note_no_row_when_nothing_factual(app):
    u = _make_user("notes_none")
    appmod._update_coach_note(u.id, "I don't feel like it today")
    assert CoachNote.query.filter_by(user_id=u.id).first() is None


# ── Context block: format, non-recitation instruction, empty case ────────────

def test_note_block_format_and_non_recitation(app):
    u = _make_user("notes_block")
    appmod._update_coach_note(u.id, "my goal is to run a 5k")
    appmod._update_coach_note(u.id, "I prefer mornings")
    block = appmod._load_coach_note_block(u.id)
    assert "Goals: run a 5k" in block
    assert "Preferences: mornings" in block
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
    appmod._update_coach_note(a.id, "my goal is to climb everest")

    # B, a different user, sees none of A's turns or notes
    assert appmod._load_coach_messages(b.id) == []
    assert appmod._load_coach_note_block(b.id) == ""

    # A sees only A's own
    a_msgs = appmod._load_coach_messages(a.id)
    assert any("A's private message" in m["content"] for m in a_msgs)
    assert "everest" in appmod._load_coach_note_block(a.id)


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
    appmod._update_coach_note(_uid("inject_user"), "my goal is to run a 5k")
    resp = client.post("/api/coach", json={
        "message": "hey", "context": {"type": "general"},
    }, headers=auth_headers(token))
    assert resp.status_code == 200
    assert "Goals: run a 5k" in cap["system"]              # injected as background
    assert 'never say "I remember,"' in cap["system"]       # not to be recited


def test_coach_call_persists_exchange_and_extracts_note(client, monkeypatch):
    """End-to-end: a coach turn stores the exchange and folds an explicit fact
    into Coach Notes — all via deterministic server logic, not the model."""
    _install_fake_anthropic(monkeypatch)
    token = register_and_login(client, "persist_user")
    uid = _uid("persist_user")
    resp = client.post("/api/coach", json={
        "message": "my goal is to run a 5k", "context": {"type": "general"},
    }, headers=auth_headers(token))
    assert resp.status_code == 200
    assert CoachTurn.query.filter_by(user_id=uid).count() == 2   # user + assistant
    assert json.loads(CoachNote.query.filter_by(user_id=uid).first().goals) == ["run a 5k"]


# ── Extraction across sentences + history loader shape ───────────────────────

def test_extract_multiple_categories_across_sentences():
    facts = appmod._coach_note_extract("My goal is to run a 5k. I prefer mornings.")
    assert facts["goals"] == ["run a 5k"]         # stops at the sentence boundary
    assert facts["preferences"] == ["mornings"]


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
    db.session.add(CoachNote(user_id=u.id, goals='[]', preferences='[]', notes='[]'))
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


def test_get_or_create_returns_existing_without_savepoint(app):
    u = _make_user("existing_note")
    db.session.add(CoachNote(user_id=u.id, goals='["x"]', preferences='[]', notes='[]'))
    db.session.commit()
    note = appmod._get_or_create_coach_note(u.id)
    assert json.loads(note.goals) == ["x"]
    assert CoachNote.query.filter_by(user_id=u.id).count() == 1


# ── WS1: atomic coach persistence ────────────────────────────────────────────

def test_persist_interaction_atomic_success(app):
    u = _make_user("atomic_ok")
    appmod._persist_coach_interaction(u.id, "my goal is to run a 5k", "nice, look at you")
    assert CoachTurn.query.filter_by(user_id=u.id).count() == 2
    assert json.loads(CoachNote.query.filter_by(user_id=u.id).first().goals) == ["run a 5k"]


def test_persist_rolls_back_turns_on_note_failure(app, monkeypatch):
    u = _make_user("atomic_fail")
    monkeypatch.setattr(appmod, "_stage_coach_note",
                        lambda uid, facts: (_ for _ in ()).throw(RuntimeError("note write failed")))
    with pytest.raises(RuntimeError):
        appmod._persist_coach_interaction(u.id, "my goal is to run a 5k", "reply")
    # atomic: the turns were rolled back too — no partial state
    assert CoachTurn.query.filter_by(user_id=u.id).count() == 0
    assert CoachNote.query.filter_by(user_id=u.id).count() == 0


def test_persist_no_duplicate_turns_after_failed_attempt(app, monkeypatch):
    u = _make_user("nodup")
    monkeypatch.setattr(appmod, "_stage_coach_note",
                        lambda uid, facts: (_ for _ in ()).throw(RuntimeError("x")))
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
    appmod._update_coach_note(u.id, "my goal is to run a 5k")
    assert CoachTurn.query.filter_by(user_id=u.id).count() == 2
    assert json.loads(CoachNote.query.filter_by(user_id=u.id).first().goals) == ["run a 5k"]
