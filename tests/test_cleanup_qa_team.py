"""delete_qa_smoke_team and cleanup_qa_smoke --team-owner-policy=delete-qa-team.

verify_all leaves one "Smoke Test" team per production run, and its qa_smoke_
owner cannot be deleted while the team exists. These tests pin what the guarded
team deletion may and may not do. SQLite enforces foreign keys here (it does
not by default), so a row left pointing at a deleted team fails the test the
way it would fail on PostgreSQL.
"""
import sys
import uuid

import pytest
from sqlalchemy import event

import app as A
import scripts.cleanup_qa_smoke as C
from app import db


@pytest.fixture()
def fk_app(app):
    def _on(dbapi_conn, _record):
        dbapi_conn.execute('PRAGMA foreign_keys=ON')
    event.listen(db.engine, 'connect', _on)
    db.session.remove()
    db.engine.dispose()
    try:
        assert db.session.execute(db.text('PRAGMA foreign_keys')).scalar() == 1
        yield app
    finally:
        db.session.remove()
        event.remove(db.engine, 'connect', _on)
        db.engine.dispose()


def _user(name):
    u = A.User(username=name, password_hash="x")
    db.session.add(u)
    db.session.commit()
    return u


def _hex():
    return uuid.uuid4().hex


def _qa_team(owner, *members, name="Smoke Test run-1", report_status="closed", hold=False):
    """A team shaped like verify_all's: every kind of row that points at it."""
    t = A.Team(name=name, created_by_user_id=owner.id)
    db.session.add(t)
    db.session.flush()
    people = (owner,) + members
    for p in people:
        db.session.add(A.TeamMembership(team_id=t.id, user_id=p.id))
    db.session.add(A.TeamInviteCode(team_id=t.id, code=_hex()[:8]))
    db.session.add(A.TeamCampfire(team_id=t.id))
    photo = A.TeamPhoto(public_id=_hex(), team_id=t.id, sender_user_id=owner.id,
                        caption="cap", image_data=b"px", byte_size=2)
    ch = A.TeamChallenge(public_id=_hex(), team_id=t.id, created_by_user_id=owner.id,
                         target_user_id=people[-1].id, preset_key="squats_10")
    db.session.add_all([photo, ch])
    db.session.flush()
    db.session.add_all([
        A.TeamMessage(team_id=t.id, sender_type="user", sender_user_id=owner.id, body="hi"),
        A.TeamMessage(team_id=t.id, sender_type="user", sender_user_id=owner.id, body="",
                      photo_id=photo.id),
        A.TeamMessage(team_id=t.id, sender_type="user", sender_user_id=owner.id, body="",
                      challenge_id=ch.id),
        A.TeamMessage(team_id=t.id, sender_type="rickie", body="welcome"),
        A.TeamMoment(team_id=t.id, moment_type="member_joined", subject_user_id=people[-1].id),
        A.TeamChallengeCompletion(challenge_id=ch.id, user_id=people[-1].id),
    ])
    rep = A.Report(public_id=_hex(), reporter_user_id=owner.id,
                   reported_user_id=people[-1].id, team_id=t.id, category="harassment",
                   subject_type="message", status=report_status, legal_hold=hold)
    db.session.add(rep)
    db.session.flush()
    act = A.ModerationAction(report_id=rep.id, action="dismiss",
                             target_user_id=people[-1].id, team_id=t.id)
    db.session.add_all([act, A.ReportEvidence(report_id=rep.id, content_type="message",
                                              content_text="hi", author_user_id=owner.id)])
    db.session.commit()
    return t, rep


def _row_counts():
    db.session.expire_all()
    return {tbl.name: db.session.execute(db.select(db.func.count()).select_from(tbl)).scalar()
            for tbl in db.metadata.sorted_tables}


def _run(monkeypatch, *argv):
    monkeypatch.setattr(sys, "argv", ["cleanup_qa_smoke.py", *argv])
    C.main()
    db.session.expire_all()


