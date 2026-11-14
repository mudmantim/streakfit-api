"""DEF-1 timing benchmark, PostgreSQL (PR #23 round-2 review). OPT-IN, NOT A CI GATE.

The CI gate is deterministic: test_d1_query_parity_* in test_pr23_followup.py
checks that a kept-empty team and a deleted one run the SAME SQL. This script
measures what that buys in wall time. Shared CI runners are too noisy to gate
on timing, so it runs only through tests/test_d1_timing_postgres.py when
STREAKFIT_D1_TIMING=1 (or by hand).

Two worlds, built through the real routes: a sole owner O and a member M; M
leaves; O leaves with delete_team. In KEPT, M first files a real report about
O in the team (201), so the team is kept, empty and closed; in DELETED there
is no report and the team is deleted. O then calls, against each world:

  lookup  GET  /api/teams/lookup/<the old code>   (and a never-issued code)
  rotate  POST /api/teams/<id>/rotate-invite
  join    POST /api/teams/<id>/join
  owner   POST /api/teams/<id>/owner
  remove  DELETE /api/teams/<id>/members/<M>

Every series is warmed up, then sampled in rounds with all series shuffled
into a random order each round. Per endpoint it reports the median of kept
minus the median of deleted (ms), a bootstrap 95% CI of that difference, and
the balanced accuracy of an attacker who sees ONE request with no jitter and
uses the best threshold direction at the midpoint of the two medians.

Passes, per endpoint, only when ALL hold (the proposal's thresholds):
  |delta median| <= 0.05 ms;  the 95% CI lies within +-0.05 ms;  accuracy <= 0.60.

Prints one JSON object; exit 0 only if every endpoint passes.
Usage: DATABASE_URL=<disposable PG> python tests/d1_timing_benchmark.py [samples]
"""
import gc
import json
import random
import secrets
import statistics
import sys
import time

PASSWORD = 'WalkTest123!'
THRESH_MS = 0.05
THRESH_ACC = 0.60


def _bootstrap_ci(a, b, reps=1000, seed=7):
    rng = random.Random(seed)
    diffs = []
    for _ in range(reps):
        ra = [a[rng.randrange(len(a))] for _ in a]
        rb = [b[rng.randrange(len(b))] for _ in b]
        diffs.append(statistics.median(ra) - statistics.median(rb))
    diffs.sort()
    return diffs[int(0.025 * reps)], diffs[int(0.975 * reps) - 1]


def _accuracy(kept, deleted):
    mid = (statistics.median(kept) + statistics.median(deleted)) / 2
    hit_k = sum(t > mid for t in kept) / len(kept)
    hit_d = sum(t <= mid for t in deleted) / len(deleted)
    acc = (hit_k + hit_d) / 2
    return max(acc, 1 - acc)


