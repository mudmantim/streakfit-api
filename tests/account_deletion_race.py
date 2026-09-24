"""Account deletion racing the writes that name the same person, on PostgreSQL.

Run by tests/test_account_deletion_postgres.py as a script (the app module binds
its database at import). Two real requests run in two threads, each with its
own app context and database session. The FIRST is paused while it holds its
lock; the test then proves the SECOND is actually waiting on that lock (still
running after a pause), releases the first, and checks both answers and the
final rows. Every ordering must end in a clean answer -- never a 500.

  deletion first  vs  operator legal hold     -> 200 / 200, hold names nobody
  hold first      vs  deletion                -> 200 / 409 (held report blocks)
  deletion first  vs  operator appeal decision-> 200 / 409 appeal_withdrawn
  decision first  vs  deletion                -> 200 / 200, decision stands
  filing first    vs  deletion of the reported-> 201 / 409 (pending blocks)
  deletion first  vs  filing about them       -> 200 / 403

Prints one JSON object; exit 0 only if every check held.
"""
import datetime
import json
import os
import sys
import threading
import time
import uuid

PASSWORD = 'WalkTest123!'
ADMIN = 'race-admin-secret'
_pause = threading.local()


def _install_hooks(A):
    """Pause a thread right after it takes its lock, when asked to."""
    orig_counts, orig_lock = A._account_dependent_counts, A._lock_moderation_subject
    orig_locks = A._lock_moderation_subjects
    orig_load = A._load_report_for_action

    def hold_here():
        gate = getattr(_pause, 'gate', None)
        if gate is not None:
            _pause.gate = None
            gate['holding'].set()
            gate['go'].wait(30)

    def counts(user_id):
        out = orig_counts(user_id)
        # The route plans once unlocked (dry run), then again under FOR UPDATE.
        _pause.counts_calls = getattr(_pause, 'counts_calls', 0) + 1
        if _pause.counts_calls == 2:
            hold_here()
        return out

    def lock(user_id):
        out = orig_lock(user_id)
        if not getattr(_pause, 'in_plural', False):
            hold_here()
        return out

    def locks(*user_ids):
        # Pause only once EVERY person is locked, not after the first.
        _pause.in_plural = True
        try:
            out = orig_locks(*user_ids)
        finally:
            _pause.in_plural = False
        if not getattr(_pause, 'in_load', False):
            hold_here()
        return out

    def load(public_id):
        # For report actions, pause only once the REPORT row is locked too.
        _pause.in_load = True
        try:
            out = orig_load(public_id)
        finally:
            _pause.in_load = False
        hold_here()
        return out

    A._account_dependent_counts = counts
    A._lock_moderation_subject = lock
    A._lock_moderation_subjects = locks
    A._load_report_for_action = load


def _run(A, fn, gate=None):
    box = {}

    def target():
        _pause.gate, _pause.counts_calls = gate, 0
        try:
            with A.app.app_context():
                box['resp'] = fn(A.app.test_client())
        except Exception as exc:          # a 500 surfaces here in TESTING mode
            box['exc'] = repr(exc)[:300]
    t = threading.Thread(target=target)
    t.start()
    return t, box


def race(A, first, second):
    """Run `first` until it holds its lock, then `second`; prove `second`
    waits; release. Returns (first_box, second_box, second_waited)."""
    gate = {'holding': threading.Event(), 'go': threading.Event()}
    t1, b1 = _run(A, first, gate)
    if not gate['holding'].wait(20):
        gate['go'].set()
        t1.join(20)
        return b1, {'exc': 'first never took its lock'}, False
    t2, b2 = _run(A, second)
    time.sleep(1.0)
    waited = t2.is_alive()
    gate['go'].set()
    t1.join(30)
    t2.join(30)
    return b1, b2, waited


def _status(box):
    if 'resp' in box:
        return box['resp'].status_code
    return box.get('exc', 'no answer')