# -- the service ------------------------------------------------------------

def test_every_foreign_key_into_team_has_exactly_one_rule():
    fks = sorted((t.name, c.name) for t in db.metadata.sorted_tables for c in t.columns
                 if any(fk.column.table.name == 'team' for fk in c.foreign_keys))
    assert sorted(A._team_fk_classification()) == fks


def test_rows_pointing_at_team_content_are_deleted_before_it():
    """team_message -> team_photo/team_challenge and completion -> challenge."""
    order = [label for label, _m, _a in A._TEAM_ROW_DELETES]
    for tbl in db.metadata.sorted_tables:
        for c in tbl.columns:
            for fk in c.foreign_keys:
                if fk.column.table.name in ('team_photo', 'team_challenge'):
                    assert order.index(tbl.name) < order.index(fk.column.table.name), tbl.name


def test_a_nullified_team_link_is_actually_nullable():
    for _label, model, attr in A._TEAM_LINK_NULLS:
        assert getattr(model, attr).property.columns[0].nullable, (model, attr)


def test_an_all_qa_team_and_then_its_owner_are_deleted_cleanly(fk_app):
    owner, member = _user("qa_smoke_owner_1"), _user("qa_smoke_member_1")
    real = _user("real_person")
    t, rep = _qa_team(owner, member)
    other_team = A.Team(name="Real family", created_by_user_id=real.id)
    db.session.add(other_team)
    db.session.commit()

    plan = A.delete_qa_smoke_team(t.id, dry_run=True)
    assert not plan["refused"] and plan["linked_people"] == 2
    assert plan["counts"]["team_message"] == 4 and plan["counts"]["report_team"] == 1

    out = A.delete_qa_smoke_team(t.id, dry_run=False)
    assert out["executed"]
    db.session.expire_all()
    assert db.session.get(A.Team, t.id) is None
    for _label, model, attr in A._TEAM_ROW_DELETES:
        if attr:
            assert model.query.filter(getattr(model, attr) == t.id).count() == 0
    assert A.TeamChallengeCompletion.query.count() == 0
    # The audit record survives, with only the team link cut.
    kept = db.session.get(A.Report, rep.id)
    assert kept is not None and kept.team_id is None and kept.status == "closed"
    assert A.ModerationAction.query.filter_by(report_id=rep.id).one().team_id is None
    assert A.ReportEvidence.query.filter_by(report_id=rep.id).count() == 1
    assert db.session.execute(db.text("PRAGMA foreign_key_check")).all() == []
    # Nothing else touched.
    assert db.session.get(A.Team, other_team.id) is not None
    assert {u.username for u in A.User.query} == {"qa_smoke_owner_1", "qa_smoke_member_1",
                                                  "real_person"}

    # The owner is now an ordinary deletion.
    acct = A.delete_user_account(owner.id, dry_run=False)
    assert acct["executed"], acct
    db.session.expire_all()
    assert db.session.get(A.User, owner.id) is None
    assert db.session.execute(db.text("PRAGMA foreign_key_check")).all() == []


@pytest.mark.parametrize("where", ["member", "message", "moment", "completion",
                                   "report", "evidence", "appeal"])
