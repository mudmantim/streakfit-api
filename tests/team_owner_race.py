"""Team ownership racing the requests around it, on PostgreSQL (PR #23 follow-up).

Run by tests/test_account_deletion_postgres.py as a script (the app module binds
its database at import), in the same style as account_deletion_race.py: two
real requests in two threads, the FIRST paused while it holds its locks, the
SECOND proven to be waiting on them, then both released and checked.

  D2a  member B's deletion first   vs  legacy owner A's deletion (B is the only
       member, so A's fallback would hand the team to B)  -> 200 / 200, no 500;
       the team, left with nobody, is deleted as any empty team is
  D2b  the same pair, A's deletion first                  -> 200 / 200, no 500
  D4a  legacy owner A deleting first  vs  B (next owner) leaving
                                     -> 200 / 409 owner_must_choose, no deadlock
  D4b  B leaving first  vs  A deleting                    -> 200 / 200, the team
       goes to C, no deadlock
  R1   a sole owner deleting  vs  someone joining with the real code
                                                          -> 200 / 404, team gone
  R2   two hand-overs by the same owner at once           -> 200 / 403, one owner

Run with the argument `lock-order <name>` instead, it plays ONE of the paused
interleavings from the PR #23 follow-up review (2026-11-14), each of which
deadlocked on b365d16 (10/10 in the review):

  S5   DEF-2: A (legacy owner) deletes, paused after locking its candidate B
       and again after locking the team; meanwhile E, recorded as adult, joins
       and then leaves. The fallback prefers E, whom A had not locked.
                                     -> 200 / 200, the team goes to B, no deadlock
  S5c  DEF-2 with no age band: A pauses before locking B; E joins; B deletes;
       A locks the team and pauses; E leaves. Nobody A locked is left.
                                     -> 200 / 200, the empty team is deleted
  S6   DEF-3: B (LOWER id than A, a member) reports A's old message while A's
       deletion is paused at its first user lock.
                                     -> report 201, deletion 409 (that report
                                        is pending), no deadlock
  S1   R2-NOTE-S1 (round-2 review): legacy owner A's unlocked dry run is
       paused right after reading the members [B]; B, the only member,
       deletes meanwhile.            -> 200 / 200, the empty team is deleted
                                        (3b143a1: A got 409 team_owned)

"No deadlock" is checked from the app's own log lines (deadlock retry in the
deletion service, or the 40P01 handler on a route), not just from the final
answers: PostgreSQL may break a deadlock by retrying the deletion, which ends
in a correct state that would otherwise hide it.

Prints one JSON object; exit 0 only if every check held.
"""
import datetime
import json
import logging
import secrets
import sys
import threading
import time

PASSWORD = 'WalkTest123!'
_pause = threading.local()


def _install_hooks(A):
    orig_counts, orig_lock_team = A._account_dependent_counts, A._lock_team
    orig_lock_deletion_team = A._lock_team_for_deletion
    orig_lock_subject = A._lock_moderation_subject

    def hold_at(point):
        """Named pauses (lock-order scenarios); each one is used once."""
        gate = (getattr(_pause, 'points', None) or {}).pop(point, None)
        if gate is not None:
            gate['holding'].set()
            gate['go'].wait(30)

    def lock_deletion_team(team_id):
        hold_at('before_team')
        out = orig_lock_deletion_team(team_id)
        hold_at('after_team')
        return out

    def lock_subject(user_id):
        hold_at('before_subject')
        return orig_lock_subject(user_id)

    A._lock_team_for_deletion = lock_deletion_team
    A._lock_moderation_subject = lock_subject

    # S1: a pause right after the plan's FIRST read of a team's members, the
    # unlocked dry run's. Wraps whichever reader this version has (3b143a1:
    # _team_member_ids_in_order, with the accounts read separately after it;
    # since round 3: _team_members_with_accounts, one statement).
    for reader in ('_team_members_with_accounts', '_team_member_ids_in_order'):
        orig = getattr(A, reader, None)
        if orig is None:
            continue

        def read_members(team_id, _orig=orig):
            out = _orig(team_id)
            hold_at('after_members')
            return out
        setattr(A, reader, read_members)

    def hold_here():
        gate = getattr(_pause, 'gate', None)
        if gate is not None:
            _pause.gate = None
            gate['holding'].set()
            gate['go'].wait(30)

    def counts(user_id, team_plan=None):
        out = orig_counts(user_id, team_plan)
        # The route plans once unlocked (dry run), then again under its locks.
        _pause.counts_calls = getattr(_pause, 'counts_calls', 0) + 1
        if _pause.counts_calls == 2:
            hold_here()
        return out

    def lock_team(team_id):
        out = orig_lock_team(team_id)
        if getattr(_pause, 'pause_on_team', False):
            hold_here()
        return out

    A._account_dependent_counts = counts
    A._lock_team = lock_team


