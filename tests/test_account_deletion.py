"""Tests for the WS5 account-deletion service (app.delete_user_account).

Covers no-deps users, users with progress + coach data, ordinary team members,
team-owner blocking, shared-data preservation (SET NULL), dry-run, rollback, and
idempotency.
"""
import datetime

import pytest

import app as appmod
from app import (db, User, Team, TeamMembership, TeamMessage, TeamMoment,
                 CoachTurn, CoachNote, DailyCompletion, ProgressEvent, Challenge)


def _mk(name):
    u = User(username=name, password_hash="x")
    db.session.add(u)
    db.session.commit()
    return u


def test_delete_user_no_dependencies(app):
    u = _mk("solo")
    rep = appmod.delete_user_account(u.id, dry_run=False)
    assert rep["executed"] and not rep["blocked"]
    assert db.session.get(User, u.id) is None


def test_delete_user_with_progress_and_coach_data(app):
    u = _mk("busy")
    db.session.add_all([
        Challenge(title="c", user_id=u.id),
        DailyCompletion(user_id=u.id, date=datetime.date(2026, 7, 24), exercise_key="pushups"),
        ProgressEvent(user_id=u.id, event_type="mission_complete", xp_delta=25),
        CoachTurn(user_id=u.id, role="user", content="hi"),
        CoachNote(user_id=u.id, goals='["x"]', preferences='[]', notes='[]'),
    ])
    db.session.commit()
    rep = appmod.delete_user_account(u.id, dry_run=False)
    assert rep["executed"]
    assert db.session.get(User, u.id) is None
    for model in (Challenge, DailyCompletion, ProgressEvent, CoachTurn, CoachNote):
        assert model.query.filter_by(user_id=u.id).count() == 0


def test_delete_ordinary_team_member_leaves_team_intact(app):
    owner = _mk("owner_a")
    member = _mk("member_a")
    t = Team(name="T", created_by_user_id=owner.id)
    db.session.add(t)
    db.session.commit()
    db.session.add_all([TeamMembership(team_id=t.id, user_id=owner.id),
                        TeamMembership(team_id=t.id, user_id=member.id)])
    db.session.commit()

    rep = appmod.delete_user_account(member.id, dry_run=False)
    assert rep["executed"]
    assert db.session.get(User, member.id) is None
    assert db.session.get(Team, t.id) is not None                      # team preserved
    assert TeamMembership.query.filter_by(team_id=t.id).count() == 1   # owner remains


def test_team_owner_with_members_is_blocked(app):
    owner = _mk("owner_b")
    member = _mk("member_b")
    t = Team(name="T2", created_by_user_id=owner.id)
    db.session.add(t)
    db.session.commit()
    db.session.add_all([TeamMembership(team_id=t.id, user_id=owner.id),
                        TeamMembership(team_id=t.id, user_id=member.id)])
    db.session.commit()

    rep = appmod.delete_user_account(owner.id, dry_run=False)
    assert rep["blocked"] and not rep["executed"]
    assert "team" in rep["blockers"][0]
    assert db.session.get(User, owner.id) is not None                  # not deleted


def test_team_owner_with_no_other_members_still_blocked_in_ws5(app):
    owner = _mk("owner_c")
    t = Team(name="T3", created_by_user_id=owner.id)
    db.session.add(t)
    db.session.commit()
    db.session.add(TeamMembership(team_id=t.id, user_id=owner.id))
    db.session.commit()
    # WS5 blocks all owners; QA-only-team teardown is WS6.
    rep = appmod.delete_user_account(owner.id, dry_run=False)
    assert rep["blocked"]


def test_authored_messages_preserved_and_anonymized(app):
    owner = _mk("owner_d")
    author = _mk("author_d")
    t = Team(name="T4", created_by_user_id=owner.id)
    db.session.add(t)
    db.session.commit()
    db.session.add(TeamMembership(team_id=t.id, user_id=author.id))
    msg = TeamMessage(team_id=t.id, sender_type="user", sender_user_id=author.id, body="hello team")
    db.session.add(msg)
    db.session.commit()
    msg_id = msg.id

    appmod.delete_user_account(author.id, dry_run=False)
    m = db.session.get(TeamMessage, msg_id)
    assert m is not None and m.body == "hello team"   # message survives
    assert m.sender_user_id is None                   # author link nulled


def test_subject_moments_preserved_and_anonymized(app):
    owner = _mk("owner_e")
    subj = _mk("subject_e")
    t = Team(name="T5", created_by_user_id=owner.id)
    db.session.add(t)
    db.session.commit()
    db.session.add(TeamMembership(team_id=t.id, user_id=subj.id))
    mom = TeamMoment(team_id=t.id, moment_type="member_joined", subject_user_id=subj.id)
    db.session.add(mom)
    db.session.commit()
    mom_id = mom.id

    appmod.delete_user_account(subj.id, dry_run=False)
    m = db.session.get(TeamMoment, mom_id)
    assert m is not None and m.subject_user_id is None   # moment survives, subject nulled


def test_dry_run_changes_nothing(app):
    u = _mk("dry")
    db.session.add(CoachTurn(user_id=u.id, role="user", content="hi"))
    db.session.commit()
    rep = appmod.delete_user_account(u.id, dry_run=True)
    assert not rep["executed"] and not rep["blocked"]
    assert rep["counts"]["coach_turn"] == 1
    assert db.session.get(User, u.id) is not None
    assert CoachTurn.query.filter_by(user_id=u.id).count() == 1


def test_rollback_on_injected_failure(app, monkeypatch):
    u = _mk("rb")
    db.session.add(CoachTurn(user_id=u.id, role="user", content="hi"))
    db.session.commit()
    orig_delete = db.session.delete
    def boom(obj):
        if isinstance(obj, User):
            raise RuntimeError("delete blew up")
        return orig_delete(obj)
    monkeypatch.setattr(db.session, "delete", boom)
    with pytest.raises(RuntimeError):
        appmod.delete_user_account(u.id, dry_run=False)
    # atomic: nothing was committed — user and their private data survive
    assert db.session.get(User, u.id) is not None
    assert CoachTurn.query.filter_by(user_id=u.id).count() == 1


def test_deletion_is_idempotent(app):
    u = _mk("idem")
    r1 = appmod.delete_user_account(u.id, dry_run=False)
    assert r1["executed"]
    r2 = appmod.delete_user_account(u.id, dry_run=False)
    assert r2["found"] is False and not r2["executed"]   # nothing left to delete
