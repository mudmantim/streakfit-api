"""The expired-photo sweep racing team and account deletion, on PostgreSQL.

PR #23 round-2 review R2-DEF-A. Run by tests/test_account_deletion_postgres.py
as a script (the app module binds its database at import), in the style of
team_owner_race.py: the REAL sweep (_sweeper_team_photo_step, through
_run_sweeper_step, exactly as the retention thread runs it) in one thread,
paused at a named statement by a cursor hook; the real route in another;
the sweep released the moment pg_stat_activity shows the route in a lock
wait ("now") or after more than deadlock_timeout ("late").

Each name below deadlocked or failed on 3b143a1 (see the round-3 notes):

  SW1   one captioned expired photo; sweep paused before its first
        `UPDATE team_message`; the sole owner leaves with delete_team.
        SW1 releases at once, SW1L after 1.5 s.     -> leave 200, team gone,
                                                       sweep ok, no deadlock
  SW2   SW1, but the sole owner deletes the account -> 200, no deadlock retry
  SW3   SW1's pause; the photo's SENDER (a member, not the owner) deletes the
        account                                     -> 200, no deadlock retry
  SW4   two captionless photos whose messages were inserted in reverse order;
        sweep paused after its first message update; sole owner leaves
                                                    -> leave 200, no deadlock
  SW5   sweep paused right after its photo SELECT; the sole owner's whole
        team deletion runs                          -> sweep ok (no
                                                       StaleDataError), leave
                                                       200 after it
  SK1   a team held FOR UPDATE (a deletion in progress) is SKIPPED, without
        waiting, while another team in the same pass is erased; once the
        holder rolls back, the NEXT pass erases it -- SKIP LOCKED never
        drops a photo that is due
  SK2   the same, but the holder commits the team's deletion: nothing left
  LH1   a legal hold still keeps a photo on this path (team hold and photo
        hold), while an unheld team in the same pass is erased
  ORD   statement order of one sweep: the team lock (FOR KEY SHARE ... SKIP
        LOCKED) comes before the first content write, and no team_photo row
        is written twice in the transaction

"No deadlock" is read from the app's own log lines (the deletion service's
retry line, the route's 40P01 handler, the sweep's failure line), not only
from the final answers: a retry would otherwise hide one.

Prints one JSON object; exit 0 only if every check held.
"""
import datetime
import json
import logging
import re
import secrets
import sys
import threading
import time
import uuid

PASSWORD = 'WalkTest123!'
_tl = threading.local()


class _Grab(logging.Handler):
    def __init__(self):
        super().__init__()
        self.lines = []

    def emit(self, record):
        try:
            self.lines.append(record.getMessage())
        except Exception:
            pass


