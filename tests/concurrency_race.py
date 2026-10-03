"""Deterministic PostgreSQL concurrency scenarios for RC-B2a (D26-D32).

Run by tests/test_concurrency_postgres.py as a script: the app binds its
database at import, and the suite's own copy is bound to SQLite.

HOW A SCENARIO PROVES ORDERING (not just an outcome)

Two real requests run in two threads (race-T1, race-T2), each with its own
app context and database connection. Every pooled connection records its
PostgreSQL backend pid against the thread that checked it out.

T1 is paused by a STATEMENT PROBE: just before (or just after) the first SQL
statement whose text matches a pattern, in T1's thread only. Probes are set
on SQL text, not on application hooks, so the same scenario runs unchanged
against the old code and the repaired code.

While T1 is held, T2 starts, and an independent observer connection watches
PostgreSQL itself:

  t2_blocked_by_t1     pg_stat_activity says T2 is waiting on a Lock, and
                       pg_blocking_pids(T2) contains T1's backend. The
                       statement T2 is waiting in and the relation it is
                       waiting on are recorded, so a scenario can require the
                       INTENDED wait (e.g. T2 parked on its FOR NO KEY UPDATE
                       of "user") rather than any wait at all.
  t2_completed         T2 finished while T1 was still held: no serialization.
  blocked_by_other     T2 waits on a lock held by somebody else.
  hang                 none of the above before the safety deadline. Always a
                       failure, reported with a pg_stat_activity dump.

A timeout is never a success condition. Every wait has a hard deadline and
every deadline produces a failure with evidence.

Prints one JSON object {scenario: result}; each result carries `fixed_ok`,
the verdict for the REPAIRED behaviour, plus all raw evidence.
"""
import json
import os
import re
import sys
import threading
import time
import traceback

from sqlalchemy import create_engine, event, text

SAFETY_S = 20.0
POLL_S = 0.02

_probe = threading.local()
_pids = {}                      # thread name -> current backend pid
_pids_lock = threading.Lock()


# ── harness ───────────────────────────────────────────────────────────────────

def install(engine):
    @event.listens_for(engine, 'checkout')
    def _record_pid(dbapi_conn, _rec, _proxy):
        cur = dbapi_conn.cursor()
        cur.execute('select pg_backend_pid()')
        pid = cur.fetchone()[0]
        cur.close()
        dbapi_conn.rollback()
        with _pids_lock:
            _pids[threading.current_thread().name] = pid

    def _maybe_hold(statement, when):
        p = getattr(_probe, 'cfg', None)
        if not p or p['fired'] or p['when'] != when:
            return
        if re.search(p['pattern'], statement, re.I | re.S):
            p['fired'] = True
            p['statement'] = statement[:300]
            p['holding'].set()
            if not p['go'].wait(SAFETY_S * 2):
                p['released_by_timeout'] = True

    @event.listens_for(engine, 'before_cursor_execute')
    def _before(_c, _cur, statement, _params, _ctx, _many):
        _maybe_hold(statement, 'before')

    @event.listens_for(engine, 'after_cursor_execute')
    def _after(_c, _cur, statement, _params, _ctx, _many):
        _maybe_hold(statement, 'after')


def _thread(A, name, fn, probe=None):
    box = {}

    def target():
        _probe.cfg = probe
        try:
            with A.app.app_context():
                resp = fn(A.app.test_client())
                box['status'] = resp.status_code
                box['body'] = (resp.get_json(silent=True) or {})
        except Exception as exc:
            box['exc'] = repr(exc)[:300]
            box['trace'] = traceback.format_exc()[-1200:]
    t = threading.Thread(target=target, name=name, daemon=True)
    t.start()
    return t, box


def _observe(observer, pid1, pid2):
    with observer.connect() as c:
        row = c.execute(text(
            "select wait_event_type, wait_event, state, query, pg_blocking_pids(pid) "
            "from pg_stat_activity where pid = :p"), {'p': pid2}).first()
        if not row:
            return None
        wtype, wevent, state, query, blockers = row
        detail = {'wait_event_type': wtype, 'wait_event': wevent, 'state': state,
                  'query': (query or '')[:300], 'blockers': list(blockers or [])}
        if wtype == 'Lock':
            locks = c.execute(text(
                "select l.locktype, l.mode, l.granted, c2.relname "
                "from pg_locks l left join pg_class c2 on c2.oid = l.relation "
                "where l.pid = :p and (l.locktype in ('tuple','relation','transactionid'))"),
                {'p': pid2}).all()
            detail['locks'] = [dict(locktype=a, mode=b, granted=g, relname=r)
                               for a, b, g, r in locks]
            rels = [lk['relname'] for lk in detail['locks']
                    if lk['locktype'] == 'tuple' and lk['relname']]
            detail['waiting_relation'] = rels[0] if rels else None
        return detail


