"""Account deletion against every foreign key into `user`.

DELETE /api/me shipped returning 500 on PostgreSQL for anyone who had ever set
a daily effort level: `daily_effort.user_id` references `user`, the deletion
service did not know the table existed, and SQLite -- which the rest of this
suite runs on -- ignores foreign keys unless asked. These tests ask.

The PostgreSQL run of the same scenario is tests/test_account_deletion_postgres.py.
"""
import pytest
from sqlalchemy import event

import app as appmod
from app import db
from tests.account_deletion_scenario import run


def _user_foreign_keys():
    return sorted((t.name, c.name)
                  for t in db.metadata.sorted_tables for c in t.columns
                  if any(fk.column.table.name == 'user' for fk in c.foreign_keys))


def test_every_foreign_key_into_user_has_a_deletion_rule():
    """A new column pointing at `user` must be given a rule -- delete, cut the
    link, or block -- before account deletion can be trusted with it."""
    rules = appmod._user_fk_classification()
    fks = _user_foreign_keys()
    assert sorted(rules) == fks, (
        f"no rule: {sorted(set(fks) - set(rules))}; "
        f"rule for a key that does not exist: {sorted(set(rules) - set(fks))}")


def test_no_foreign_key_has_two_rules():
    seen = []
    for rows in (appmod._USER_PRIVATE_DELETES, appmod._USER_LINK_NULLS,
                 appmod._USER_DELETION_BLOCKERS):
        for _label, model, attr in rows:
            seen.append((model.__tablename__,
                         getattr(model, attr).property.columns[0].name))
    seen += list(appmod._USER_FK_HANDLED_BY_HAND)
    assert len(seen) == len(set(seen))


def test_a_nullified_link_is_actually_nullable():
    """Cutting a link on a NOT NULL column would fail exactly like the bug."""
    for _label, model, attr in appmod._USER_LINK_NULLS:
        assert getattr(model, attr).property.columns[0].nullable, (model, attr)


@pytest.fixture()
def fk_app(app):
    """The app fixture, with SQLite enforcing foreign keys on every connection."""
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


def test_the_production_bug_a_daily_effort_no_longer_stops_deletion(fk_app):
    client = fk_app.test_client()
    client.post('/api/register', json={'username': 'effortful', 'password': 'WalkTest123!'})
    tok = client.post('/api/login', json={'username': 'effortful',
                                          'password': 'WalkTest123!'}).get_json()['access_token']
    h = {'Authorization': f'Bearer {tok}'}
    uid = appmod.User.query.filter_by(username='effortful').one().id
    import datetime
    db.session.add(appmod.DailyEffort(user_id=uid, date=datetime.date(2026, 9, 1),
                                      level='easy'))
    db.session.commit()
    resp = client.delete('/api/me', json={'password': 'WalkTest123!'}, headers=h)
    assert resp.status_code == 200, resp.get_json()
    db.session.remove()
    assert db.session.get(appmod.User, uid) is None


def test_deletion_scenario_with_foreign_keys_enforced(fk_app):
    results = run(appmod, fk_app.test_client())
    failed = {name: detail for name, (ok, detail) in results.items() if not ok}
    assert not failed, failed


def test_self_deletion_never_tells_you_that_you_were_reported(fk_app):
    """The deletion plan counts reports about the person, evidence of their
    words, actions against them and blocks others made of them. None of that
    may reach them in the response -- the reported person is never told a
    report exists."""
    import uuid
    client = fk_app.test_client()
    for name in ('reported_one', 'someone_else'):
        client.post('/api/register', json={'username': name, 'password': 'WalkTest123!'})
    tok = client.post('/api/login', json={'username': 'reported_one',
                                          'password': 'WalkTest123!'}).get_json()['access_token']
    me = appmod.User.query.filter_by(username='reported_one').one().id
    other = appmod.User.query.filter_by(username='someone_else').one().id
    rep = appmod.Report(public_id=uuid.uuid4().hex, reporter_user_id=other,
                        reported_user_id=me, category='harassment', subject_type='user',
                        status='closed', disposition='dismissed')  # pending would block
    db.session.add(rep)
    db.session.flush()
    db.session.add_all([
        appmod.ReportEvidence(report_id=rep.id, content_type='user', author_user_id=me),
        appmod.ModerationAction(report_id=rep.id, action='dismiss', target_user_id=me),
        appmod.UserBlock(blocker_user_id=other, blocked_user_id=me),
    ])
    db.session.commit()
    resp = client.delete('/api/me', json={'password': 'WalkTest123!'},
                         headers={'Authorization': f'Bearer {tok}'})
    assert resp.status_code == 200
    body = resp.get_json()
    assert set(body['counts']) <= set(appmod._SELF_DELETION_COUNTS)
    blob = str(body).lower()
    for word in ('report', 'moderation', 'evidence', 'received', 'restriction', 'appeal'):
        assert word not in blob, word