class _Grab(logging.Handler):
    def __init__(self):
        super().__init__()
        self.lines = []

    def emit(self, record):
        try:
            self.lines.append(record.getMessage())
        except Exception:
            pass


def _run(A, fn, gate=None, pause_on_team=False, points=None):
    box = {}

    def target():
        _pause.gate, _pause.counts_calls = gate, 0
        _pause.points = dict(points or {})
        _pause.pause_on_team = pause_on_team
        try:
            with A.app.app_context():
                box['resp'] = fn(A.app.test_client())
        except Exception as exc:          # a 500 surfaces here in TESTING mode
            box['exc'] = repr(exc)[:300]
    t = threading.Thread(target=target)
    t.start()
    return t, box


def race(A, first, second, first_pauses_on_team=False):
    gate = {'holding': threading.Event(), 'go': threading.Event()}
    t1, b1 = _run(A, first, gate, pause_on_team=first_pauses_on_team)
    if not gate['holding'].wait(20):
        gate['go'].set()
        t1.join(20)
        return b1, {'exc': 'first never took its lock'}, False
    t2, b2 = _run(A, second)
    time.sleep(1.5)                     # > deadlock_timeout (1s)
    waited = t2.is_alive()
    gate['go'].set()
    t1.join(40)
    t2.join(40)
    return b1, b2, waited


def _status(box):
    if 'resp' in box:
        return box['resp'].status_code
    return box.get('exc', 'no answer')


def _body(box):
    return box['resp'].get_json(silent=True) if 'resp' in box else None


def _gate():
    return {'holding': threading.Event(), 'go': threading.Event()}


