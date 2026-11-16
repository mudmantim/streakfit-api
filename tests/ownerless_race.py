"""Ownerless teams racing the requests around them, on PostgreSQL (round 6).

Run by tests/test_account_deletion_postgres.py as a script, in the style of
team_owner_race.py: two real requests (or a request and a sweeper pass) in two
threads, the FIRST paused while it holds its locks, the SECOND proven to be
waiting on them, then both released and checked. No step may end in a 5xx, an
exception or a deadlock line in the app log, and no content may land in an
ownerless team.

  O1  owner leaves with nobody (paused holding the team)  vs  a member posts
                       -> leave 200, post 403 team_read_only, no message row
  O2  a member posts (paused holding the team FOR SHARE)  vs  owner leaves
      with nobody      -> post 201 (the team was owned), leave 200 after it
  O3  a mission completion (paused on the team FOR SHARE) vs  owner leaves
      with nobody      -> both 200, no deadlock; the campfire moved once
  O4  owner deletes their account under a team report (P9; paused holding
      the team)        vs  a member posts
                       -> deletion 200, post 403; the team and its rows kept
  O5  a legal hold being placed (paused holding the fence) vs the reaper
                       -> the reaper waits, then KEEPS the team (evidence)
  O6  the reaper (paused holding the team) vs a hold being placed
                       -> the hold waits; the team is deleted first (nothing
                          held it when the reaper checked); no deadlock
  O7  the photo sweep erasing an empty ownerless team's photos (paused
      inside its erase transaction) vs the reaper -> both finish, no deadlock,
      and the team is gone after the next pass at the latest
  O8  the reaper fails half-way -> nothing deleted (rollback), the pass
      counts it as failed instead of raising (round 7), the next pass
      deletes it

Prints one JSON object; exit 0 only if every check held.
"""
import datetime
import json
import logging
import secrets
import sys
import threading
import time
import uuid

PASSWORD = 'WalkTest123!'
_pause = threading.local()


def _install_hooks(A):
    def wrap(name):
        orig = getattr(A, name)

        def hooked(*args, **kwargs):
            out = orig(*args, **kwargs)
            gate = (getattr(_pause, 'points', None) or {}).pop(name, None)
            if gate is not None:
                gate['holding'].set()
                gate['go'].wait(30)
            return out
        setattr(A, name, hooked)

    for name in ('_lock_team', '_lock_team_for_content', '_lock_team_for_deletion',
                 '_legal_hold_fence'):
        wrap(name)


class _Grab(logging.Handler):
    def __init__(self):
        super().__init__()
        self.lines = []

    def emit(self, record):
        try:
            self.lines.append(record.getMessage())
        except Exception:
            pass


def _gate():
    return {'holding': threading.Event(), 'go': threading.Event()}


def _run(A, fn, points=None):
    box = {}

    def target():
        _pause.points = dict(points or {})
        try:
            with A.app.app_context():
                box['resp'] = fn(A.app.test_client())
        except Exception as exc:
            box['exc'] = repr(exc)[:300]
            try:
                A.db.session.rollback()
            except Exception:
                pass
    t = threading.Thread(target=target)
    t.start()
    return t, box


def race(A, first, point, second):
    gate = _gate()
    t1, b1 = _run(A, first, points={point: gate})
    if not gate['holding'].wait(20):
        gate['go'].set()
        t1.join(20)
        return b1, {'exc': 'first never reached its pause'}, False
    t2, b2 = _run(A, second)
    time.sleep(1.5)                     # > deadlock_timeout (1s)
    waited = t2.is_alive()
    gate['go'].set()
    t1.join(40)
    t2.join(40)
    return b1, b2, waited


def _status(box):
    r = box.get('resp')
    if r is None:
        return box.get('exc', 'no answer')
    return getattr(r, 'status_code', r)


def _body(box):
    r = box.get('resp')
    return r.get_json(silent=True) if hasattr(r, 'get_json') else r