def test_deletion_log_lines_carry_no_user_id(fk_app, caplog):
    """The deletion line, timed to the same moment as the reporter_note_removed
    row, would name the reporter those rows were written to forget."""
    import logging
    client = fk_app.test_client()
    client.post('/api/register', json={'username': 'logless', 'password': 'WalkTest123!'})
    tok = client.post('/api/login', json={'username': 'logless',
                                          'password': 'WalkTest123!'}).get_json()['access_token']
    uid = appmod.User.query.filter_by(username='logless').one().id
    with caplog.at_level(logging.INFO, logger=fk_app.logger.name):
        resp = client.delete('/api/me', json={'password': 'WalkTest123!'},
                             headers={'Authorization': f'Bearer {tok}'})
    assert resp.status_code == 200
    lines = [r.getMessage() for r in caplog.records if 'account_' in r.getMessage()]
    assert any('event=account_deleted' in l for l in lines)
    assert any('event=account_self_deleted' in l for l in lines)
    assert not any('user_id' in l or str(uid) in l.split('photos=')[0] for l in lines), lines


def test_a_team_owner_cannot_use_deletion_to_learn_about_a_report(fk_app):
    """An owner is refused either way, so they lose nothing by asking. If the
    answer changed when a report about them appeared, polling DELETE /api/me
    would reveal when one was filed and closed."""
    import uuid
    client = fk_app.test_client()
    for name in ('owner_o', 'mate_o'):
        client.post('/api/register', json={'username': name, 'password': 'WalkTest123!'})
    tok = client.post('/api/login', json={'username': 'owner_o',
                                          'password': 'WalkTest123!'}).get_json()['access_token']
    h = {'Authorization': f'Bearer {tok}'}
    owner = appmod.User.query.filter_by(username='owner_o').one().id
    mate = appmod.User.query.filter_by(username='mate_o').one().id
    db.session.add(appmod.Team(name='mine', created_by_user_id=owner))
    db.session.commit()
    before = client.delete('/api/me', json={'password': 'WalkTest123!'}, headers=h)
    db.session.add(appmod.Report(public_id=uuid.uuid4().hex, reporter_user_id=mate,
                                 reported_user_id=owner, category='harassment',
                                 subject_type='user', legal_hold=True, legal_hold_reason='x'))
    db.session.commit()
    after = client.delete('/api/me', json={'password': 'WalkTest123!'}, headers=h)
    assert before.status_code == after.status_code == 409
    assert before.get_json() == after.get_json()
    assert after.get_json()['blocker_codes'] == ['team_owned']
    assert appmod.delete_user_account(owner, dry_run=True)['blocker_codes'] == [
        'team_owned', 'report_open_about']      # the plan still knows


def _person(client, name):
    client.post('/api/register', json={'username': name, 'password': 'WalkTest123!'})
    tok = client.post('/api/login', json={'username': name,
                                          'password': 'WalkTest123!'}).get_json()['access_token']
    return (appmod.User.query.filter_by(username=name).one().id,
            {'Authorization': f'Bearer {tok}'})


