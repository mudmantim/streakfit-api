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
        sweep paused after its message update; sole owner leaves
                                                    -> leave 200, no deadlock
  SW5   sweep paused right after its photo SELECT (the locking one since
        round 4); the sole owner's whole team deletion runs
                                                    -> sweep ok (no
                                                       StaleDataError), leave
                                                       200 after it
  SK1   a team held FOR UPDATE (a deletion in progress) is SKIPPED, without
        waiting, while another team in the same pass is erased; once the
        holder rolls back, the NEXT pass erases it -- SKIP LOCKED never
        drops a photo that is due
  SK2   the same, but the holder commits the team's deletion: nothing left
  LH1   a legal hold still keeps a photo on this path (team hold and photo
        hold), while an unheld team in the same pass is erased
  ORD   statement order of one sweep: in every erase transaction the
        legal-hold fence, then the team lock (FOR KEY SHARE ... SKIP LOCKED),
        then the photo lock (FOR NO KEY UPDATE, ORDER BY id) come before the
        first content write; a COMMIT between teams; message and moment
        writes lock in id order; no team_photo row is written twice

Round 4 (PR #23 round-3 review NEW-1, NEW-2, the mid-batch legal hold). Each
of these fails on c484ca8:

  HEAP  NEW-1: the photo SENDER has two due captioned photos stored in the
        heap in the opposite order to their ids (an early page freed by a
        deletion and reused after VACUUM). Their DELETE /api/me is paused
        after its first message write; the sweep runs; release on its lock
        wait                                        -> no deadlock, 200,
                                                       sweep ok
  NB    NEW-2: three teams with due photos; the sweep paused inside the
        SECOND team's erase transaction. The first team's sole owner leaves
        with delete_team and the third team's sole owner deletes their
        account, both while the sweep is still paused
                                                    -> both 200 without
                                                       waiting for the sweep
  CRM   a failure injected mid-team (team 2's message write, after its photo
        write)                                      -> team 1 erased whole,
                                                       team 2 and 3 intact,
                                                       run failed; the next
                                                       pass erases the rest;
                                                       nothing half-erased
  CRR   a failure injected mid-run, between teams 2 and 3
                                                    -> teams 1-2 erased,
                                                       team 3 intact; next
                                                       pass finishes
  CRT   one team, 5 photos, 2 per transaction; a failure in its second
        transaction                                 -> photos 1-2 erased,
                                                       3-5 intact; next pass
                                                       finishes
  LHM1  hold placed (POST .../legal-hold) and committed after the sweep has
        chosen the photo's team, before that team's erase transaction
                                                    -> photo kept
  LHM2  the sweep paused after its team lock, LHM3 after its hold check,
  LHM4  after its last erase write (before COMMIT); a hold is placed
                                                    -> the hold WAITS for the
                                                       erase to commit (photo
                                                       erased: too late by
                                                       definition), then 200
  LHM5  a hold's transaction paused before its commit; the sweep starts
                                                    -> the erase waits for
                                                       the hold, sees it,
                                                       photo kept

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

    def _commit(conn):
        rec = getattr(_tl, 'record', None)
        if rec is not None:
            rec.append(('COMMIT', None, False))

    def _after(conn, cursor, statement, params, context, many):
        rule = getattr(_tl, 'rule', None)
        if rule and rule['when'] == 'after' and rule['match'](statement):
            _hold(rule)

    def _hold(rule):
        rule['seen'] += 1
        if rule['seen'] == rule['nth']:
            _tl.rule = None
            if rule.get('raise'):
                raise RuntimeError('injected sweep failure')
            rule['holding'].set()
            rule['go'].wait(30)

    with A.app.app_context():
        event.listen(db.engine, 'before_cursor_execute', _before)
        event.listen(db.engine, 'after_cursor_execute', _after)
        event.listen(db.engine, 'commit', _commit)

    def rule(match, nth=1, when='before', fail=False):
        return {'match': match, 'nth': nth, 'when': when, 'seen': 0, 'raise': fail,
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

    def request_thread(fn, pause=None):
        box = {}

        def target():
            _tl.rule = pause
            try:
                with A.app.app_context():
                    box['resp'] = fn(A.app.test_client())
            except Exception as exc:         # a 500 surfaces here in TESTING mode
                box['exc'] = repr(exc)[:300]
            finally:
                _tl.rule = None
        t = threading.Thread(target=target)
        t.start()
        return t, box

    def status(box):
        return box['resp'].status_code if 'resp' in box else box.get('exc', 'no answer')

    def lock_waiters(event_name=None):
        with monitor.connect() as c:
            return c.execute(text(
                "select count(*) from pg_stat_activity where datname = current_database() "
                "and wait_event_type = 'Lock' and (:e is null or wait_event = :e)"),
                {'e': event_name}).scalar()

    def wait_for_lock_wait(thread, timeout=10, event_name=None):
        end = time.time() + timeout
        while time.time() < end:
            if lock_waiters(event_name):
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

    def photos(tid, sender, captions, reverse_messages=False, fresh=False):
        """Expired photos (not expired: fresh=True), each with its chat
        message (and, when captioned, an old caption-carrying moment).
        Returns the photo ids."""
        when = datetime.datetime.utcnow() - datetime.timedelta(days=1 if fresh else 40)
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
        rbox = paused('SW4', rule(starts('UPDATE team_message'), when='after'),
                      leave_delete(tid, oh))
        if rbox is not None:
            check('SW4 leave 200 and the team is gone', status(rbox) == 200 and team_gone(tid),
                  status(rbox))

    if want('SW5'):
        drain()
        o, oh = person('swx')
        tid = team(o)
        photos(tid, o, ['beach day', None])
        # Right after the photo SELECT (since round 4 the one that locks the
        # photos, FOR NO KEY UPDATE), before any write.
        rbox = paused('SW5', rule(lambda s: starts('SELECT team_photo.id')(s) and 'LIMIT' in s.upper(),
                                  when='after'),
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
        sql_only = [sql for sql, _p, _m in rec]
        writes = []
        for sql, params, many in rec:
            if sql.upper().startswith('UPDATE TEAM_PHOTO'):
                for p in (params if many else [params]):
                    writes.extend(v for k, v in p.items() if re.fullmatch(r'id_\d+(_\d+)?', k))
        fences = [i for i, s_ in enumerate(sql_only) if 'pg_advisory_xact_lock_shared' in s_]
        team_locks = [i for i, s_ in enumerate(sql_only)
                      if re.search(r'FOR KEY SHARE OF team SKIP LOCKED$', s_)]
        photo_locks = [i for i, s_ in enumerate(sql_only)
                       if s_.upper().startswith('SELECT TEAM_PHOTO.ID')
                       and s_.endswith('FOR NO KEY UPDATE OF team_photo')
                       and 'ORDER BY team_photo.id' in s_]
        content_writes = [i for i, s_ in enumerate(sql_only)
                          if s_.upper().startswith(('UPDATE TEAM_', 'DELETE', 'INSERT INTO TEAM'))]
        commits = [i for i, s_ in enumerate(sql_only) if s_ == 'COMMIT']
        check('ORD the sweep ok and every photo erased',
              sbox.get('ok') is True and all(erased(p) for p in ids))
        # One erase transaction per team: fence < team lock < photo lock <
        # its writes < COMMIT < the next team's fence.
        per_team_ok = len(fences) == 2 and len(team_locks) == 2 and len(photo_locks) == 2
        if per_team_ok:
            for k in range(2):
                nxt = fences[k + 1] if k + 1 < len(fences) else len(sql_only)
                mine = [i for i in content_writes if fences[k] < i < nxt]
                per_team_ok &= bool(mine) and fences[k] < team_locks[k] < photo_locks[k] < mine[0]
                per_team_ok &= any(mine[-1] < c < nxt for c in commits)
        check('ORD each team: fence, team lock, photo lock (id order) before its writes, '
              'then COMMIT before the next team', per_team_ok,
              (fences, team_locks, photo_locks, content_writes, commits))
        check('ORD no team_photo row written twice', sorted(writes) == sorted(ids), writes)
        ordered = [s_ for s_ in sql_only if s_.upper().startswith(('UPDATE TEAM_MESSAGE',
                                                                   'UPDATE TEAM_MOMENT'))]
        check('ORD message and moment writes lock their rows in id order',
              ordered and all('ORDER BY' in s_ and 'FOR NO KEY UPDATE' in s_ for s_ in ordered),
              ordered)

    # ── round 4 ──
    def hold_report(o, photo_id):
        """A closed report about the photo, NOT yet held. Returns its public id."""
        with A.app.app_context():
            ref = db.session.get(A.TeamPhoto, photo_id).public_id
            r = A.Report(public_id=uuid.uuid4().hex, category='harassment', status='closed',
                         reporter_user_id=o, subject_type='photo', subject_ref=ref,
                         legal_hold=False)
            db.session.add(r)
            db.session.commit()
            return r.public_id

    import os as _os
    _os.environ['ADMIN_SECRET'] = 'sweep-race-local-admin'

    def place_hold(report_public_id):
        return lambda c: c.post(f'/api/admin/reports/{report_public_id}/legal-hold',
                                json={'hold': True, 'reason': 'sweep race'},
                                headers={'X-Admin-Secret': 'sweep-race-local-admin'})

    def release_holds():
        with A.app.app_context():
            A.Report.query.filter(A.Report.legal_hold.is_(True)).update(
                {A.Report.legal_hold: False}, synchronize_session=False)
            db.session.commit()

    def message_body(pid):
        with A.app.app_context():
            m = A.TeamMessage.query.filter_by(photo_id=pid).first()
            return None if m is None else m.body

    def whole(pids):
        """Every photo either fully erased (pixels, caption, message body) or
        fully intact -- never half: returns (erased ids, intact ids, half ids)."""
        er, intact, half = [], [], []
        with A.app.app_context():
            for pid in pids:
                db.session.expire_all()
                p = db.session.get(A.TeamPhoto, pid)
                body = message_body(pid)
                gone = p.image_data is None and p.byte_size == 0 and p.caption is None
                full = p.image_data is not None and p.byte_size != 0
                if gone and body == '':
                    er.append(pid)
                elif full and body == (p.caption or ''):
                    intact.append(pid)
                else:
                    half.append(pid)
        return er, intact, half

    if want('HEAP'):
        drain()
        o, _oh = person('hpo')
        s_, sh = person('hps')
        z, _zh = person('hpz')
        tid = team(o, [s_])
        ztid = team(z)
        filler = photos(ztid, z, [None] * 150, fresh=True)
        (p1,) = photos(tid, s_, ['first'])
        with monitor.connect() as c:
            c.execute(text('delete from team_message where photo_id = any(:ids)'), {'ids': filler})
            c.execute(text('delete from team_photo where id = any(:ids)'), {'ids': filler})
            c.exec_driver_sql('VACUUM team_photo')
            c.exec_driver_sql('VACUUM team_message')
        (p2,) = photos(tid, s_, ['second'])
        with monitor.connect() as c:
            heap = [r[0] for r in c.execute(text(
                'select id from team_photo where sender_user_id = :s'), {'s': s_})]
        check('HEAP precondition: heap order is the reverse of id order',
              heap == [p2, p1] and p1 < p2, (heap, p1, p2))
        mark = len(grab.lines)
        pause = rule(starts('UPDATE team_message'), when='after')
        dt, dbox = request_thread(delete_me(sh), pause=pause)
        if not pause['holding'].wait(20):
            pause['go'].set()
            dt.join(30)
            check('HEAP the deletion reached its pause point', False)
        else:
            st, sbox = sweep_thread()
            waited = wait_for_lock_wait(st)
            pause['go'].set()
            dt.join(40)
            st.join(40)
            check('HEAP the sweep waited for the deletion', waited)
            check('HEAP the sender\'s deletion 200, both photos erased',
                  status(dbox) == 200 and user_gone(s_) and erased(p1) and erased(p2),
                  (status(dbox), erased(p1), erased(p2)))
            check('HEAP the sweep committed (retention_run ok)',
                  sbox.get('ok') is True and last_run() == ('ok', None), (sbox, last_run()))
            check('HEAP no deadlock, retry or sweep failure in the log',
                  not bad_lines(mark), bad_lines(mark))

    if want('NB'):
        drain()
        oa, ah = person('nba')
        ob, _bh = person('nbb')
        oc, ch = person('nbc')
        ta, tb, tc = team(oa), team(ob), team(oc)
        pa = photos(ta, oa, ['a1'])
        pb = photos(tb, ob, ['b1'])
        pc = photos(tc, oc, ['c1'])
        mark = len(grab.lines)
        # Inside team B's erase transaction: after its message write.
        pause = rule(starts('UPDATE team_message'), nth=2, when='after')
        st, sbox = sweep_thread(pause)
        if not pause['holding'].wait(20):
            pause['go'].set()
            st.join(30)
            check('NB the sweep reached its pause point (inside team 2)', False)
        else:
            started = time.time()
            at, abox = request_thread(leave_delete(ta, ah))
            ct, cbox = request_thread(delete_me(ch))
            at.join(10)
            ct.join(10)
            took = time.time() - started
            answered = not at.is_alive() and not ct.is_alive()
            pause['go'].set()
            st.join(40)
            at.join(30)
            ct.join(30)
            check('NB team 1\'s leave and team 3\'s account deletion answered while the '
                  'sweep was still inside team 2', answered and took < 5, round(took, 3))
            check('NB both 200; teams 1 and 3 gone',
                  status(abox) == 200 and status(cbox) == 200 and team_gone(ta) and team_gone(tc),
                  (status(abox), status(cbox), team_gone(ta), team_gone(tc)))
            check('NB the sweep committed; team 2 erased',
                  sbox.get('ok') is True and last_run() == ('ok', None) and erased(pb[0]),
                  (sbox, last_run(), erased(pb[0])))
            check('NB no deadlock, lock timeout or sweep failure in the log',
                  not bad_lines(mark), bad_lines(mark))
        del pa, pc

    for name in ('CRM', 'CRR', 'CRT'):
        if not want(name):
            continue
        drain()
        old_chunk = getattr(A, '_TEAM_PHOTO_SWEEP_CHUNK', None)
        if name == 'CRT':
            A._TEAM_PHOTO_SWEEP_CHUNK = 2
            o, _ = person('crt')
            t1 = team(o)
            groups = [photos(t1, o, ['one', None, 'three', 'four', None])]
            fail = rule(starts('UPDATE team_message'), nth=2, fail=True)
        else:
            groups = []
            for k in range(3):
                o, _ = person(name.lower())
                groups.append(photos(team(o), o, [f'{name} {k} a', None]))
            fail = (rule(starts('UPDATE team_message'), nth=2, fail=True) if name == 'CRM'
                    else rule(lambda s: 'pg_advisory_xact_lock_shared' in s, nth=3, fail=True))
        allp = [p for g in groups for p in g]
        st, sbox = sweep_thread(fail)
        st.join(60)
        er, intact, half = whole(allp)
        if name == 'CRT':
            want_er, want_intact = groups[0][:2], groups[0][2:]
        elif name == 'CRM':
            want_er, want_intact = groups[0], groups[1] + groups[2]
        else:
            want_er, want_intact = groups[0] + groups[1], groups[2]
        check(f'{name} the failed pass is recorded as failed',
              sbox.get('ok') is False and last_run() == ('failed', 'RuntimeError'),
              (sbox, last_run()))
        check(f'{name} after the failure: committed work kept whole, the rest intact, '
              f'nothing half-erased',
              sorted(er) == sorted(want_er) and sorted(intact) == sorted(want_intact) and not half,
              {'erased': er, 'intact': intact, 'half': half, 'want_erased': want_er})
        st, sbox = sweep_thread()
        st.join(60)
        er, intact, half = whole(allp)
        check(f'{name} the next pass erases the rest; nothing half-erased',
              sbox.get('ok') is True and sorted(er) == sorted(allp) and not half,
              {'erased': er, 'intact': intact, 'half': half})
        if old_chunk is not None:
            A._TEAM_PHOTO_SWEEP_CHUNK = old_chunk

    # Legal hold committed mid-batch, at each point of one erase transaction.
    lhm_points = {
        # the statement that chose the photo's team (c484ca8: the photo SELECT)
        'LHM1': (lambda s: starts('SELECT team_photo.')(s) and 'LIMIT' in s.upper()
                 and 'COUNT(' not in s.upper(), 'after'),
        'LHM2': (lambda s: re.search(r'FOR KEY SHARE OF team SKIP LOCKED$', ' '.join(s.split())),
                 'after'),
        'LHM3': (lambda s: starts('SELECT team_photo.id')(s) and 'LIMIT' in s.upper(), 'after'),
        'LHM4': (starts('UPDATE team_moment'), 'after'),
    }
    for name in ('LHM1', 'LHM2', 'LHM3', 'LHM4', 'LHM5'):
        if not want(name):
            continue
        drain()
        release_holds()
        o, _ = person(name.lower())
        tid = team(o)
        (pid,) = photos(tid, o, ['keep me?'])
        ref = hold_report(o, pid)
        mark = len(grab.lines)
        if name != 'LHM5':
            match, when = lhm_points[name]
            pause = rule(match, when=when)
            st, sbox = sweep_thread(pause)
            if not pause['holding'].wait(20):
                pause['go'].set()
                st.join(30)
                check(f'{name} the sweep reached its pause point', False)
                continue
            ht, hbox = request_thread(place_hold(ref))
            if name == 'LHM1':
                ht.join(10)
                check('LHM1 the hold committed (200) while the sweep was paused',
                      not ht.is_alive() and status(hbox) == 200, status(hbox))
                pause['go'].set()
                st.join(40)
                check('LHM1 the photo is kept (the erase\'s own hold check saw the hold)',
                      not erased(pid) and message_body(pid) == 'keep me?',
                      (erased(pid), message_body(pid)))
            else:
                waited = wait_for_lock_wait(ht, event_name='advisory')
                still_waiting = ht.is_alive()
                pause['go'].set()
                st.join(40)
                ht.join(40)
                check(f'{name} the hold waited for the erase transaction to commit',
                      waited and still_waiting, (waited, still_waiting))
                check(f'{name} the photo was erased by that transaction; the hold then '
                      f'committed (200): too late by definition, never in between',
                      erased(pid) and status(hbox) == 200, (erased(pid), status(hbox)))
        else:
            # The hold's transaction is in progress (paused before its commit)
            # when the erase starts.
            hpause = rule(starts('INSERT INTO moderation_action'), when='after')
            ht, hbox = request_thread(place_hold(ref), pause=hpause)
            if not hpause['holding'].wait(20):
                hpause['go'].set()
                ht.join(30)
                check('LHM5 the hold reached its pause point', False)
                continue
            st, sbox = sweep_thread()
            waited = wait_for_lock_wait(st, event_name='advisory')
            erased_meanwhile = erased(pid)
            hpause['go'].set()
            ht.join(40)
            st.join(40)
            check('LHM5 the erase waited for the hold in progress', waited and not erased_meanwhile,
                  (waited, erased_meanwhile))
            check('LHM5 the hold 200 and the photo kept',
                  status(hbox) == 200 and not erased(pid) and message_body(pid) == 'keep me?',
                  (status(hbox), erased(pid), message_body(pid)))
        check(f'{name} the sweep committed (retention_run ok), no deadlock or failure',
              sbox.get('ok') is True and last_run() == ('ok', None) and not bad_lines(mark),
              (sbox, last_run(), bad_lines(mark)))
        release_holds()

    A.app.logger.removeHandler(grab)
    monitor.dispose()
    print(json.dumps(results, indent=1))
    return 0 if results and all(v['ok'] for v in results.values()) else 1


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