def main():
    import app as A
    A.limiter.enabled = False
    A.app.config['TESTING'] = True
    os.environ['ADMIN_SECRET'] = ADMIN
    _install_hooks(A)
    db = A.db
    H = {'X-Admin-Secret': ADMIN}
    results = {}
    n = [0]

    def person(prefix):
        n[0] += 1
        name = f'{prefix}{n[0]}'
        with A.app.app_context():
            c = A.app.test_client()
            c.post('/api/register', json={'username': name, 'password': PASSWORD})
            tok = c.post('/api/login', json={'username': name, 'password': PASSWORD}
                         ).get_json()['access_token']
            uid = A.User.query.filter_by(username=name).one().id
        return uid, {'Authorization': f'Bearer {tok}'}

    def delete_me(h):
        return lambda c: c.delete('/api/me', json={'password': PASSWORD}, headers=h)

    def closed_report_about(reporter, reported, team_id=None):
        with A.app.app_context():
            r = A.Report(public_id=uuid.uuid4().hex, reporter_user_id=reporter,
                         reported_user_id=reported, category='harassment',
                         subject_type='user', team_id=team_id, status='closed',
                         disposition='dismissed', reviewed_at=datetime.datetime.utcnow())
            db.session.add(r)
            db.session.commit()
            return r.public_id, r.id

    def check(name, ok, detail=''):
        results[name] = {'ok': bool(ok), 'detail': str(detail)}

    # 1. deletion first, operator puts a legal hold on a closed report about them
    rep_by, _ = person('rep')
    victim, vh = person('held')
    pid, rid = closed_report_about(rep_by, victim)
    b1, b2, waited = race(A, delete_me(vh), lambda c: c.post(
        f'/api/admin/reports/{pid}/legal-hold', json={'hold': True, 'reason': 'race'},
        headers=H))
    with A.app.app_context():
        r = db.session.get(A.Report, rid)
        acts = A.ModerationAction.query.filter_by(report_id=rid).all()
        check('1 deletion first: the hold waited for the deletion', waited)
        check('1 deletion first: deletion 200, hold 200 (no 500)',
              (_status(b1), _status(b2)) == (200, 200), (_status(b1), _status(b2)))
        check('1 the hold stands and names nobody',
              r.legal_hold and r.reported_user_id is None
              and all(a.target_user_id is None for a in acts), [a.target_user_id for a in acts])

    # 2. hold first, then the reported person tries to delete
    rep_by, _ = person('rep')
    victim, vh = person('held')
    pid, rid = closed_report_about(rep_by, victim)
    b1, b2, waited = race(A, lambda c: c.post(
        f'/api/admin/reports/{pid}/legal-hold', json={'hold': True, 'reason': 'race'},
        headers=H), delete_me(vh))
    with A.app.app_context():
        check('2 hold first: the deletion waited for the hold', waited)
        body = b2['resp'].get_json() if 'resp' in b2 else {}
        check('2 hold first: hold 200, deletion 409 safety_record',
              (_status(b1), _status(b2)) == (200, 409)
              and body.get('blocker_codes') == ['safety_record'], (_status(b1), _status(b2), body))
        check('2 the person still exists and is still named',
              db.session.get(A.User, victim) is not None
              and db.session.get(A.Report, rid).reported_user_id == victim)

    def appeal_for(uid):
        with A.app.app_context():
            act = A.ModerationAction(action='suspend_social', target_user_id=uid, note='why')
            db.session.add(act)
            db.session.flush()
            ap = A.Appeal(public_id=uuid.uuid4().hex, user_id=uid, action_id=act.id,
                          reason='please')
            db.session.add_all([ap, A.UserRestriction(user_id=uid, kind='social_suspended')])
            db.session.commit()
            return ap.public_id, ap.id, act.id

    def decide(apid):
        return lambda c: c.post(f'/api/admin/appeals/{apid}/decide',
                                json={'outcome': 'overturned', 'note': 'race'}, headers=H)

    # 3. deletion first, operator decides the appeal
    uid, uh = person('appl')
    apid, apk, _ = appeal_for(uid)
    b1, b2, waited = race(A, delete_me(uh), decide(apid))
    with A.app.app_context():
        ap = db.session.get(A.Appeal, apk)
        body = b2['resp'].get_json() if 'resp' in b2 else {}
        check('3 deletion first: the decision waited for the deletion', waited)
        check('3 deletion first: deletion 200, decision 409 appeal_withdrawn',
              (_status(b1), _status(b2)) == (200, 409)
              and body.get('code') == 'appeal_withdrawn', (_status(b1), _status(b2)))
        check('3 the appeal reads withdrawn', (ap.status, ap.outcome) == ('closed', 'withdrawn'))

    # 4. decision first, then the appellant deletes
    uid, uh = person('appl')
    apid, apk, _ = appeal_for(uid)
    b1, b2, waited = race(A, decide(apid), delete_me(uh))
    with A.app.app_context():
        ap = db.session.get(A.Appeal, apk)
        overturned = A.ModerationAction.query.filter_by(action='appeal_overturned').all()
        check('4 decision first: the deletion waited for the decision', waited)
        check('4 decision first: decision 200, deletion 200',
              (_status(b1), _status(b2)) == (200, 200), (_status(b1), _status(b2)))
        check('4 the decision stands, without the person',
              (ap.outcome, ap.user_id, ap.reason) == ('overturned', None, None)
              and overturned and all(a.target_user_id is None for a in overturned))

    # A shared team, so a person can be reported.
    def teammates():
        a, ah = person('mate')
        b, bh = person('mate')
        with A.app.app_context():
            t = A.Team(name=f'race {a}', created_by_user_id=a)
            db.session.add(t)
            db.session.flush()
            db.session.add_all([A.TeamMembership(team_id=t.id, user_id=a),
                                A.TeamMembership(team_id=t.id, user_id=b)])
            db.session.commit()
            return a, ah, b, bh, t.id

    def file_about(h, target, team_id):
        return lambda c: c.post('/api/reports', json={
            'category': 'harassment', 'subject_type': 'user',
            'reported_user_id': target, 'team_id': team_id}, headers=h)

    # 5. filing first, then the reported person tries to delete
    a, ah, b, bh, tid = teammates()
    b1, b2, waited = race(A, file_about(ah, b, tid), delete_me(bh))
    with A.app.app_context():
        check('5 filing first: the deletion waited for the report', waited)
        check('5 filing first: report 201, deletion 409',
              (_status(b1), _status(b2)) == (201, 409), (_status(b1), _status(b2)))
        check('5 the reported person still exists', db.session.get(A.User, b) is not None)

    # 6. deletion first, then someone files about them
    a, ah, b, bh, tid = teammates()
    before = None
    with A.app.app_context():
        before = A.Report.query.count()
    b1, b2, waited = race(A, delete_me(bh), file_about(ah, b, tid))
    with A.app.app_context():
        check('6 deletion first: the report waited for the deletion', waited)
        check('6 deletion first: deletion 200, report 403 (as for anyone unseen)',
              (_status(b1), _status(b2)) == (200, 403), (_status(b1), _status(b2)))
        check('6 no report was written', A.Report.query.count() == before)

    # 7. hold first, then the REPORTER of that report tries to delete
    rep_by, rh = person('filer')
    other, _ = person('about')
    pid, rid = closed_report_about(rep_by, other)
    b1, b2, waited = race(A, lambda c: c.post(
        f'/api/admin/reports/{pid}/legal-hold', json={'hold': True, 'reason': 'race'},
        headers=H), delete_me(rh))
    with A.app.app_context():
        r = db.session.get(A.Report, rid)
        check("7 hold first: the reporter's deletion waited for the hold", waited)
        check('7 hold first: hold 200, reporter deletion 409',
              (_status(b1), _status(b2)) == (200, 409), (_status(b1), _status(b2)))
        check('7 the held report still names its reporter',
              r.legal_hold and r.reporter_user_id == rep_by)

    # 8. hold first, retention sweep meanwhile: the held report is not purged
    rep_by, _ = person('swp')
    other, _ = person('swp')
    pid, rid = closed_report_about(rep_by, other)
    with A.app.app_context():
        r = db.session.get(A.Report, rid)
        r.reviewed_at = datetime.datetime.utcnow() - datetime.timedelta(days=45)
        r.note = 'reporter words'
        db.session.add(A.ReportEvidence(report_id=rid, content_type='message',
                                        content_text='kept', author_user_id=other,
                                        context_json='{}'))
        db.session.commit()

    def sweep(_client):
        out = A._sweep_moderation_evidence()
        db.session.commit()
        return type('R', (), {'status_code': 200, 'out': out})()
    b1, b2, waited = race(A, lambda c: c.post(
        f'/api/admin/reports/{pid}/legal-hold', json={'hold': True, 'reason': 'race'},
        headers=H), sweep)
    with A.app.app_context():
        r = db.session.get(A.Report, rid)
        ev = A.ReportEvidence.query.filter_by(report_id=rid).one()
        check('8 hold first: the sweep did not purge the report being held',
              _status(b1) == 200 and r.legal_hold and r.note == 'reporter words'
              and ev.content_text == 'kept', (_status(b1), r.note, ev.content_text))
        again = A._sweep_moderation_evidence()
        db.session.commit()
        ev = A.ReportEvidence.query.filter_by(report_id=rid).one()
        check('8 the next sweep counts it as held and still keeps it',
              again['held_by_legal_hold'] >= 1 and ev.content_text == 'kept', again)

    # 9. two people who blocked, challenged and reported each other delete at once
    def mutual_pair():
        p, ph, q, qh, tid = teammates()
        with A.app.app_context():
            now = datetime.datetime.utcnow()
            db.session.add_all([
                A.UserBlock(blocker_user_id=p, blocked_user_id=q),
                A.UserBlock(blocker_user_id=q, blocked_user_id=p),
                A.TeamChallenge(public_id=uuid.uuid4().hex, team_id=tid, preset_key='x',
                                created_by_user_id=p, target_user_id=q,
                                expires_at=now + datetime.timedelta(hours=1)),
                A.TeamChallenge(public_id=uuid.uuid4().hex, team_id=tid, preset_key='x',
                                created_by_user_id=q, target_user_id=p,
                                expires_at=now + datetime.timedelta(hours=1)),
            ])
            db.session.commit()
            # The team's creator would be blocked; hand the team to nobody by
            # making a third person its creator.
            third, _ = person('owner')
            t = db.session.get(A.Team, tid)
            t.created_by_user_id = third
            db.session.commit()
        closed_report_about(p, q)
        closed_report_about(q, p)
        return p, ph, q, qh
    codes = []
    for _ in range(8):
        p, ph, q, qh = mutual_pair()
        t1, x = _run(A, delete_me(ph))
        t2, y = _run(A, delete_me(qh))
        t1.join(60)
        t2.join(60)
        codes.append((_status(x), _status(y)))
    check('9 simultaneous mutual deletions all succeed (deadlocks retried)',
          all(c == (200, 200) for c in codes), codes)

    # 10. a challenge addressed to someone deleting at that instant, both ways
    a, ah, b, bh, tid = teammates()
    preset = A.CHALLENGE_PRESETS[0]['key']

    def challenge(h, target):
        return lambda c: c.post(f'/api/teams/{tid}/challenges',
                                json={'preset_key': preset, 'target_user_id': target}, headers=h)
    b1, b2, waited = race(A, delete_me(bh), challenge(ah, b))
    check('10 deletion first: the challenge waited, then 400 (not a member), no 500',
          waited and (_status(b1), _status(b2)) == (200, 400), (waited, _status(b1), _status(b2)))

    print(json.dumps(results, indent=1))
    return 0 if all(v['ok'] for v in results.values()) else 1


if __name__ == '__main__':
    sys.exit(main())