def test_a_team_with_any_real_person_linked_is_refused(fk_app, where):
    owner, member = _user("qa_smoke_owner_2"), _user("qa_smoke_member_2")
    real = _user("real_alice")
    t, rep = _qa_team(owner, member)
    ch = A.TeamChallenge.query.filter_by(team_id=t.id).first()
    act = A.ModerationAction.query.filter_by(team_id=t.id).first()
    db.session.add({
        "member": lambda: A.TeamMembership(team_id=t.id, user_id=real.id),
        "message": lambda: A.TeamMessage(team_id=t.id, sender_type="user",
                                         sender_user_id=real.id, body="x"),
        "moment": lambda: A.TeamMoment(team_id=t.id, moment_type="x", subject_user_id=real.id),
        "completion": lambda: A.TeamChallengeCompletion(challenge_id=ch.id, user_id=real.id),
        "report": lambda: A.Report(public_id=_hex(), reported_user_id=real.id, team_id=t.id,
                                   category="other", subject_type="user", status="closed"),
        "evidence": lambda: A.ReportEvidence(report_id=rep.id, content_type="message",
                                             author_user_id=real.id),
        "appeal": lambda: A.Appeal(public_id=_hex(), user_id=real.id, action_id=act.id),
    }[where]())
    db.session.commit()
    before = _row_counts()

    for dry in (True, False):
        out = A.delete_qa_smoke_team(t.id, dry_run=dry)
        assert out["refused"] and not out["executed"]
        assert out["refusals"] == ["non_qa_smoke_person_linked"] and out["non_qa_people"] == 1
    assert _row_counts() == before


def test_a_real_owner_or_a_non_smoke_name_is_refused(fk_app):
    real = _user("real_owner")
    t, _rep = _qa_team(real, _user("qa_smoke_m_3"))
    assert "owner_not_qa_smoke" in A.delete_qa_smoke_team(t.id, dry_run=False)["refusals"]
    t2, _rep2 = _qa_team(_user("qa_smoke_o_3"), name="Family")
    assert A.delete_qa_smoke_team(t2.id, dry_run=False)["refusals"] == ["team_name_not_smoke_test"]
    # The prefix is exact: 'qa_smokeX' and a SQL-LIKE-style lookalike do not count.
    t3, _rep3 = _qa_team(_user("qa_smoke_o_4"), _user("qa-smoke_x"))
    assert "non_qa_smoke_person_linked" in A.delete_qa_smoke_team(t3.id)["refusals"]
    db.session.expire_all()
    assert A.Team.query.count() == 3


@pytest.mark.parametrize("status,hold", [("pending", False), ("closed", True)])
def test_a_team_with_a_pending_or_held_report_is_refused(fk_app, status, hold):
    t, rep = _qa_team(_user("qa_smoke_owner_5"), _user("qa_smoke_member_5"),
                      report_status=status, hold=hold)
    before = _row_counts()
    out = A.delete_qa_smoke_team(t.id, dry_run=False)
    assert out["refusals"] == ["report_pending_or_held"] and out["open_or_held_reports"] == 1
    assert _row_counts() == before
    assert db.session.get(A.Report, rep.id).team_id == t.id


def test_a_missing_team_is_refused(app):
    assert A.delete_qa_smoke_team(999999, dry_run=False)["refusals"] == ["team_not_found"]


def test_a_failure_part_way_rolls_the_whole_team_back(fk_app, monkeypatch):
    t, _rep = _qa_team(_user("qa_smoke_owner_6"), _user("qa_smoke_member_6"))
    before = _row_counts()

    real_rows = A._qa_team_rows

    def rows(model, attr, team_id):
        if model is A.TeamCampfire:
            raise RuntimeError("boom")
        return real_rows(model, attr, team_id)
    monkeypatch.setattr(A, "_qa_team_rows", rows)
    with pytest.raises(RuntimeError):
        A.delete_qa_smoke_team(t.id, dry_run=False)
    assert _row_counts() == before


# -- the script -------------------------------------------------------------

def test_policy_dry_run_changes_nothing(fk_app, monkeypatch, capsys):
    t, _rep = _qa_team(_user("qa_smoke_owner_7"), _user("qa_smoke_member_7"))
    before = _row_counts()
    _run(monkeypatch, "--team-owner-policy=delete-qa-team")
    out = capsys.readouterr().out
    assert _row_counts() == before
    assert "1 owner(s), 1 QA-only team(s) eligible" in out
    assert f"team={t.id}" in out and "08920334bccd" in out