def main(argv):
    import app as A
    A.limiter.enabled = False
    A.app.config['TESTING'] = True
    _install_hooks(A)
    db = A.db
    grab = _Grab()
    A.app.logger.addHandler(grab)
    A.app.logger.setLevel(logging.INFO)
    results = {}
    run_tag = secrets.token_hex(3)
    n = [0]

    def check(name, ok, detail=''):
        results[name] = {'ok': bool(ok), 'detail': str(detail)}

    def person(prefix):
        n[0] += 1
        name = f'{prefix}{run_tag}{n[0]}'
        with A.app.app_context():
            c = A.app.test_client()
            c.post('/api/register', json={'username': name, 'password': PASSWORD})
            tok = c.post('/api/login', json={'username': name, 'password': PASSWORD}
                         ).get_json()['access_token']
            uid = A.User.query.filter_by(username=name).one().id
        return uid, {'Authorization': f'Bearer {tok}'}

    def team(owner, members, owner_in_team=True):
        """A team created by `owner`; `members` joined in that order, a day apart."""
        with A.app.app_context():
            t = A.Team(name=f'race {owner}', created_by_user_id=owner)
            db.session.add(t)
            db.session.flush()
            code = A._generate_team_invite_code()
            db.session.add(A.TeamInviteCode(team_id=t.id, code=code))
            start = datetime.datetime.utcnow() - datetime.timedelta(days=30)
            people = ([owner] if owner_in_team else []) + list(members)
            for i, uid in enumerate(people):
                db.session.add(A.TeamMembership(
                    team_id=t.id, user_id=uid,
                    joined_at=start + datetime.timedelta(days=i)))
            db.session.commit()
            return t.id, code

    def delete_me(h):
        return lambda c: c.delete('/api/me', json={'password': PASSWORD}, headers=h)

    def deadlocks_since(mark):
        return [m for m in grab.lines[mark:] if 'deadlock' in m]

    def team_state(tid):
        with A.app.app_context():
            t = db.session.get(A.Team, tid)
            members = A._team_member_ids_in_order(tid) if t else []
            return (t.created_by_user_id if t else None), members, t is not None

    if argv[:1] == ['lock-order']:
        _lock_order(A, argv[1], check, person, team, delete_me, deadlocks_since,
                    team_state, grab)
        A.app.logger.removeHandler(grab)
        print(json.dumps(results, indent=1))
        return 0 if results and all(v['ok'] for v in results.values()) else 1

    # D2a. B (the only member) deletes first; the legacy owner A deletes meanwhile.
    a, ah = person('own')
    b, bh = person('mem')
    tid, _ = team(a, [b], owner_in_team=False)
    b1, b2, waited = race(A, delete_me(bh), delete_me(ah))
    owner, members, exists = team_state(tid)
    check('D2a B first: A waited for B', waited)
    check('D2a B first: B 200, A 200 (no 500)',
          (_status(b1), _status(b2)) == (200, 200), (_status(b1), _status(b2), _body(b2)))
    check('D2a the team, left with nobody, is gone', not exists, (owner, members))

    # D2b. The same pair, A first.
    a, ah = person('own')
    b, bh = person('mem')
    tid, _ = team(a, [b], owner_in_team=False)
    b1, b2, waited = race(A, delete_me(ah), delete_me(bh))
    owner, members, exists = team_state(tid)
    check('D2b A first: B waited for A', waited)
    check('D2b A first: A 200, B 200 (no 500)',
          (_status(b1), _status(b2)) == (200, 200), (_status(b1), _status(b2), _body(b2)))
    check('D2b the team went with B, its new owner and only member', not exists,
          (owner, members))

    # D4a. Legacy owner A deletes first (paused holding every lock); B, the
    # longest-standing member and so the fallback's choice, leaves meanwhile.
    a, ah = person('own')
    b, bh = person('mem')
    c_, _ch = person('mem')
    tid, _ = team(a, [b, c_], owner_in_team=False)
    mark = len(grab.lines)
    b1, b2, waited = race(A, delete_me(ah), lambda c: c.post(
        f'/api/teams/{tid}/leave', json={}, headers=bh))
    owner, members, exists = team_state(tid)
    check('D4a deletion first: the leave waited for the deletion', waited)
    check('D4a deletion first: deletion 200, leave 409 owner_must_choose (B now owns it)',
          (_status(b1), _status(b2)) == (200, 409)
          and (_body(b2) or {}).get('error') == 'owner_must_choose',
          (_status(b1), _status(b2), _body(b2)))
    check('D4a no deadlock', not deadlocks_since(mark), deadlocks_since(mark))
    check('D4a B owns the team and is still in it', owner == b and b in members,
          (owner, members))

    # D4b. B leaves first (paused holding B and the team); A deletes meanwhile.
    a, ah = person('own')
    b, bh = person('mem')
    c_, _ch = person('mem')
    tid, _ = team(a, [b, c_], owner_in_team=False)
    mark = len(grab.lines)
    b1, b2, waited = race(A, lambda c: c.post(f'/api/teams/{tid}/leave', json={}, headers=bh),
                          delete_me(ah), first_pauses_on_team=True)
    owner, members, exists = team_state(tid)
    check('D4b leave first: the deletion waited for the leave', waited)
    check('D4b leave first: leave 200, deletion 200',
          (_status(b1), _status(b2)) == (200, 200), (_status(b1), _status(b2), _body(b2)))
    check('D4b no deadlock', not deadlocks_since(mark), deadlocks_since(mark))
    check('D4b the team went to C, the one member left', owner == c_ and members == [c_],
          (owner, members))

    # R1. A sole owner deletes; someone joins with the real code meanwhile.
    o, oh = person('sole')
    j, jh = person('join')
    tid, code = team(o, [])
    b1, b2, waited = race(A, delete_me(oh), lambda c: c.post(
        f'/api/teams/{tid}/join', json={'code': code}, headers=jh))
    owner, members, exists = team_state(tid)
    check('R1 deletion first: the join waited for the deletion', waited)
    check('R1 deletion 200, join 404', (_status(b1), _status(b2)) == (200, 404),
          (_status(b1), _status(b2), _body(b2)))
    check('R1 the team is gone and nobody joined it', not exists, (owner, members))

    # R2. Two hand-overs by the same owner at once.
    o, oh = person('ownr')
    p, _ph = person('memp')
    q, _qh = person('memq')
    tid, _ = team(o, [p, q])
    b1, b2, waited = race(
        A, lambda c: c.post(f'/api/teams/{tid}/owner', json={'new_owner_user_id': p},
                            headers=oh),
        lambda c: c.post(f'/api/teams/{tid}/owner', json={'new_owner_user_id': q},
                         headers=oh),
        first_pauses_on_team=True)
    owner, members, exists = team_state(tid)
    check('R2 the second hand-over waited for the first', waited)
    check('R2 first 200, second 403 (no longer the owner)',
          (_status(b1), _status(b2)) == (200, 403), (_status(b1), _status(b2)))
    check('R2 exactly one owner: the first pick', owner == p, owner)

    A.app.logger.removeHandler(grab)
    print(json.dumps(results, indent=1))
    return 0 if all(v['ok'] for v in results.values()) else 1


