"""The PW-1 rollback build keeps session security.

This build replaces PW-1 without a schema change. It has no password change,
but everything a PW-1 password change did must STAY done: a revoked session is
refused on every authenticated route, and passwords set under PW-1 (stored in
NFKC) keep signing in. The database states PW-1 leaves behind are simulated
directly: a moved token_version, an NFKC hash.
"""
import ast
import os
import re
import unicodedata

import pytest
from flask_jwt_extended import create_access_token, decode_token
from werkzeug.security import generate_password_hash

import app as appmod
from conftest import auth_headers

PW = 'WalkTest123!'


def _user(username, password=PW, version=0):
    u = appmod.User(username=username, password_hash=generate_password_hash(password, method='pbkdf2:sha256'),
                    token_version=version)
    appmod.db.session.add(u)
    appmod.db.session.commit()
    return u


def _login(client, username, password):
    return client.post('/api/login', json={'username': username, 'password': password})


def _as_pw1_left_it(u, new_password):
    """What a PW-1 password change wrote: NFKC hash, version moved."""
    u.password_hash = generate_password_hash(unicodedata.normalize('NFKC', new_password),
                                             method='pbkdf2:sha256')
    u.token_version = (u.token_version or 0) + 1
    appmod.db.session.commit()


def test_a_session_revoked_by_a_pw1_change_is_refused_on_every_authenticated_route(client):
    """Walk the whole URL map: wherever no token is a 401, a revoked one is a
    401 session_ended -- never served."""
    u = _user('compat_walk')
    stale = _login(client, 'compat_walk', PW).get_json()['access_token']
    _as_pw1_left_it(u, 'Changed-under-pw1-lantern')
    checked = 0
    for rule in appmod.app.url_map.iter_rules():
        if rule.endpoint == 'static':
            continue
        url = re.sub(r'<[^>]+>', '1', rule.rule)
        for m in sorted(rule.methods - {'HEAD', 'OPTIONS'}):
            kw = {'json': {}} if m in ('POST', 'PUT', 'PATCH', 'DELETE') else {}
            if getattr(client, m.lower())(url, **kw).status_code != 401:
                continue
            r = getattr(client, m.lower())(url, headers=auth_headers(stale), **kw)
            assert r.status_code == 401 and r.get_json()['error'] == 'session_ended', (m, rule.rule)
            checked += 1
    assert checked >= 40


def test_a_token_from_before_pw1_works_until_a_version_moves(client):
    u = _user('compat_legacy_token')
    legacy = create_access_token(identity=str(u.id))          # no `tv`: 1d7b2f2 issued these
    assert client.get('/api/me', headers=auth_headers(legacy)).status_code == 200
    _as_pw1_left_it(u, 'Changed-under-pw1-lantern')
    assert client.get('/api/me', headers=auth_headers(legacy)).status_code == 401


def test_tokens_this_build_issues_carry_the_current_version(client):
    _user('compat_issue', version=3)
    token = _login(client, 'compat_issue', PW).get_json()['access_token']
    assert decode_token(token)['tv'] == 3
    assert client.get('/api/me', headers=auth_headers(token)).status_code == 200


def test_the_version_is_read_on_every_request_not_cached_across_requests(client):
    u = _user('compat_live')
    token = _login(client, 'compat_live', PW).get_json()['access_token']
    assert client.get('/api/me', headers=auth_headers(token)).status_code == 200
    u.token_version = 1                     # e.g. PW-1, rolled forward, changed it
    appmod.db.session.commit()
    assert client.get('/api/me', headers=auth_headers(token)).status_code == 401


def test_a_deleted_accounts_token_still_answers_404(client):
    _user('compat_gone')
    token = _login(client, 'compat_gone', PW).get_json()['access_token']
    assert client.delete('/api/me', json={'password': PW}, headers=auth_headers(token)).status_code == 200
    assert client.get('/api/me', headers=auth_headers(token)).status_code == 404


def test_there_is_no_password_change_here_and_nothing_moves_a_version(client):
    _user('compat_no_change')
    token = _login(client, 'compat_no_change', PW).get_json()['access_token']
    r = client.post('/api/me/password', json={'current_password': PW, 'new_password': 'x' * 20},
                    headers=auth_headers(token))
    assert r.status_code in (404, 405)
    src = open(os.path.join(os.path.dirname(appmod.__file__), 'app.py')).read()
    tree = ast.parse(src)
    writes = [n for n in ast.walk(tree)
              if isinstance(n, (ast.Assign, ast.AugAssign))
              and any(isinstance(t, ast.Attribute) and t.attr == 'token_version'
                      for t in (n.targets if isinstance(n, ast.Assign) else [n.target]))]
    assert writes == []


def test_create_access_token_is_only_called_by_the_one_issuer():
    src = open(os.path.join(os.path.dirname(appmod.__file__), 'app.py')).read()
    callers = [fn.name for fn in ast.walk(ast.parse(src)) if isinstance(fn, ast.FunctionDef)
               for node in ast.walk(fn)
               if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
               and node.func.id == 'create_access_token']
    assert callers == ['_issue_access_token']


@pytest.mark.parametrize('pw1_password,typed', [
    ('Changed-under-pw1-lantern', 'Changed-under-pw1-lantern'),
    ('Café-lantern-morning-9', unicodedata.normalize('NFD', 'Café-lantern-morning-9')),
    ('Café-lantern-morning-9', unicodedata.normalize('NFC', 'Café-lantern-morning-9')),
    ('Ｌａｎｔｅｒｎ-kettle-99', 'Lantern-kettle-99'),
])
def test_a_password_set_under_pw1_still_signs_in_and_deletes(client, pw1_password, typed):
    u = _user('compat_nfkc')
    _as_pw1_left_it(u, pw1_password)
    r = _login(client, 'compat_nfkc', typed)
    assert r.status_code == 200
    assert client.delete('/api/me', json={'password': typed},
                         headers=auth_headers(r.get_json()['access_token'])).status_code == 200


@pytest.mark.parametrize('raw', ['Old8char', 'Ünïcødé-old', 'ｆｕｌｌ-width-old'])
def test_a_password_from_before_pw1_still_signs_in_exactly_as_typed(client, raw):
    _user('compat_raw', password=raw)
    assert _login(client, 'compat_raw', raw).status_code == 200


def test_the_old_password_is_refused_after_a_pw1_change(client):
    u = _user('compat_old_pw')
    _as_pw1_left_it(u, 'Changed-under-pw1-lantern')
    assert _login(client, 'compat_old_pw', PW).status_code == 401


def test_routes_that_load_the_caller_read_the_row_once(client):
    from sqlalchemy import event
    _user('compat_once')
    token = _login(client, 'compat_once', PW).get_json()['access_token']
    seen = []

    def spy(conn, cursor, statement, params, context, executemany):
        s = statement.replace('"', '')
        if 'FROM user' in s and 'WHERE user.id' in s:
            seen.append(s)

    event.listen(appmod.db.engine, 'before_cursor_execute', spy)
    try:
        appmod.db.session.remove()
        assert client.get('/api/me', headers=auth_headers(token)).status_code == 200
        assert len(seen) == 1
    finally:
        event.remove(appmod.db.engine, 'before_cursor_execute', spy)


@pytest.mark.parametrize('route,body', [
    ('/api/register', '{"username":"sur_reg","password":"Lantern-\\ud800-meadow"}'),
    ('/api/login', '{"username":"sur_login","password":"Lantern-\\ud800-meadow"}'),
])
def test_a_lone_surrogate_in_credentials_is_a_400_not_a_500(client, route, body):
    assert client.post(route, data=body, content_type='application/json').status_code == 400