def test_policy_execute_deletes_qa_teams_and_owners_only(fk_app, monkeypatch, capsys):
    real = _user("real_bob")
    ok_owner, member = _user("qa_smoke_owner_8"), _user("qa_smoke_member_8")
    ok_team, _r = _qa_team(ok_owner, member)
    mixed_owner = _user("qa_smoke_owner_9")
    mixed_team, _r2 = _qa_team(mixed_owner, real)                       # real member
    pend_owner = _user("qa_smoke_owner_10")
    pend_team, _r3 = _qa_team(pend_owner, report_status="pending")      # pending report
    real_team = A.Team(name="Smoke Test lookalike", created_by_user_id=real.id)
    db.session.add(real_team)
    db.session.commit()
    ok_id, mixed_id, pend_id, real_id = ok_team.id, mixed_team.id, pend_team.id, real_team.id
    mixed_owner_id, pend_owner_id = mixed_owner.id, pend_owner.id

    _run(monkeypatch, "--team-owner-policy=delete-qa-team", "--execute")
    out = capsys.readouterr().out

    assert db.session.get(A.Team, ok_id) is None
    assert {t.id for t in A.Team.query} == {mixed_id, pend_id, real_id}
    users = {u.username for u in A.User.query}
    # member_8 was SAFE (membership only) and went in the existing safe phase.
    assert users == {"real_bob", "qa_smoke_owner_9", "qa_smoke_owner_10"}
    assert "DELETED 1 QA-only team(s)" in out and "1 owner(s) deleted" in out
    assert f"REFUSED id={mixed_owner_id}" in out and f"REFUSED id={pend_owner_id}" in out
    assert "real_bob" not in out and "Smoke Test" not in out.split("TEAM-OWNER POLICY")[1]
    assert db.session.execute(db.text("PRAGMA foreign_key_check")).all() == []


def test_policy_respects_the_team_sanity_cap(fk_app, monkeypatch, capsys):
    monkeypatch.setattr(C, "TEAM_SANITY_CAP", 1)
    o = _user("qa_smoke_owner_11")
    _qa_team(o, name="Smoke Test a")
    _qa_team(o, name="Smoke Test b")
    before = _row_counts()
    with pytest.raises(SystemExit) as e:
        _run(monkeypatch, "--team-owner-policy=delete-qa-team", "--execute")
    assert e.value.code == 2
    assert _row_counts() == before


def test_policy_respects_the_account_sanity_cap(fk_app, monkeypatch):
    monkeypatch.setattr(C, "SANITY_CAP", 1)
    _qa_team(_user("qa_smoke_owner_12"), _user("qa_smoke_member_12"))
    before = _row_counts()
    with pytest.raises(SystemExit) as e:
        _run(monkeypatch, "--team-owner-policy=delete-qa-team", "--execute")
    assert e.value.code == 2
    assert _row_counts() == before


def test_without_the_policy_execute_is_unchanged(fk_app, monkeypatch, capsys):
    owner, member = _user("qa_smoke_owner_13"), _user("qa_smoke_member_13")
    t, _r = _qa_team(owner, member)
    _run(monkeypatch, "--execute")
    out = capsys.readouterr().out
    assert db.session.get(A.Team, t.id) is not None
    assert {u.username for u in A.User.query} == {"qa_smoke_owner_13"}
    assert "TEAM-OWNER POLICY" not in out
    assert "1 blocked account(s) left untouched" in out


def test_an_owner_with_another_blocker_is_left_alone(fk_app, monkeypatch, capsys):
    owner = _user("qa_smoke_owner_14")
    child = _user("qa_smoke_child_14")
    t, _r = _qa_team(owner, child)
    db.session.add(A.GuardianLink(child_user_id=child.id, guardian_user_id=owner.id,
                                  method="test"))
    db.session.commit()
    _run(monkeypatch, "--team-owner-policy=delete-qa-team", "--execute")
    out = capsys.readouterr().out
    assert db.session.get(A.Team, t.id) is not None
    assert f"REFUSED id={owner.id}" in out and "account:guardian_link_guardian" in out
