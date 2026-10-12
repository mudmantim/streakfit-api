"""PW-1 races on PostgreSQL: password changes under concurrent requests.

Run by tests/test_password_change_postgres.py against a freshly migrated,
disposable database (DATABASE_URL). Prints one JSON object: per scenario, the
invariant that must hold and what was observed. Each scenario is repeated on
fresh users so different interleavings get a chance to happen.

  P1 two changes at once from two devices, same current password: one wins,
     the other is refused (403); no 500; the version moved exactly once; the
     winner's password is the one that works.
  P2 a change races the account's deletion: no 500; either the account is
     gone, or it exists with the new password and the deletion was refused.
  P3 a change races ordinary requests on another device's token: no 500;
     after the change that token is refused (401 session_ended).
"""
import json
import os
import sys
import threading

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

REPEATS = int(os.environ.get('STREAKFIT_RACE_REPEATS', '5'))
PASSWORD = 'Race-Original-Password'


def main():
    import app as A
    from werkzeug.security import check_password_hash, generate_password_hash
    A.limiter.enabled = False
    A.app.config.update(TESTING=False, PROPAGATE_EXCEPTIONS=False)
    pw_hash = generate_password_hash(PASSWORD, method='pbkdf2:sha256')
    seq = [0]

    def person():
        seq[0] += 1
        with A.app.app_context():
            u = A.User(username=f'pwrace_{os.getpid()}_{seq[0]}', password_hash=pw_hash)
            A.db.session.add(u)
            A.db.session.commit()
            return u.id, A._issue_access_token(u)

    def h(token):
        return {'Authorization': 'Bearer ' + token}

    def at_once(*calls):
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

    def row(uid):
        with A.app.app_context():
            u = A.db.session.get(A.User, uid)
            return None if u is None else (u.password_hash, u.token_version)

    def change(token, new):
        return lambda c: c.post('/api/me/password', headers=h(token),
                                json={'current_password': PASSWORD, 'new_password': new})

    results = {}

    def record(name, ok, detail):
        results.setdefault(name, {'ok': True, 'runs': []})
        results[name]['runs'].append(detail)
        results[name]['ok'] = results[name]['ok'] and ok

    for _ in range(REPEATS):
        # P1
        uid, tok = person()
        out = at_once(change(tok, 'Winner-Is-Kettle-One'), change(tok, 'Winner-Is-Lantern-Two'))
        codes = sorted(o[0] for o in out)
        stored, version = row(uid)
        winner = [n for n in ('Winner-Is-Kettle-One', 'Winner-Is-Lantern-Two')
                  if check_password_hash(stored, n)]
        record('P1', codes == [200, 403] and version == 1 and len(winner) == 1,
               {'codes': codes, 'version': version, 'winners': len(winner)})

        # P2
        uid, tok = person()
        out = at_once(change(tok, 'Survivor-Password-Here'),
                      lambda c, t=tok: c.delete('/api/me', headers=h(t),
                                                json={'password': PASSWORD}))
        (c_code, _), (d_code, _) = out
        state = row(uid)
        if state is None:
            ok = d_code == 200 and c_code in (200, 404, 401)
        else:
            ok = (d_code in (401, 403, 404, 409) and c_code == 200
                  and check_password_hash(state[0], 'Survivor-Password-Here'))
        record('P2', ok and 500 not in (c_code, d_code) and 'exception' not in (c_code, d_code),
               {'change': c_code, 'delete': d_code, 'exists': state is not None})

        # P3
        uid, tok = person()
        with A.app.app_context():
            other = A._issue_access_token(A.db.session.get(A.User, uid))
        out = at_once(change(tok, 'Third-Race-Password'),
                      *[lambda c, t=other: c.get('/api/me', headers=h(t)) for _ in range(3)])
        codes = [o[0] for o in out]
        with A.app.app_context():
            after = A.app.test_client().get('/api/me', headers=h(other))
        record('P3', codes[0] == 200 and all(c in (200, 401) for c in codes[1:])
               and after.status_code == 401
               and after.get_json().get('error') == 'session_ended',
               {'codes': codes, 'after': after.status_code})

    print(json.dumps(results))


if __name__ == '__main__':
    main()