def test_a_refusal_tells_the_operator_not_the_person(fk_app, monkeypatch):
    """A report holding up a deletion is flagged in /admin -- once, with no id
    and no role -- and the person's answer is exactly what it was without it."""
    import uuid
    monkeypatch.setenv('ADMIN_SECRET', 'ind-secret')
    AH = {'X-Admin-Secret': 'ind-secret'}
    client = fk_app.test_client()
    filer, fh = _person(client, 'ind_filer')
    about, _ = _person(client, 'ind_about')
    rep = appmod.Report(public_id=uuid.uuid4().hex, reporter_user_id=filer,
                        reported_user_id=about, category='harassment', subject_type='user')
    other = appmod.Report(public_id=uuid.uuid4().hex, reporter_user_id=about,
                          category='harassment', subject_type='user')   # unrelated to filer
    db.session.add_all([rep, other])
    db.session.commit()
    pid, rid, oid = rep.public_id, rep.id, other.id

    first = client.delete('/api/me', json={'password': 'WalkTest123!'}, headers=fh)
    db.session.remove()
    body = first.get_json()
    assert first.status_code == 409 and body['blocker_codes'] == ['safety_record']
    assert set(body) == {'error', 'message', 'blockers', 'blocker_codes'}
    assert 'from the app' not in body['message'] and 'report' not in body['message'].lower()
    marked = db.session.get(appmod.Report, rid).deletion_requested_at
    assert marked is not None
    assert db.session.get(appmod.Report, oid).deletion_requested_at is None

    again = client.delete('/api/me', json={'password': 'WalkTest123!'}, headers=fh)
    db.session.remove()
    assert again.get_json() == body                               # same answer
    assert db.session.get(appmod.Report, rid).deletion_requested_at == marked   # first time only

    queue = client.get('/api/admin/reports', headers=AH).get_json()
    row = next(r for r in queue['reports'] if r['report_id'] == pid)
    assert row['deletion_waiting'] is True and queue['counts']['deletion_waiting'] == 1
    assert 'reporter' not in str(row).lower().replace('reported_user_id', '')
    detail = client.get(f'/api/admin/reports/{pid}', headers=AH).get_json()
    assert detail['deletion_waiting'] is True

    client.post(f'/api/admin/reports/{pid}/action', json={'action': 'dismiss', 'note': 'x'},
                headers=AH)
    queue = client.get('/api/admin/reports?status=all', headers=AH).get_json()
    row = next(r for r in queue['reports'] if r['report_id'] == pid)
    assert row['deletion_waiting'] is False and queue['counts']['deletion_waiting'] == 0
    assert client.delete('/api/me', json={'password': 'WalkTest123!'},
                         headers=fh).status_code == 200


def test_a_team_owner_refusal_marks_nothing(fk_app):
    """The owner's refusal is about the team; flagging a report would point the
    operator at the wrong thing."""
    import uuid
    client = fk_app.test_client()
    owner, oh = _person(client, 'ind_owner')
    mate, _ = _person(client, 'ind_mate')
    db.session.add(appmod.Team(name='t', created_by_user_id=owner))
    rep = appmod.Report(public_id=uuid.uuid4().hex, reporter_user_id=mate,
                        reported_user_id=owner, category='harassment', subject_type='user')
    db.session.add(rep)
    db.session.commit()
    rid = rep.id
    assert client.delete('/api/me', json={'password': 'WalkTest123!'},
                         headers=oh).get_json()['blocker_codes'] == ['team_owned']
    db.session.remove()
    assert db.session.get(appmod.Report, rid).deletion_requested_at is None


def test_challenge_evidence_says_the_target_left(fk_app):
    """Captured after the target deleted: target_left, not a whole-team read."""
    client = fk_app.test_client()
    owner, oh = _person(client, 'ev_owner')
    kid, kh = _person(client, 'ev_kid')
    mate, mh = _person(client, 'ev_mate')
    team = client.post('/api/teams', json={'name': 'ev'}, headers=oh).get_json()['team']
    for h in (kh, mh):
        client.post(f"/api/teams/{team['id']}/join", json={'code': team['invite_code']}, headers=h)
    preset = appmod.CHALLENGE_PRESETS[0]['key']
    ch = client.post(f"/api/teams/{team['id']}/challenges",
                     json={'preset_key': preset, 'target_user_id': mate}, headers=oh).get_json()
    assert client.delete('/api/me', json={'password': 'WalkTest123!'}, headers=mh).status_code == 200
    db.session.remove()
    cid = ch.get('challenge', ch).get('public_id')
    r = client.post('/api/reports', json={'category': 'inappropriate_content',
                                          'subject_type': 'challenge', 'subject_ref': cid},
                    headers=kh)
    assert r.status_code == 201, r.get_json()
    import json as _json
    rep = appmod.Report.query.filter_by(public_id=r.get_json()['report_id']).one()
    ctx = _json.loads(appmod.ReportEvidence.query.filter_by(report_id=rep.id).one().context_json)
    assert ctx['target_user_id'] is None and ctx['target_left'] is True