def _activity_dump(observer):
    with observer.connect() as c:
        return [dict(r._mapping) for r in c.execute(text(
            "select pid, application_name, state, wait_event_type, wait_event, "
            "left(query, 200) as query, pg_blocking_pids(pid) as blockers "
            "from pg_stat_activity where datname = current_database()")).all()]


def race(A, observer, first, second, pattern, when='before'):
    """Hold T1 at its first statement matching `pattern`; start T2; classify
    T2 from PostgreSQL's own view; release; join. Returns the evidence."""
    probe = {'pattern': pattern, 'when': when, 'fired': False,
             'holding': threading.Event(), 'go': threading.Event()}
    out = {'probe': {'pattern': pattern, 'when': when}}
    t1, b1 = _thread(A, 'race-T1', first, probe)
    if not probe['holding'].wait(SAFETY_S):
        probe['go'].set()
        t1.join(SAFETY_S)
        out.update(outcome='probe_never_reached', t1=b1)
        return out
    out['probe']['statement'] = probe.get('statement')
    pid1 = _pids.get('race-T1')
    t2, b2 = _thread(A, 'race-T2', second)
    deadline = time.monotonic() + SAFETY_S
    outcome, seen = 'hang', None
    while time.monotonic() < deadline:
        if not t2.is_alive():
            outcome = 't2_completed'
            break
        pid2 = _pids.get('race-T2')
        if pid2:
            seen = _observe(observer, pid1, pid2)
            if seen and seen['wait_event_type'] == 'Lock' and seen['blockers']:
                outcome = 't2_blocked_by_t1' if pid1 in seen['blockers'] else 'blocked_by_other'
                break
        time.sleep(POLL_S)
    if outcome == 'hang':
        out['activity'] = _activity_dump(observer)
    out.update(outcome=outcome, t2_wait=seen, pid1=pid1, pid2=_pids.get('race-T2'))
    probe['go'].set()
    t1.join(SAFETY_S)
    t2.join(SAFETY_S)
    out['t1'], out['t2'] = b1, b2
    out['joined'] = not t1.is_alive() and not t2.is_alive()
    out['t1_released_by_timeout'] = bool(probe.get('released_by_timeout'))
    return out


def intended_wait(ev, relation, statement_pattern):
    """The repaired-code ordering claim: T2 parked on T1's lock, on `relation`,
    inside a statement matching `statement_pattern`."""
    w = ev.get('t2_wait') or {}
    return (ev.get('outcome') == 't2_blocked_by_t1'
            and w.get('waiting_relation') == relation
            and bool(re.search(statement_pattern, w.get('query') or '', re.I | re.S)))


def no_5xx(ev):
    return all(b.get('status', 500) < 500 and 'exc' not in b for b in (ev['t1'], ev['t2']))


# ── scenario fixtures (direct rows: no rate limits, no API noise) ─────────────

