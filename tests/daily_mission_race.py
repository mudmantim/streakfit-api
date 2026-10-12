"""Step 1 races on PostgreSQL: the stored mission under concurrent requests.

Run by tests/test_daily_mission_postgres.py against a freshly migrated,
disposable database (DATABASE_URL). Prints one JSON object: per scenario, the
invariant that must hold and what was observed. Every scenario is repeated on
fresh users so different interleavings get a chance to happen.

  R1 first GETs race to create the day's mission: one row, one five for all.
  R2 the same exercise from two devices: one row, nothing paid twice.
  R3 the last two exercises from two devices: one mission bonus.
  R4 easing races a completion: completed slots never move; every row is a
     slot; the loser gets an honest answer (200 or 409 mission_changed).
  R5 Brain Boost answered from four devices: one answer, one award.
  R6 effort chosen from four devices at once (D72): no 500, one row.
  R7 account deleted mid-GET, while that GET creates the day's mission: 404.
"""
import json
import os
import sys
import threading

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

REPEATS = int(os.environ.get('STREAKFIT_RACE_REPEATS', '5'))


def main():
    import app as A
    from flask_jwt_extended import create_access_token
    A.limiter.enabled = False
    A.app.config.update(TESTING=False, PROPAGATE_EXCEPTIONS=False)
    seq = [0]

    def person(skill='beginner'):
        seq[0] += 1
        with A.app.app_context():
            u = A.User(username=f'm1race_{seq[0]}', password_hash='x', skill_level=skill)
            A.db.session.add(u)
            A.db.session.commit()
            return u.id, {'Authorization': 'Bearer ' + create_access_token(identity=str(u.id))}

    def at_once(*calls):
        """Run each call(client) in its own thread, released together."""
        barrier = threading.Barrier(len(calls))
        out = [None] * len(calls)

        def run(i, fn):
            with A.app.app_context():
                c = A.app.test_client()
                barrier.wait()
                try:
                    r = fn(c)
                    out[i] = (r.status_code, r.get_json(silent=True))
                except Exception as e:          # a crash is a result, not a skip
                    out[i] = ('exception', repr(e))
        threads = [threading.Thread(target=run, args=(i, fn)) for i, fn in enumerate(calls)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(60)
        return out

    def q(fn):
        with A.app.app_context():
            return fn()

    def day():
        with A.app.app_context():
            return A.server_today()

    def daily(h):
        with A.app.app_context():
            return A.app.test_client().get('/api/daily', headers=h).get_json()

    def post_complete(key, h, md):
        return lambda c: c.post(f'/api/daily/{key}/complete', headers=h, json={'mission_date': md})

    results = {}

    def record(name, ok, detail):
        r = results.setdefault(name, {'ok': True, 'runs': []})
        r['ok'] = r['ok'] and bool(ok)
        r['runs'].append({'ok': bool(ok), **detail})

    def one_round():
        # R1
        uid, h = person()
        outs = at_once(*[(lambda c: c.get('/api/daily', headers=h))] * 8)
        keysets = {tuple(e['key'] for e in body['exercises']) for st, body in outs if st == 200}
        rows = q(lambda: A.DailyMission.query.filter_by(user_id=uid).count())
        record('R1_first_get_creates_one_mission',
               all(st == 200 for st, _ in outs) and len(keysets) == 1 and rows == 1,
               {'statuses': [st for st, _ in outs], 'distinct_fives': len(keysets), 'rows': rows})

        # R2
        uid, h = person()
        d = daily(h)
        md = d['mission_date']
        k0 = d['exercises'][0]['key']
        outs = at_once(post_complete(k0, h, md), post_complete(k0, h, md))
        rows = q(lambda: A.DailyCompletion.query.filter_by(user_id=uid).count())
        xp = q(lambda: A.db.session.get(A.User, uid).xp_total)
        fresh = [b for st, b in outs if st == 200 and not b.get('already_recorded')]
        record('R2_same_exercise_two_devices',
               all(st == 200 for st, _ in outs) and rows == 1 and len(fresh) == 1
               and xp == sum(b.get('xp_awarded', 0) for _, b in outs),
               {'statuses': [st for st, _ in outs], 'rows': rows, 'fresh': len(fresh), 'xp': xp})

        # R3
        uid, h = person()
        d = daily(h)
        md = d['mission_date']
        keys = [e['key'] for e in d['exercises']]
        for k in keys[:3]:
            with A.app.app_context():
                A.app.test_client().post(f'/api/daily/{k}/complete', headers=h, json={'mission_date': md})
        outs = at_once(post_complete(keys[3], h, md), post_complete(keys[4], h, md))
        awards = q(lambda: A.DailyMissionAward.query.filter_by(user_id=uid).count())
        bonus_events = sum(1 for _, b in outs for e in (b or {}).get('progress_events', [])
                           if e.get('event_type') == 'mission_complete')
        record('R3_last_two_exercises_two_devices_one_bonus',
               all(st == 200 for st, _ in outs) and awards == 1 and bonus_events == 1,
               {'statuses': [st for st, _ in outs], 'awards': awards, 'bonus_events': bonus_events})

        # R4
        uid, h = person('intermediate')
        d = daily(h)
        md = d['mission_date']
        keys = [e['key'] for e in d['exercises']]
        for k in keys[:3]:
            with A.app.app_context():
                A.app.test_client().post(f'/api/daily/{k}/complete', headers=h, json={'mission_date': md})
        outs = at_once(lambda c: c.put('/api/daily/effort', headers=h, json={'level': 'easy'}),
                       post_complete(keys[3], h, md))
        final = daily(h)
        slots = [e['key'] for e in final['exercises']]
        done_rows = q(lambda: [r.exercise_key for r in
                               A.DailyCompletion.query.filter_by(user_id=uid, date=day())])
        ease_st, comp_st = outs[0][0], outs[1][0]
        comp_err = (outs[1][1] or {}).get('error')
        record('R4_easing_races_a_completion',
               ease_st == 200 and slots[:3] == keys[:3]
               and set(done_rows) <= set(slots)
               and final['completed_count'] == len(done_rows)
               and (comp_st == 200 or (comp_st == 409 and comp_err == 'mission_changed')),
               {'ease': ease_st, 'complete': [comp_st, comp_err], 'rows': len(done_rows),
                'count': final['completed_count']})

        # R5
        uid, h = person()
        d = daily(h)
        md = d['mission_date']
        outs = at_once(*[(lambda c: c.post('/api/brain-boost/answer', headers=h,
                                            json={'selected_index': 0, 'mission_date': md}))] * 4)
        answers = q(lambda: A.BrainBoostAnswer.query.filter_by(user_id=uid).count())
        attempts = q(lambda: A.ProgressEvent.query.filter_by(
            user_id=uid, event_type='brain_boost_attempt').count())
        record('R5_brain_boost_four_devices',
               all(st == 200 for st, _ in outs) and answers == 1 and attempts == 1,
               {'statuses': [st for st, _ in outs], 'answers': answers, 'attempt_awards': attempts})

        # R6
        uid, h = person('intermediate')
        daily(h)
        outs = at_once(*[(lambda c, lv=lv: c.put('/api/daily/effort', headers=h, json={'level': lv}))
                         for lv in ('easy', 'usual', 'easy', 'more')])
        rows = q(lambda: A.DailyEffort.query.filter_by(user_id=uid).count())
        record('R6_effort_from_four_devices',
               all(st == 200 for st, _ in outs) and rows <= 1,
               {'statuses': [st for st, _ in outs], 'rows': rows})

    for _ in range(REPEATS):
        one_round()

    # R7 -- the account is deleted (committed, another connection) while a
    # GET is building that person's first mission of the day. The insert
    # then fails on the foreign key; there is no winner row to read back.
    # Must be 404 (the person is gone), never 500.
    from sqlalchemy import create_engine, text
    other = create_engine(os.environ['DATABASE_URL'], isolation_level='AUTOCOMMIT')
    real_build = A._build_mission
    for _ in range(REPEATS):
        uid, h = person()

        def build_then_delete(user, today, _uid=uid):
            built = real_build(user, today)
            with other.connect() as c:
                c.execute(text('DELETE FROM "user" WHERE id = :u'), {'u': _uid})
            return built
        A._build_mission = build_then_delete
        try:
            with A.app.app_context():
                r = A.app.test_client().get('/api/daily', headers=h)
        finally:
            A._build_mission = real_build
        rows = q(lambda _u=uid: A.DailyMission.query.filter_by(user_id=_u).count())
        record('R7_deleted_mid_request_is_404', r.status_code == 404 and rows == 0,
               {'status': r.status_code, 'rows': rows})
    other.dispose()

    print(json.dumps(results, default=str))


if __name__ == '__main__':
    main()