def main(argv):
    samples = int(argv[0]) if argv else 3000
    import app as A
    from sqlalchemy import event
    A.limiter.enabled = False
    A.app.config['TESTING'] = True
    tag = secrets.token_hex(3)
    c = A.app.test_client()

    def person(name):
        name = f'{name}{tag}'
        c.post('/api/register', json={'username': name, 'password': PASSWORD})
        tok = c.post('/api/login', json={'username': name, 'password': PASSWORD}
                     ).get_json()['access_token']
        h = {'Authorization': f'Bearer {tok}'}
        return c.get('/api/me', headers=h).get_json()['id'], h

    def world(label, report):
        o, oh = person(f'tbo{label}')
        m, mh = person(f'tbm{label}')
        t = c.post('/api/teams', json={'name': f'Bench {label}'}, headers=oh).get_json()['team']
        tid, code = t['id'], t['invite_code']
        assert c.post(f'/api/teams/{tid}/join', json={'code': code}, headers=mh).status_code == 200
        c.post(f'/api/teams/{tid}/messages', json={'body': 'hello'}, headers=oh)
        if report:
            r = c.post('/api/reports', headers=mh, json={
                'category': 'harassment', 'subject_type': 'user',
                'reported_user_id': o, 'team_id': tid})
            assert r.status_code == 201, r.get_json()
        assert c.post(f'/api/teams/{tid}/leave', json={}, headers=mh).status_code == 200
        r = c.post(f'/api/teams/{tid}/leave', json={'delete_team': True}, headers=oh)
        assert r.status_code == 200, r.get_json()
        with A.app.app_context():
            kept = A.db.session.get(A.Team, tid) is not None
        assert kept == report
        return {'tid': tid, 'code': code, 'm': m, 'h': oh}

    with A.app.app_context():
        deleted, kept = world('d', False), world('k', True)

    def calls(w):
        t = w['tid']
        return {
            'lookup': ('get', f"/api/teams/lookup/{w['code']}", None),
            'rotate': ('post', f'/api/teams/{t}/rotate-invite', None),
            'join': ('post', f'/api/teams/{t}/join', {'code': w['code']}),
            'owner': ('post', f'/api/teams/{t}/owner', {'new_owner_user_id': w['m']}),
            'remove': ('delete', f"/api/teams/{t}/members/{w['m']}", None),
        }
    series = {}
    for wname, w in (('deleted', deleted), ('kept', kept)):
        for ep, call in calls(w).items():
            series[(ep, wname)] = (call, w['h'])
    series[('lookup', 'never')] = (None, deleted['h'])

    def one(key):
        call, h = series[key]
        if call is None:          # a fresh never-issued code each time
            call = ('get', '/api/teams/lookup/Z' + secrets.token_hex(3)[:5].upper(), None)
        verb, path, body = call
        t0 = time.perf_counter()
        r = getattr(c, verb)(path, json=body, headers=h)
        dt = (time.perf_counter() - t0) * 1000
        assert r.status_code == 404, (key, r.status_code, r.get_json(silent=True))
        return dt, r.get_json(silent=True)

    # The deterministic part, on PostgreSQL: same body, same SQL text.
    parity = {}
    stmts = []

    def rec(_c, _cu, statement, _p, _ctx, _m):
        stmts.append(' '.join(statement.split()))
    with A.app.app_context():
        event.listen(A.db.engine, 'before_cursor_execute', rec)
    for ep in calls(deleted):
        seen = {}
        for wname in ('deleted', 'kept'):
            stmts.clear()
            _dt, body = one((ep, wname))
            seen[wname] = (body, list(stmts))
        parity[ep] = {'same_body': seen['deleted'][0] == seen['kept'][0],
                      'statements_deleted': len(seen['deleted'][1]),
                      'statements_kept': len(seen['kept'][1]),
                      'same_sql': seen['deleted'][1] == seen['kept'][1]}
    with A.app.app_context():
        event.remove(A.db.engine, 'before_cursor_execute', rec)

    keys = list(series)
    for _ in range(300):                   # warm-up, discarded
        for k in keys:
            one(k)
    data = {k: [] for k in keys}
    rng = random.Random(11)
    gc.collect()
    gc.disable()
    try:
        for i in range(samples):
            order = keys[:]
            rng.shuffle(order)
            for k in order:
                data[k].append(one(k)[0])
            if i % 200 == 199:
                gc.enable()
                gc.collect()
                gc.disable()
    finally:
        gc.enable()

    out = {'samples_per_series': samples, 'parity': parity, 'endpoints': {}, 'series_p50_ms': {}}
    for k, v in data.items():
        out['series_p50_ms'][f'{k[0]} {k[1]}'] = round(statistics.median(v), 4)
    ok_all = True
    for ep in calls(deleted):
        k, d = data[(ep, 'kept')], data[(ep, 'deleted')]
        delta = statistics.median(k) - statistics.median(d)
        lo, hi = _bootstrap_ci(k, d)
        acc = _accuracy(k, d)
        ok = (abs(delta) <= THRESH_MS and -THRESH_MS <= lo and hi <= THRESH_MS
              and acc <= THRESH_ACC and parity[ep]['same_sql'] and parity[ep]['same_body'])
        ok_all &= ok
        out['endpoints'][ep] = {'delta_median_ms': round(delta, 4),
                                'ci95_ms': [round(lo, 4), round(hi, 4)],
                                'accuracy_n1': round(acc, 3), 'pass': ok}
    nv = data[('lookup', 'never')]
    out['lookup_never_minus_deleted_ms'] = round(
        statistics.median(nv) - statistics.median(data[('lookup', 'deleted')]), 4)
    print(json.dumps(out, indent=1))
    return 0 if ok_all else 1


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