def main():
    import app as A
    from flask_jwt_extended import create_access_token
    from werkzeug.security import generate_password_hash
    A.limiter.enabled = False
    A.app.config.update(TESTING=False, PROPAGATE_EXCEPTIONS=False)
    observer = create_engine(os.environ['DATABASE_URL'], isolation_level='AUTOCOMMIT')
    with A.app.app_context():
        install(A.db.engine)
    PW = 'RaceTest123!'
    pw_hash = generate_password_hash(PW)
    results = {}
    seq = [0]

    def person(prefix, **cols):
        seq[0] += 1
        with A.app.app_context():
            u = A.User(username=f'race_{prefix}_{seq[0]}', password_hash=pw_hash, **cols)
            A.db.session.add(u)
            A.db.session.commit()
            return u.id, {'Authorization': 'Bearer ' + create_access_token(identity=str(u.id))}

    def sql(stmt, **params):
        with A.app.app_context():
            r = A.db.session.execute(text(stmt), params)
            A.db.session.commit()
            try:
                return r.all()
            except Exception:
                return None

    def scalar(stmt, **params):
        with A.app.app_context():
            return A.db.session.execute(text(stmt), params).scalar()

    def todays_keys(h):
        with A.app.app_context():
            r = A.app.test_client().get('/api/daily', headers=h)
            return [e['key'] for e in r.get_json()['exercises']]

    def team_with(owner_id, member_ids, campfire=0):
        with A.app.app_context():
            t = A.Team(name=f'race team {seq[0]}', created_by_user_id=owner_id)
            A.db.session.add(t)
            A.db.session.flush()
            for uid in [owner_id, *member_ids]:
                A.db.session.add(A.TeamMembership(team_id=t.id, user_id=uid))
            A.db.session.add(A.TeamCampfire(team_id=t.id, total_team_missions=campfire))
            A.db.session.add(A.TeamInviteCode(team_id=t.id, code=f'R{seq[0]:05d}'[:8]))
            A.db.session.commit()
            return t.id, f'R{seq[0]:05d}'[:8]

    def seed_completions(uid, keys):
        today = scalar('select current_date')  # same server, same clock as the app
        for k in keys:
            sql('insert into daily_completion (user_id, date, exercise_key, completed_at) '
                'values (:u, :d, :k, now())', u=uid, d=A.date.today(), k=k)
        return today

    def ledger(uid):
        return sql('select coalesce(sum(xp_delta),0), coalesce(sum(acorn_delta),0) '
                   'from progress_event where user_id = :u', u=uid)[0]

    def totals(uid):
        return sql('select xp_total, acorns_total, acorns_spent from "user" where id = :u', u=uid)[0]

    def run(name, fn):
        try:
            results[name] = fn()
        except Exception:
            results[name] = {'fixed_ok': False, 'error': traceback.format_exc()[-2000:]}

    LOCK_USER = r'FOR (NO KEY )?UPDATE'

    # D26 — two different acorn filters bought together with too few acorns.
    def d26():
        uid, h = person('d26')
        sql('update "user" set acorns_total = 40, acorns_spent = 0 where id = :u', u=uid)
        ev = race(A, observer,
                  lambda c: c.post('/api/photo-filters/frosty/unlock', headers=h),
                  lambda c: c.post('/api/photo-filters/golden_hour/unlock', headers=h),
                  r'INSERT INTO user_filter_unlock')
        total, earned, spent = 0, *totals(uid)[1:]
        cost = scalar('select coalesce(sum(acorns_spent),0) from user_filter_unlock where user_id=:u', u=uid)
        inv = {'spent_le_total': spent <= earned, 'spent_eq_unlocks': spent == cost,
               'granted_le_earned': cost <= earned, 'no_5xx': no_5xx(ev)}
        ev.update(state={'acorns_total': earned, 'acorns_spent': spent, 'unlock_cost_sum': cost},
                  invariants=inv,
                  intended_wait=intended_wait(ev, 'user', LOCK_USER))
        ev['fixed_ok'] = all(inv.values()) and ev['intended_wait'] and ev['joined']
        return ev

    # D27a — two different first-time exercises at once: totals vs ledger.
    def d27a():
        uid, h = person('d27a')
        keys = todays_keys(h)
        ev = race(A, observer,
                  lambda c: c.post(f'/api/daily/{keys[0]}/complete', headers=h),
                  lambda c: c.post(f'/api/daily/{keys[1]}/complete', headers=h),
                  r'UPDATE "user" SET .*xp_total', when='after')
        (xp, ac, _sp), (lxp, lac) = totals(uid), ledger(uid)
        inv = {'xp_eq_ledger': xp == lxp, 'acorns_eq_ledger': ac == lac, 'no_5xx': no_5xx(ev),
               'both_rows': scalar('select count(*) from daily_completion where user_id=:u', u=uid) == 2}
        ev.update(state={'xp_total': xp, 'ledger_xp': lxp, 'acorns_total': ac, 'ledger_acorns': lac},
                  invariants=inv, intended_wait=intended_wait(ev, 'user', LOCK_USER))
        ev['fixed_ok'] = all(inv.values()) and ev['intended_wait'] and ev['joined']
        return ev

    # D27b — the 4th and 5th exercise at once: the mission bonus exactly once.
    def d27b():
        uid, h = person('d27b')
        keys = todays_keys(h)
        seed_completions(uid, keys[:3])
        ev = race(A, observer,
                  lambda c: c.post(f'/api/daily/{keys[3]}/complete', headers=h),
                  lambda c: c.post(f'/api/daily/{keys[4]}/complete', headers=h),
                  r'count\(daily_completion\.id\).*daily_completion\.date')
        n = {t: scalar("select count(*) from progress_event where user_id=:u and event_type=:t", u=uid, t=t)
             for t in ('mission_complete', 'perfect_mission')}
        (xp, ac, _sp), (lxp, lac) = totals(uid), ledger(uid)
        inv = {'mission_bonus_once': n['mission_complete'] == 1,
               'perfect_bonus_once': n['perfect_mission'] == 1,
               'xp_eq_ledger': xp == lxp, 'acorns_eq_ledger': ac == lac, 'no_5xx': no_5xx(ev)}
        ev.update(state={**n, 'xp_total': xp, 'ledger_xp': lxp}, invariants=inv,
                  intended_wait=intended_wait(ev, 'user', LOCK_USER))
        ev['fixed_ok'] = all(inv.values()) and ev['intended_wait'] and ev['joined']
        return ev

    # D28 — two joiners for the last seat of a free team.
    def d28():
        owner, _ = person('d28o')
        members = [person(f'd28m{i}')[0] for i in range(6)]
        team_id, code = team_with(owner, members)       # 7 of 8
        (_a, ha), (_b, hb) = person('d28a'), person('d28b')
        ev = race(A, observer,
                  lambda c: c.post(f'/api/teams/{team_id}/join', json={'code': code}, headers=ha),
                  lambda c: c.post(f'/api/teams/{team_id}/join', json={'code': code}, headers=hb),
                  r'INSERT INTO team_membership')
        count = scalar('select count(*) from team_membership where team_id=:t', t=team_id)
        inv = {'at_most_cap': count <= 8, 'exactly_one_joined': count == 8, 'no_5xx': no_5xx(ev)}
        ev.update(state={'members': count}, invariants=inv,
                  intended_wait=intended_wait(ev, 'team', r'FOR UPDATE'))
        ev['fixed_ok'] = all(inv.values()) and ev['intended_wait'] and ev['joined']
        return ev

    # D29 — the same exercise twice at once.
    def d29():
        uid, h = person('d29')
        keys = todays_keys(h)
        ev = race(A, observer,
                  lambda c: c.post(f'/api/daily/{keys[0]}/complete', headers=h),
                  lambda c: c.post(f'/api/daily/{keys[0]}/complete', headers=h),
                  r'INSERT INTO daily_completion')
        rows = scalar('select count(*) from daily_completion where user_id=:u', u=uid)
        awards = scalar('select count(*) from progress_event where user_id=:u', u=uid)
        (xp, ac, _sp), (lxp, lac) = totals(uid), ledger(uid)
        inv = {'one_row': rows == 1, 'awarded_once': awards == 1,
               'both_200': ev['t1'].get('status') == 200 and ev['t2'].get('status') == 200,
               'xp_eq_ledger': xp == lxp, 'no_5xx': no_5xx(ev)}
        ev.update(state={'rows': rows, 'award_events': awards}, invariants=inv,
                  intended_wait=intended_wait(ev, 'user', LOCK_USER))
        ev['fixed_ok'] = all(inv.values()) and ev['intended_wait'] and ev['joined']
        return ev

    # D30 — one person joining the same team twice at once.
    def d30():
        owner, _ = person('d30o')
        team_id, code = team_with(owner, [])
        uid, h = person('d30j')
        ev = race(A, observer,
                  lambda c: c.post(f'/api/teams/{team_id}/join', json={'code': code}, headers=h),
                  lambda c: c.post(f'/api/teams/{team_id}/join', json={'code': code}, headers=h),
                  r'INSERT INTO team_membership')
        mem = scalar('select count(*) from team_membership where team_id=:t and user_id=:u', t=team_id, u=uid)
        mom = scalar("select count(*) from team_moment where team_id=:t and subject_user_id=:u "
                     "and moment_type='member_joined'", t=team_id, u=uid)
        statuses = sorted([ev['t1'].get('status'), ev['t2'].get('status')])
        inv = {'one_membership': mem == 1, 'one_moment': mom == 1,
               'statuses_200_400': statuses == [200, 400], 'no_5xx': no_5xx(ev)}
        ev.update(state={'memberships': mem, 'joined_moments': mom, 'statuses': statuses},
                  invariants=inv, intended_wait=intended_wait(ev, 'user', LOCK_USER))
        ev['fixed_ok'] = all(inv.values()) and ev['intended_wait'] and ev['joined']
        return ev

    # D31 — the same account deleted twice at once.
    def d31():
        uid, h = person('d31')
        body = {'password': PW}
        ev = race(A, observer,
                  lambda c: c.delete('/api/me', json=body, headers=h),
                  lambda c: c.delete('/api/me', json=body, headers=h),
                  r'FOR UPDATE', when='after')
        gone = scalar('select count(*) from "user" where id=:u', u=uid) == 0
        statuses = sorted([ev['t1'].get('status'), ev['t2'].get('status')])
        inv = {'deleted': gone, 'statuses_200_404': statuses == [200, 404], 'no_5xx': no_5xx(ev)}
        ev.update(state={'statuses': statuses}, invariants=inv,
                  intended_wait=intended_wait(ev, 'user', r'FOR UPDATE'))
        ev['fixed_ok'] = all(inv.values()) and ev['intended_wait'] and ev['joined']
        return ev

    # D32 — two teammates finish their missions at once across a stage line.
    def d32(start, label):
        owner, _ = person(f'{label}o')
        (a, ha), (b, hb) = person(f'{label}a'), person(f'{label}b')
        team_id, _code = team_with(owner, [a, b], campfire=start)
        ka, kb = todays_keys(ha), todays_keys(hb)
        seed_completions(a, ka[:4])
        seed_completions(b, kb[:4])
        ev = race(A, observer,
                  lambda c: c.post(f'/api/daily/{ka[4]}/complete', headers=ha),
                  lambda c: c.post(f'/api/daily/{kb[4]}/complete', headers=hb),
                  r'UPDATE team_campfire', when='after')
        total = scalar('select total_team_missions from team_campfire where team_id=:t', t=team_id)
        logs = sql("select (moment_metadata::json->>'total_team_missions')::int from team_moment "
                   "where team_id=:t and moment_type='campfire_log_added' order by 1", t=team_id)
        logs = [r[0] for r in logs]
        stages = sql("select moment_metadata::json->>'stage', "
                     "(moment_metadata::json->>'total_team_missions')::int from team_moment "
                     "where team_id=:t and moment_type='campfire_stage_reached'", t=team_id)
        stages = [list(r) for r in stages]
        rickie = {k: scalar("select count(*) from team_message where team_id=:t and sender_type='rickie' "
                            "and body = any(:b)", t=team_id, b=list(A.RICKIE_TEAM_MESSAGES[k]))
                  for k in ('first_log', 'campfire_stage_reached')}
        expect_logs = [start + 1, start + 2]
        crossed = [(n, s) for n, s in A.CAMPFIRE_STAGE_THRESHOLDS
                   if start < n <= start + 2]
        inv = {
            'total_plus_two': total == start + 2,
            'logs_consecutive_unique': logs == expect_logs,
            'one_stage_moment_per_crossing': sorted(stages) == sorted([[s, n] for n, s in crossed]),
            'rickie_stage_message_once_per_crossing': rickie['campfire_stage_reached'] == len(crossed),
            'rickie_first_log_once': rickie['first_log'] == (1 if start == 0 else 0),
            'no_5xx': no_5xx(ev),
        }
        ev.update(state={'total': total, 'log_totals': logs, 'stage_moments': stages,
                         'rickie_messages': rickie}, invariants=inv,
                  intended_wait=intended_wait(ev, 'team_campfire', r'UPDATE team_campfire'))
        ev['fixed_ok'] = all(inv.values()) and ev['intended_wait'] and ev['joined']
        return ev

    run('D26_concurrent_filter_purchases', d26)
    run('D27a_parallel_awards_lose_updates', d27a)
    run('D27b_mission_bonus_paid_twice', d27b)
    run('D28_member_cap', d28)
    run('D29_same_exercise_twice', d29)
    run('D30_same_user_double_join', d30)
    run('D31_double_delete', d31)
    run('D32a_campfire_crossing_small_flame', lambda: d32(99, 'd32a'))
    run('D32b_campfire_first_log', lambda: d32(0, 'd32b'))
    print(json.dumps(results, default=str))
    sys.exit(0 if all(r.get('fixed_ok') for r in results.values()) else 1)


if __name__ == '__main__':
    main()