def _lock_order(A, name, check, person, team, delete_me, deadlocks_since,
                team_state, grab):
    """One paused interleaving from the PR #23 follow-up review (DEF-2/DEF-3).
    No step may end in a deadlock, a 5xx or an exception."""
    db = A.db

    def call(fn):
        with A.app.app_context():
            return fn(A.app.test_client())

    def leave(tid, h):
        return lambda c: c.post(f'/api/teams/{tid}/leave', json={}, headers=h)

    def wait(gate, what):
        ok = gate['holding'].wait(20)
        check(f'{name} {what}', ok)
        return ok

    mark = len(grab.lines)
    if name in ('S5', 'S5c'):
        a, ah = person('own')
        b, bh = person('mem')
        e, eh = person('late')
        if name == 'S5':
            with A.app.app_context():
                db.session.get(A.User, e).age_band = A.AGE_ADULT
                db.session.commit()
        tid, code = team(a, [b], owner_in_team=False)
        first, second = _gate(), _gate()
        first_point = 'before_team' if name == 'S5' else 'before_subject'
        t1, b1 = _run(A, delete_me(ah), points={first_point: first, 'after_team': second})
        if not wait(first, 'the deletion paused at its first lock point'):
            first['go'].set()
            second['go'].set()
            t1.join(40)
            return
        join = call(lambda c: c.post(f'/api/teams/{tid}/join', json={'code': code},
                                     headers=eh))
        check(f'{name} E joined while the deletion was paused', join.status_code == 200,
              join.status_code)
        if name == 'S5c':
            gone = call(delete_me(bh))
            check('S5c B (the only older member) deleted meanwhile',
                  gone.status_code == 200, gone.status_code)
        first['go'].set()
        if wait(second, 'the deletion holds the team'):
            t2, b2 = _run(A, leave(tid, eh))
            time.sleep(1.5)                 # > deadlock_timeout (1s)
            check(f'{name} E\'s leave waited for the team', t2.is_alive())
        else:
            t2, b2 = None, {'exc': 'deletion never locked the team'}
        second['go'].set()
        t1.join(40)
        if t2 is not None:
            t2.join(40)
        owner, members, exists = team_state(tid)
        check(f'{name} deletion 200, E\'s leave 200 (no 503, no 500)',
              (_status(b1), _status(b2)) == (200, 200),
              (_status(b1), _status(b2), _body(b1), _body(b2)))
        check(f'{name} no deadlock', not deadlocks_since(mark), deadlocks_since(mark))
        if name == 'S5':
            check('S5 the team went to B, the one member left', exists and owner == b
                  and members == [b], (owner, members, exists))
        else:
            check('S5c the team, left with nobody, is gone', not exists,
                  (owner, members))
        with A.app.app_context():
            check(f'{name} A is gone', db.session.get(A.User, a) is None)
        return

    if name == 'S6':
        b, bh = person('mem')                # created first: the LOWER id
        a, ah = person('own')
        check('S6 B has the lower id', b < a, (b, a))
        tid, _code = team(a, [b], owner_in_team=False)
        with A.app.app_context():
            msg = A.TeamMessage(team_id=tid, sender_type='user', sender_user_id=a,
                                body='an old message')
            db.session.add(msg)
            db.session.commit()
            ref = msg.public_id
        gate = _gate()
        t1, b1 = _run(A, delete_me(ah), points={'before_subject': gate})
        if not wait(gate, 'the deletion paused at its first user lock'):
            gate['go'].set()
            t1.join(40)
            return
        t2, b2 = _run(A, lambda c: c.post('/api/reports', headers=bh, json={
            'category': 'harassment', 'subject_type': 'message', 'subject_ref': ref}))
        time.sleep(1.5)                     # > deadlock_timeout (1s)
        gate['go'].set()
        t1.join(40)
        t2.join(40)
        check('S6 report 201, deletion 409 (the report is pending)',
              (_status(b2), _status(b1)) == (201, 409),
              (_status(b2), _status(b1), _body(b2), _body(b1)))
        check('S6 no deadlock', not deadlocks_since(mark), deadlocks_since(mark))
        with A.app.app_context():
            alive = db.session.get(A.User, a) is not None
            rep = A.Report.query.filter_by(subject_ref=ref).one_or_none()
            check('S6 the report names A, who is still there',
                  alive and rep is not None and rep.reported_user_id == a,
                  (alive, rep and rep.reported_user_id))
        return

    if name == 'S1':
        # R2-NOTE-S1, the reviewer's deterministic repro: A (a legacy owner
        # who already left) deletes; A's unlocked dry run is paused right after
        # reading the members [B]; B, the only member, deletes and commits;
        # A resumes. 3b143a1 read B's account in a second statement, found
        # nobody, planned "pick" and answered 409 team_owned.
        a, ah = person('own')
        b, bh = person('mem')
        tid, _code = team(a, [b], owner_in_team=False)
        gate = _gate()
        t1, b1 = _run(A, delete_me(ah), points={'after_members': gate})
        if not wait(gate, 'A\'s dry run paused after reading the members'):
            gate['go'].set()
            t1.join(40)
            return
        gone = call(delete_me(bh))
        check('S1 B deleted (200) while A was paused', gone.status_code == 200,
              gone.status_code)
        gate['go'].set()
        t1.join(40)
        owner, members, exists = team_state(tid)
        check('S1 A 200 (no spurious 409 team_owned)', _status(b1) == 200,
              (_status(b1), _body(b1)))
        check('S1 the team, left with nobody, is gone', not exists, (owner, members))
        check('S1 no deadlock', not deadlocks_since(mark), deadlocks_since(mark))
        with A.app.app_context():
            check('S1 A is gone', db.session.get(A.User, a) is None)
        return

    check(f'unknown scenario {name}', False)


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
