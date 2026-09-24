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