def main():
    import app as A
    A.limiter.enabled = False
    A.app.config['TESTING'] = True
    _install_hooks(A)
    db = A.db
    grab = _Grab()
    A.app.logger.addHandler(grab)
    A.app.logger.setLevel(logging.INFO)
    results = {}
    tag = secrets.token_hex(3)
    n = [0]

    def check(name, ok, detail=''):
        results[name] = {'ok': bool(ok), 'detail': str(detail)[:400]}

    def person(prefix):
        n[0] += 1
        name = f'{prefix}{tag}{n[0]}'
        with A.app.app_context():
            c = A.app.test_client()
            c.post('/api/register', json={'username': name, 'password': PASSWORD})
            tok = c.post('/api/login', json={'username': name, 'password': PASSWORD}
                         ).get_json()['access_token']
            uid = A.User.query.filter_by(username=name).one().id
        return uid, {'Authorization': f'Bearer {tok}'}

    def team(owner, members, owner_in=True):
        with A.app.app_context():
            t = A.Team(name=f'or {owner}', created_by_user_id=owner)
            db.session.add(t)
            db.session.flush()
            db.session.add(A.TeamInviteCode(team_id=t.id, code=A._generate_team_invite_code()))
            db.session.add(A.TeamCampfire(team_id=t.id, total_team_missions=0))
            start = datetime.datetime.utcnow() - datetime.timedelta(days=30)
            for i, uid in enumerate(([owner] if owner_in else []) + list(members)):
                db.session.add(A.TeamMembership(team_id=t.id, user_id=uid,
                                                joined_at=start + datetime.timedelta(days=i)))
            db.session.commit()
            return t.id

    def empty_ownerless(with_photo=False):
        """Built OWNED (an ownerless team accepts no new rows -- the guard under
        test), then made ownerless and left empty, as real teams get there."""
        owner, _h = person('orph')
        with A.app.app_context():
            t = A.Team(name=f'orphan {tag}', created_by_user_id=owner)
            db.session.add(t)
            db.session.flush()
            db.session.add(A.TeamCampfire(team_id=t.id, total_team_missions=0))
            if with_photo:
                db.session.add(A.TeamPhoto(
                    public_id=uuid.uuid4().hex, team_id=t.id, image_data=b'x' * 64,
                    byte_size=64, caption='old',
                    created_at=datetime.datetime.utcnow() - datetime.timedelta(days=40),
                    expires_at=datetime.datetime.utcnow() - datetime.timedelta(days=10)))
            db.session.commit()
            db.session.execute(db.text('UPDATE team SET created_by_user_id = NULL WHERE id = :t'),
                               {'t': t.id})
            db.session.commit()
            return t.id

    def report(tid, hold=False, status='pending'):
        with A.app.app_context():
            r = A.Report(public_id=uuid.uuid4().hex, team_id=tid, category='harassment',
                         subject_type='user', status=status, legal_hold=hold,
                         legal_hold_reason='race' if hold else None)
            db.session.add(r)
            db.session.commit()
            return r.id

    def place_hold(rid):
        """What admin_legal_hold does: the fence EXCLUSIVE, then the hold."""
        def fn(_client):
            A._legal_hold_fence(exclusive=True)
            r = db.session.get(A.Report, rid)
            r.legal_hold, r.legal_hold_reason = True, 'race'
            db.session.commit()
            return 'held'
        return fn

    def reap_only(tid):
        """One reaper pass over exactly this team (earlier scenarios leave
        other kept teams behind, and the pause must land on THIS one)."""
        def fn(_client):
            orig = A._empty_ownerless_team_ids
            A._empty_ownerless_team_ids = (lambda limit=50, after_id=0:
                                            [tid] if after_id < tid else [])
            try:
                return A._reap_empty_ownerless_teams()
            finally:
                A._empty_ownerless_team_ids = orig
        return fn

    def counts(tid):
        with A.app.app_context():
            t = db.session.get(A.Team, tid)
            return {
                'exists': t is not None,
                'owner': t.created_by_user_id if t else 'gone',
                'messages': A.TeamMessage.query.filter_by(team_id=tid).count(),
                'moments': A.TeamMoment.query.filter_by(team_id=tid).count(),
                'members': A.TeamMembership.query.filter_by(team_id=tid).count(),
            }

    def deadlocks_since(mark):
        return [m for m in grab.lines[mark:] if 'deadlock' in m.lower()]

    def post(tid, h, body='hi'):
        return lambda c: c.post(f'/api/teams/{tid}/messages', json={'body': body}, headers=h)

    def leave_nobody(tid, h):
        return lambda c: c.post(f'/api/teams/{tid}/leave', json={'no_owner': True}, headers=h)

    # O1
    o, oh = person('o1o')
    m, mh = person('o1m')
    tid = team(o, [m])
    before = counts(tid)
    mark = len(grab.lines)
    b1, b2, waited = race(A, leave_nobody(tid, oh), '_lock_team', post(tid, mh))
    after = counts(tid)
    check('O1 the post waited for the leave', waited)
    check('O1 leave 200, post 403 team_read_only',
          (_status(b1), _status(b2)) == (200, 403)
          and (_body(b2) or {}).get('error') == 'team_read_only', (_status(b1), _status(b2), _body(b2)))
    check('O1 no message landed in the ownerless team',
          after['owner'] is None and after['messages'] == before['messages'], (before, after))
    check('O1 no deadlock', not deadlocks_since(mark), deadlocks_since(mark))

    # O2
    o, oh = person('o2o')
    m, mh = person('o2m')
    tid = team(o, [m])
    before = counts(tid)
    mark = len(grab.lines)
    b1, b2, waited = race(A, post(tid, mh), '_lock_team_for_content', leave_nobody(tid, oh))
    after = counts(tid)
    check('O2 the leave waited for the post', waited)
    check('O2 post 201, leave 200', (_status(b1), _status(b2)) == (201, 200),
          (_status(b1), _status(b2), _body(b1), _body(b2)))
    check('O2 the message landed while the team was owned; then ownerless',
          after['owner'] is None and after['messages'] == before['messages'] + 1, (before, after))
    check('O2 no deadlock', not deadlocks_since(mark), deadlocks_since(mark))

    # O3
    o, oh = person('o3o')
    m, mh = person('o3m')
    tid = team(o, [m])
    with A.app.app_context():
        c = A.app.test_client()
        keys = [e['key'] for e in c.get('/api/daily', headers=mh).get_json()['exercises']][:5]
        day = A.server_today().isoformat()
        for k in keys[:4]:
            c.post(f'/api/daily/{k}/complete', json={'mission_date': day}, headers=mh)
    before = counts(tid)
    mark = len(grab.lines)
    b1, b2, waited = race(A, lambda c: c.post(f'/api/daily/{keys[4]}/complete',
                                              json={'mission_date': day}, headers=mh),
                          '_lock_team_for_content', leave_nobody(tid, oh))
    after = counts(tid)
    check('O3 the leave waited for the completion', waited)
    check('O3 completion 200, leave 200', (_status(b1), _status(b2)) == (200, 200),
          (_status(b1), _status(b2), _body(b2)))
    check('O3 no deadlock', not deadlocks_since(mark), deadlocks_since(mark))
    check('O3 the team ends ownerless', after['owner'] is None, after)

    # O4 (P9): the sole owner deletes under a team report, paused holding the
    # team; their own second device posts meanwhile.
    o, oh = person('o4o')
    tid = team(o, [])
    with A.app.app_context():
        db.session.add(A.TeamMessage(team_id=tid, sender_type='user', sender_user_id=o,
                                     body='evidence'))
        db.session.commit()
    rid = report(tid)
    before = counts(tid)
    mark = len(grab.lines)
    b1, b2, waited = race(A, lambda c: c.delete('/api/me', json={'password': PASSWORD}, headers=oh),
                          '_lock_team_for_deletion', post(tid, oh, 'from my phone'))
    after = counts(tid)
    check('O4 the post waited for the deletion', waited)
    check('O4 deletion 200; the post then finds no account (404)',
          (_status(b1), _status(b2)) == (200, 404), (_status(b1), _status(b2), _body(b2)))
    check('O4 the team and its evidence are kept, ownerless',
          after['exists'] and after['owner'] is None
          and after['messages'] == before['messages'] and after['members'] == 0, (before, after))
    with A.app.app_context():
        check('O4 the report still names the team', db.session.get(A.Report, rid).team_id == tid)
    check('O4 no deadlock', not deadlocks_since(mark), deadlocks_since(mark))

    # O5: hold first, reaper second.
    tid = empty_ownerless()
    rid = report(tid, status='closed')
    mark = len(grab.lines)
    b1, b2, waited = race(A, place_hold(rid), '_legal_hold_fence', reap_only(tid))
    after = counts(tid)
    check('O5 the reaper waited for the hold', waited)
    check('O5 hold placed; reaper kept the team for it',
          _status(b1) == 'held' and isinstance(b2.get('resp'), dict)
          and b2['resp']['deleted'] == 0 and b2['resp']['failed'] == 0
          and b2['resp']['skipped'] == 1, (b1, b2))
    check('O5 the held team is still there', after['exists'], after)
    check('O5 no deadlock', not deadlocks_since(mark), deadlocks_since(mark))

    # O6: reaper first, hold second.
    tid = empty_ownerless()
    rid = report(tid, status='closed')
    due = [tid]
    mark = len(grab.lines)
    b1, b2, waited = race(A, reap_only(tid), '_lock_team_for_deletion', place_hold(rid))
    after = counts(tid)
    check('O6 the hold waited for the reaper', waited, due)
    check('O6 reaper deleted the team (nothing held it when it checked); hold then placed',
          (b1.get('resp') or {}).get('deleted') == 1 and b1['resp']['failed'] == 0
          and _status(b2) == 'held' and not after['exists'], (b1, b2, after))
    with A.app.app_context():
        r = db.session.get(A.Report, rid)
        check('O6 the report itself survives, its team link cut',
              r is not None and r.team_id is None and r.legal_hold is True)
    check('O6 no deadlock', not deadlocks_since(mark), deadlocks_since(mark))

    # O7: photo sweep vs reaper on the same empty ownerless team.
    tid = empty_ownerless(with_photo=True)
    mark = len(grab.lines)
    # Paused at the sweep's fence (shared), i.e. inside its erase transaction.
    b1, b2, waited = race(A, lambda c: A._sweep_expired_team_photos(), '_legal_hold_fence',
                          reap_only(tid))
    check('O7 both finish without an exception',
          'exc' not in b1 and 'exc' not in b2, (b1.get('exc'), b2.get('exc')))
    with A.app.app_context():
        reap_only(tid)(None)
    check('O7 nothing left after one more pass', not counts(tid)['exists'])
    check('O7 no deadlock', not deadlocks_since(mark), deadlocks_since(mark))

    # O8: an injected failure half-way through the reaper.
    tid = empty_ownerless(with_photo=True)
    real = A._qa_team_rows
    calls = {'n': 0}

    def boom(model, attr, team_id):
        calls['n'] += 1
        if calls['n'] == 4:
            raise RuntimeError('injected')
        return real(model, attr, team_id)
    A._qa_team_rows = boom
    with A.app.app_context():
        # Round 7 (review R6-2(b)): the pass does not raise; the team is
        # rolled back whole and counted as failed.
        failed = reap_only(tid)(None)['failed'] == 1
    A._qa_team_rows = real
    with A.app.app_context():
        photos = A.TeamPhoto.query.filter_by(team_id=tid).count()
    check('O8 the failure was counted and rolled back whole',
          failed and counts(tid)['exists'] and photos == 1, (failed, photos))
    with A.app.app_context():
        reap_only(tid)(None)
    check('O8 the next pass deletes it', not counts(tid)['exists'])

    A.app.logger.removeHandler(grab)
    print(json.dumps(results, indent=1))
    return 0 if results and all(v['ok'] for v in results.values()) else 1


if __name__ == '__main__':
    sys.exit(main())