def main(argv):
    import app as A
    from sqlalchemy import create_engine, event, text
    A.limiter.enabled = False
    A.app.config['TESTING'] = True
    db = A.db
    grab = _Grab()
    A.app.logger.addHandler(grab)
    A.app.logger.setLevel(logging.INFO)
    results = {}
    tag = secrets.token_hex(3)
    n = [0]
    with A.app.app_context():
        monitor = create_engine(db.engine.url, isolation_level='AUTOCOMMIT')

    def check(name, ok, detail=''):
        results[name] = {'ok': bool(ok), 'detail': str(detail)[:600]}

    # ── a pause at a named statement, in the sweep's thread only ──
    def _before(conn, cursor, statement, params, context, many):
        rule = getattr(_tl, 'rule', None)
        rec = getattr(_tl, 'record', None)
        if rec is not None:
            rec.append((' '.join(statement.split()), params, many))
        if rule and rule['when'] == 'before' and rule['match'](statement):
            _hold(rule)

    def _after(conn, cursor, statement, params, context, many):
        rule = getattr(_tl, 'rule', None)
        if rule and rule['when'] == 'after' and rule['match'](statement):
            _hold(rule)

    def _hold(rule):
        rule['seen'] += 1
        if rule['seen'] == rule['nth']:
            _tl.rule = None
            rule['holding'].set()
            rule['go'].wait(30)

    with A.app.app_context():
        event.listen(db.engine, 'before_cursor_execute', _before)
        event.listen(db.engine, 'after_cursor_execute', _after)

    def rule(match, nth=1, when='before'):
        return {'match': match, 'nth': nth, 'when': when, 'seen': 0,
                'holding': threading.Event(), 'go': threading.Event()}

    def starts(prefix):
        return lambda s: ' '.join(s.split()).upper().startswith(prefix.upper())

    def sweep_thread(pause=None, record=None):
        box = {}

        def target():
            _tl.rule, _tl.record = pause, record
            try:
                box['ok'] = A._run_sweeper_step(
                    'team photo sweep', A._sweeper_team_photo_step,
                    lambda exc: A._record_retention_failure(
                        A.RETENTION_TEAM_PHOTO, A.SOURCE_THREAD, exc))
            finally:
                _tl.rule = _tl.record = None
        t = threading.Thread(target=target)
        t.start()
        return t, box

    def request_thread(fn):
        box = {}

        def target():
            try:
                with A.app.app_context():
                    box['resp'] = fn(A.app.test_client())
            except Exception as exc:         # a 500 surfaces here in TESTING mode
                box['exc'] = repr(exc)[:300]
        t = threading.Thread(target=target)
        t.start()
        return t, box

    def status(box):
        return box['resp'].status_code if 'resp' in box else box.get('exc', 'no answer')

    def lock_waiters():
        with monitor.connect() as c:
            return c.execute(text(
                "select count(*) from pg_stat_activity where datname = current_database() "
                "and wait_event_type = 'Lock'")).scalar()

    def wait_for_lock_wait(thread, timeout=10):
        end = time.time() + timeout
        while time.time() < end:
            if lock_waiters():
                return True
            if not thread.is_alive():
                return False
            time.sleep(0.01)
        return False

    def last_run():
        with A.app.app_context():
            r = A.RetentionRun.query.filter_by(kind=A.RETENTION_TEAM_PHOTO).order_by(
                A.RetentionRun.id.desc()).first()
            return (r.outcome, r.error_type) if r else None

    def bad_lines(mark):
        return [m for m in grab.lines[mark:]
                if 'deadlock' in m or 'lock_timeout' in m or 'sweep failed' in m]

    # ── fixtures (rows written directly, as team_owner_race.py does) ──
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

    def team(owner, members=()):
        with A.app.app_context():
            t = A.Team(name=f'sweep {owner}', created_by_user_id=owner)
            db.session.add(t)
            db.session.flush()
            db.session.add(A.TeamInviteCode(team_id=t.id, code=A._generate_team_invite_code()))
            for uid in [owner, *members]:
                db.session.add(A.TeamMembership(team_id=t.id, user_id=uid))
            db.session.commit()
            return t.id

    def photos(tid, sender, captions, reverse_messages=False):
        """Expired photos, each with its chat message (and, when captioned,
        an old caption-carrying moment). Returns the photo ids."""
        when = datetime.datetime.utcnow() - datetime.timedelta(days=40)
        with A.app.app_context():
            rows = []
            for cap in captions:
                p = A.TeamPhoto(public_id=uuid.uuid4().hex, team_id=tid, sender_user_id=sender,
                                caption=cap, image_data=b'pixels', byte_size=6,
                                created_at=when, expires_at=when + datetime.timedelta(days=30))
                db.session.add(p)
                db.session.flush()
                rows.append(p)
            for p in (reversed(rows) if reverse_messages else rows):
                db.session.add(A.TeamMessage(team_id=tid, sender_type='user',
                                             sender_user_id=sender, body=p.caption or '',
                                             photo_id=p.id))
                db.session.flush()      # one INSERT at a time: heap order = this order
                if p.caption:
                    db.session.add(A.TeamMoment(
                        team_id=tid, moment_type='photo_shared', subject_user_id=sender,
                        moment_metadata=json.dumps({"caption": p.caption[:60]})))
            db.session.commit()
            return [p.id for p in rows]

    def erased(pid):
        with A.app.app_context():
            p = db.session.get(A.TeamPhoto, pid)
            return p is None or (p.image_data is None and p.caption is None and p.byte_size == 0)

    def team_gone(tid):
        with A.app.app_context():
            return db.session.get(A.Team, tid) is None

    def user_gone(uid):
        with A.app.app_context():
            return db.session.get(A.User, uid) is None

    def drain():
        """Erase whatever earlier scenarios left, so each starts clean."""
        for _ in range(5):
            t, box = sweep_thread()
            t.join(30)
            with A.app.app_context():
                left = A.TeamPhoto.query.filter(
                    A.TeamPhoto.expires_at <= datetime.datetime.utcnow(),
                    A.TeamPhoto.image_data.isnot(None)).count()
            if not left:
                return

    def leave_delete(tid, h):
        return lambda c: c.post(f'/api/teams/{tid}/leave', json={'delete_team': True}, headers=h)

    def delete_me(h):
        return lambda c: c.delete('/api/me', json={'password': PASSWORD}, headers=h)

    # ── the paused interleavings ──
    def paused(name, pause, make_request, release='now', expect_wait=True):
        mark = len(grab.lines)
        st, sbox = sweep_thread(pause)
        if not pause['holding'].wait(20):
            pause['go'].set()
            st.join(30)
            check(f'{name} the sweep reached its pause point', False)
            return None
        rt, rbox = request_thread(make_request)
        waited = wait_for_lock_wait(rt)
        if release == 'late':
            time.sleep(1.5)                  # > deadlock_timeout (1 s)
        pause['go'].set()
        st.join(40)
        rt.join(40)
        if expect_wait:
            check(f'{name} the request waited for the sweep (release {release})', waited)
        check(f'{name} the sweep committed (retention_run ok)',
              sbox.get('ok') is True and last_run() == ('ok', None), (sbox, last_run()))
        check(f'{name} no deadlock, lock timeout or sweep failure in the log',
              not bad_lines(mark), bad_lines(mark))
        return rbox

    only = set(argv) if argv else None

    def want(name):
        return only is None or name in only

    for name, release in (('SW1', 'now'), ('SW1L', 'late')):
        if not want(name):
            continue
        drain()
        o, oh = person('swo')
        tid = team(o)
        (pid,) = photos(tid, o, ['beach day'])
        rbox = paused(name, rule(starts('UPDATE team_message')), leave_delete(tid, oh), release)
        if rbox is not None:
            check(f'{name} leave 200 and the team is gone', status(rbox) == 200 and team_gone(tid),
                  (status(rbox), team_gone(tid)))

    if want('SW2'):
        drain()
        o, oh = person('swa')
        tid = team(o)
        photos(tid, o, ['beach day'])
        rbox = paused('SW2', rule(starts('UPDATE team_message')), delete_me(oh))
        if rbox is not None:
            check('SW2 the sole owner\'s deletion 200; user and team gone',
                  status(rbox) == 200 and user_gone(o) and team_gone(tid),
                  (status(rbox), user_gone(o), team_gone(tid)))

    if want('SW3'):
        drain()
        o, _oh = person('swk')
        s, sh = person('sws')
        tid = team(o, [s])
        (pid,) = photos(tid, s, ['beach day'])
        rbox = paused('SW3', rule(starts('UPDATE team_message')), delete_me(sh))
        if rbox is not None:
            check('SW3 the sender\'s deletion 200; the photo erased; the team stays',
                  status(rbox) == 200 and user_gone(s) and erased(pid) and not team_gone(tid),
                  (status(rbox), user_gone(s), erased(pid), team_gone(tid)))

    if want('SW4'):
        drain()
        o, oh = person('swr')
        tid = team(o)
        photos(tid, o, [None, None], reverse_messages=True)
        rbox = paused('SW4', rule(starts('UPDATE team_message'), nth=2), leave_delete(tid, oh))
        if rbox is not None:
            check('SW4 leave 200 and the team is gone', status(rbox) == 200 and team_gone(tid),
                  status(rbox))

    if want('SW5'):
        drain()
        o, oh = person('swx')
        tid = team(o)
        photos(tid, o, ['beach day', None])
        # Right after the photo batch SELECT, before any write.
        rbox = paused('SW5', rule(starts('SELECT team_photo.id AS team_photo_id'), when='after'),
                      leave_delete(tid, oh))
        if rbox is not None:
            check('SW5 the deletion 200 after the sweep; the team is gone',
                  status(rbox) == 200 and team_gone(tid), status(rbox))

    for name in ('SK1', 'SK2'):
        if not want(name):
            continue
        drain()
        o1, _ = person('sk1')
        o2, _ = person('sk2')
        t1, t2 = team(o1), team(o2)
        (p1,) = photos(t1, o1, ['held by a deletion'])
        (p2,) = photos(t2, o2, ['free'])
        holder = monitor.connect().execution_options(isolation_level='READ COMMITTED')
        tx = holder.begin()
        holder.execute(text('select id from team where id = :t for update'), {'t': t1})
        mark = len(grab.lines)
        started = time.time()
        st, sbox = sweep_thread()
        st.join(10)
        took = time.time() - started
        check(f'{name} the sweep did not wait for the locked team', not st.is_alive() and took < 2,
              round(took, 3))
        check(f'{name} pass 1: the locked team skipped, the other team erased',
              sbox.get('ok') is True and not erased(p1) and erased(p2), (sbox, erased(p1), erased(p2)))
        if name == 'SK1':
            tx.rollback()                    # the deletion gave up (say a 503)
        else:
            with A.app.app_context():
                pass
            for table, col in (('team_message', 'team_id'), ('team_moment', 'team_id'),
                               ('team_photo', 'team_id'), ('team_invite_code', 'team_id'),
                               ('team_membership', 'team_id'), ('team', 'id')):
                holder.execute(text(f'delete from {table} where {col} = :t'), {'t': t1})
            tx.commit()
        holder.close()
        st, sbox = sweep_thread()
        st.join(30)
        if name == 'SK1':
            check('SK1 pass 2: the skipped team\'s photo is erased',
                  sbox.get('ok') is True and erased(p1) and not team_gone(t1), (sbox, erased(p1)))
        else:
            check('SK2 pass 2: nothing left of the deleted team; the sweep ok',
                  sbox.get('ok') is True and team_gone(t1) and last_run() == ('ok', None),
                  (sbox, team_gone(t1), last_run()))
        check(f'{name} no deadlock or sweep failure in the log', not bad_lines(mark), bad_lines(mark))

    if want('LH1'):
        drain()
        o1, _ = person('lh1')
        o2, _ = person('lh2')
        o3, _ = person('lh3')
        t1, t2, t3 = team(o1), team(o2), team(o3)
        (p1,) = photos(t1, o1, ['team held'])
        (p2,) = photos(t2, o2, ['photo held'])
        (p3,) = photos(t3, o3, ['free'])
        with A.app.app_context():
            ref2 = db.session.get(A.TeamPhoto, p2).public_id
            for kw in ({'team_id': t1, 'subject_type': 'user', 'subject_ref': None},
                       {'team_id': None, 'subject_type': 'photo', 'subject_ref': ref2}):
                db.session.add(A.Report(public_id=uuid.uuid4().hex, category='harassment',
                                        status='closed', legal_hold=True,
                                        legal_hold_reason='test', **kw))
            db.session.commit()
        for i in (1, 2):
            st, sbox = sweep_thread()
            st.join(30)
            check(f'LH1 pass {i}: both held photos kept, the free one erased',
                  sbox.get('ok') is True and not erased(p1) and not erased(p2) and erased(p3),
                  (sbox, erased(p1), erased(p2), erased(p3)))
        with A.app.app_context():
            r = A.RetentionRun.query.filter_by(kind=A.RETENTION_TEAM_PHOTO).order_by(
                A.RetentionRun.id.desc()).first()
            check('LH1 the run records them as held', r.detail.endswith('held=2'), r.detail)
            A.Report.query.filter(A.Report.legal_hold.is_(True)).update(
                {A.Report.legal_hold: False}, synchronize_session=False)
            db.session.commit()

    if want('ORD'):
        drain()
        o, _ = person('ord')
        o2, _ = person('ord')
        t1, t2 = team(o), team(o2)
        ids = photos(t1, o, ['a', None, 'b']) + photos(t2, o2, ['c', None])
        rec = []
        st, sbox = sweep_thread(record=rec)
        st.join(30)
        writes = []
        for sql, params, many in rec:
            if sql.upper().startswith('UPDATE TEAM_PHOTO'):
                for p in (params if many else [params]):
                    writes.append(p.get('team_photo_id') if isinstance(p, dict) else p[-1])
        lock = [i for i, (sql, _p, _m) in enumerate(rec)
                if re.search(r'FOR KEY SHARE OF team SKIP LOCKED$', sql)]
        first_write = next((i for i, (sql, _p, _m) in enumerate(rec)
                            if sql.upper().startswith(('UPDATE', 'DELETE', 'INSERT'))), None)
        check('ORD the sweep ok and every photo erased',
              sbox.get('ok') is True and all(erased(p) for p in ids))
        check('ORD the team lock (FOR KEY SHARE ... SKIP LOCKED) precedes the first write',
              lock and first_write is not None and lock[0] < first_write, (lock, first_write))
        check('ORD no team_photo row written twice', sorted(writes) == sorted(ids), writes)

    A.app.logger.removeHandler(grab)
    monitor.dispose()
    print(json.dumps(results, indent=1))
    return 0 if results and all(v['ok'] for v in results.values()) else 1


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
