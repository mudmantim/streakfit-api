"""Tests for scripts/cleanup_qa_smoke.py — the smoke-account cleanup tool.

Exercises the classification/matching logic and the safe-only execute path
against a seeded database, so the destructive tool is covered rather than only
manually validated.
"""
import scripts.cleanup_qa_smoke as C
from app import db, User, CoachTurn, CoachNote, Team


def _mk(name):
    u = User(username=name, password_hash="x")
    db.session.add(u)
    db.session.commit()
    return u


def test_matches_exact_prefix_only(app):
    _mk("real_alice")
    _mk("qa_smokeXYZ")       # no underscore after 'smoke' -> not a match
    _mk("qaXsmokeY_1_z")     # wrong prefix -> not a match
    _mk("qa_smoke_1_a")
    _mk("qa_smoke_verify_check")   # non-epoch name still matches
    matched = {u.username for u in C._matches()}
    assert matched == {"qa_smoke_1_a", "qa_smoke_verify_check"}


def test_survey_splits_safe_and_blocked(app):
    _mk("qa_smoke_1_a")
    owner = _mk("qa_smoke_owner_x")
    db.session.add(Team(name="T", created_by_user_id=owner.id))
    db.session.commit()
    safe, blocked, counts = C.survey()
    assert {u.username for u in safe} == {"qa_smoke_1_a"}
    assert [u.username for u, _r in blocked] == ["qa_smoke_owner_x"]
    assert counts[owner.id]["team_owned"] == 1


def test_survey_counts_dependents(app):
    s = _mk("qa_smoke_dep_a")
    db.session.add_all([
        CoachTurn(user_id=s.id, role="user", content="hi"),
        CoachTurn(user_id=s.id, role="assistant", content="yo"),
        CoachNote(user_id=s.id, goals='["x"]', preferences='[]', notes='[]'),
    ])
    db.session.commit()
    _safe, _blocked, counts = C.survey()
    assert counts[s.id]["coach_turn"] == 2
    assert counts[s.id]["coach_note"] == 1


def test_execute_deletes_safe_only(app):
    _mk("real_bob")
    safe_u = _mk("qa_smoke_del_a")
    owner = _mk("qa_smoke_owner_y")
    db.session.add(Team(name="T2", created_by_user_id=owner.id))
    db.session.add(CoachTurn(user_id=safe_u.id, role="user", content="hi"))
    db.session.commit()

    safe, _blocked, counts = C.survey()
    C.execute(safe, counts)

    left = {u.username for u in User.query.all()}
    assert left == {"real_bob", "qa_smoke_owner_y"}   # safe deleted; real + team-owner remain
    assert CoachTurn.query.count() == 0               # its dependents went too


def test_sanity_cap_aborts(app, monkeypatch):
    monkeypatch.setattr(C, "SANITY_CAP", 1)
    _mk("qa_smoke_a_1")
    _mk("qa_smoke_a_2")
    assert C.survey() is None   # over the cap -> hard-abort signal, no classification
