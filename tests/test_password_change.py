"""PW-1: change your password, and every other device is signed out.

docs/architecture/pw1-implementation-plan.md; matrix rows T1-T10, T13, T14
(T11/T12/T15 are on PostgreSQL in test_password_change_postgres.py).
"""
import ast
import logging
import os
from datetime import timedelta

import pytest
from flask_jwt_extended import create_access_token

import app as appmod
from conftest import auth_headers, register_and_login

PASSWORD = 'WalkTest-Lantern-123!'
NEW = 'Kettle-Morning-Lantern'


def _change(client, token, current=PASSWORD, new=NEW, ip='10.0.0.1', **extra):
    body = {'current_password': current, 'new_password': new, **extra}
    return client.post('/api/me/password', json=body, headers=auth_headers(token),
                       environ_base={'REMOTE_ADDR': ip})


def _login(client, username, password):
    return client.post('/api/login', json={'username': username, 'password': password})


def _user(username):
    return appmod.db.session.execute(
        appmod.db.select(appmod.User).where(appmod.User.username == username)
    ).scalar_one()


# ── T1 / T5: the change itself ───────────────────────────────────────────────

def test_a_change_swaps_the_password_and_returns_a_working_token(client):
    token = register_and_login(client, 'pw_alice')
    r = _change(client, token)
    assert r.status_code == 200
    body = r.get_json()
    assert body['changed'] is True and body['access_token']

    assert _login(client, 'pw_alice', PASSWORD).status_code == 401
    assert _login(client, 'pw_alice', NEW).status_code == 200
    assert client.get('/api/me', headers=auth_headers(body['access_token'])).status_code == 200

    u = _user('pw_alice')
    assert u.token_version == 1 and u.password_changed_at is not None


def test_a_token_from_after_the_change_keeps_working(client):
    register_and_login(client, 'pw_fresh')
    token = _login(client, 'pw_fresh', PASSWORD).get_json()['access_token']
    new_token = _change(client, token).get_json()['access_token']
    later = _login(client, 'pw_fresh', NEW).get_json()['access_token']
    for t in (new_token, later):
        assert client.get('/api/me', headers=auth_headers(t)).status_code == 200


# ── T4 / T7: every other device is signed out ────────────────────────────────

SIGNED_OUT_ROUTES = [
    ('get', '/api/me', None),
    ('patch', '/api/me', {'display_name': 'X'}),
    ('delete', '/api/me', {'password': NEW}),
    ('get', '/api/me/data', None),
    ('get', '/api/daily', None),
    ('post', '/api/coach', {'message': 'hi'}),
    ('post', '/api/me/password', {'current_password': NEW, 'new_password': 'Another-Long-One'}),
    ('get', '/api/teams', None),
]


@pytest.mark.parametrize('method,path,body', SIGNED_OUT_ROUTES)
def test_another_devices_token_is_refused_everywhere_after_a_change(client, method, path, body):
    phone = register_and_login(client, 'pw_two_devices')
    tablet = _login(client, 'pw_two_devices', PASSWORD).get_json()['access_token']
    assert _change(client, phone).status_code == 200

    r = getattr(client, method)(path, json=body, headers=auth_headers(tablet))
    assert r.status_code == 401
    assert r.get_json()['error'] == 'session_ended'
    # Refused, not acted on: the account is still there, unchanged.
    assert _user('pw_two_devices').display_name is None
    assert _login(client, 'pw_two_devices', NEW).status_code == 200


def test_a_token_from_before_this_release_works_until_the_first_change(client):
    """No `tv` claim (every token issued by the previous build) reads as 0."""
    register_and_login(client, 'pw_legacy')
    uid = _user('pw_legacy').id
    legacy = create_access_token(identity=str(uid))           # no additional claims
    assert client.get('/api/me', headers=auth_headers(legacy)).status_code == 200
    assert _change(client, legacy).status_code == 200
    r = client.get('/api/me', headers=auth_headers(legacy))
    assert r.status_code == 401 and r.get_json()['error'] == 'session_ended'


def test_each_change_retires_the_token_the_previous_change_issued(client):
    token = register_and_login(client, 'pw_twice')
    first = _change(client, token).get_json()['access_token']
    second = _change(client, first, current=NEW, new='Second-Long-Password').get_json()['access_token']
    assert client.get('/api/me', headers=auth_headers(first)).status_code == 401
    assert client.get('/api/me', headers=auth_headers(second)).status_code == 200
    assert _user('pw_twice').token_version == 2


# ── T2 / T3: a wrong current password ────────────────────────────────────────

def test_a_wrong_current_password_is_a_403_that_changes_nothing(client):
    """403, not 401: a typo must not sign the person out (clients treat 401 so)."""
    token = register_and_login(client, 'pw_typo')
    before = _user('pw_typo')
    hash_before, version_before = before.password_hash, before.token_version
    r = _change(client, token, current='not-my-password')
    assert r.status_code == 403
    assert r.get_json()['error'] == 'password_incorrect'
    appmod.db.session.expire_all()
    after = _user('pw_typo')
    assert after.password_hash == hash_before and after.token_version == version_before
    assert after.password_changed_at is None
    assert client.get('/api/me', headers=auth_headers(token)).status_code == 200
    assert _login(client, 'pw_typo', PASSWORD).status_code == 200


# ── T6 / T8: expired tokens and deleted accounts ─────────────────────────────

def test_an_expired_token_cannot_change_the_password(client):
    register_and_login(client, 'pw_expired')
    uid = _user('pw_expired').id
    old = create_access_token(identity=str(uid), additional_claims={'tv': 0},
                              expires_delta=timedelta(seconds=-5))
    r = _change(client, old)
    assert r.status_code == 401 and r.get_json()['error'] == 'Token has expired'
    assert _login(client, 'pw_expired', PASSWORD).status_code == 200


def test_a_deleted_accounts_token_still_answers_404_not_session_ended(client):
    token = register_and_login(client, 'pw_gone')
    assert client.delete('/api/me', json={'password': PASSWORD},
                         headers=auth_headers(token)).status_code == 200
    assert client.get('/api/me', headers=auth_headers(token)).status_code == 404
    assert _change(client, token).status_code == 404


def test_account_deletion_works_with_the_new_password_and_not_the_old(client):
    token = register_and_login(client, 'pw_then_delete')
    new_token = _change(client, token).get_json()['access_token']
    assert client.delete('/api/me', json={'password': PASSWORD},
                         headers=auth_headers(new_token)).status_code == 403
    assert client.delete('/api/me', json={'password': NEW},
                         headers=auth_headers(new_token)).status_code == 200
    assert _login(client, 'pw_then_delete', NEW).status_code == 401


# ── T9: policy, and that a policy failure spends no slow hash ────────────────

@pytest.mark.parametrize('new,code', [
    ('Short-pass-1', 'too_short'),                     # 12: the minimum is 15
    ('x' * 120 + 'Qz9!-abcd', 'too_long'),
    ('pw_policy_kid-2026', 'username_based'),          # the username is most of it
    ('StreakFit-2026-!!!', 'service_name'),
    ('rickie!!!!!!!!!!', 'service_name'),
    ('aaaaaaaaaaaaaaaa', 'repetitive'),
    ('abababababababab', 'repetitive'),
    ('123123123123123', 'repetitive'),
    ('abcdefghijklmnop', 'sequential'),
    ('zyxwvutsrqponmlk', 'sequential'),
    ('iloveyouiloveyou', 'common'),                    # on the breached-password list
    ('PasswordPassword', 'common'),                    # case does not get it past
    ('\uff50\uff41\uff53\uff53\uff57\uff4f\uff52\uff44password', 'common'),  # full-width
    (PASSWORD, 'same_as_current'),
])
def test_a_weak_new_password_is_refused_without_hashing_anything(client, monkeypatch, new, code):
    token = register_and_login(client, 'pw_policy_kid')
    calls = []
    monkeypatch.setattr(appmod, 'check_password_hash',
                        lambda *a: calls.append('check') or True)
    monkeypatch.setattr(appmod, 'generate_password_hash',
                        lambda *a, **k: calls.append('generate') or 'x')
    r = _change(client, token, new=new)
    assert r.status_code == 400
    assert r.get_json()['error'] == 'password_policy'
    assert r.get_json()['code'] == code
    assert r.get_json()['message']
    assert calls == []
    assert _user('pw_policy_kid').token_version == 0


@pytest.mark.parametrize('new', [
    'Kettle-Morning-Lantern',
    'correct horse battery staple',
    'pw_policy_ok is my name',        # contains a DIFFERENT username fragment
    '0123456789abcdefX',              # not a straight run: 9 -> a is a jump
    'Ünïcødé-pässwörd-lantern',
    'Savannah-lantern-meadow',        # review B3: a short name inside is no weakness
])
def test_reasonable_passwords_are_accepted(client, new):
    token = register_and_login(client, 'pw_ok_user')
    assert _change(client, token, new=new).status_code == 200


def test_registration_applies_the_same_policy(client):
    """One policy for every new password (owner decision, 2026-10-12)."""
    for name, pw, code in (('reg_short', 'Fourteen-chars', 'too_short'),
                           ('reg_common', 'passwordpassword', 'common'),
                           ('reg_seq', 'abcdefghijklmnop', 'sequential'),
                           ('reg_mostly_name', 'reg_mostly_name-1', 'username_based')):
        r = client.post('/api/register', json={'username': name, 'password': pw})
        assert r.status_code == 400, (name, r.get_json())
        assert r.get_json()['code'] == code
        assert r.get_json()['error'] == appmod._PASSWORD_POLICY_MESSAGES[code]
    assert client.post('/api/register', json={'username': 'reg_ok',
                                              'password': 'Fifteen-chars-ok'}).status_code == 201


def test_an_existing_short_password_keeps_working(client):
    """The new minimum judges NEW passwords only. An account created under
    the old 8-character rule still signs in, deletes, and can change to a
    compliant password."""
    from werkzeug.security import generate_password_hash
    u = appmod.User(username='pw_legacy_short', password_hash=generate_password_hash('Old8char', method='pbkdf2:sha256'))
    appmod.db.session.add(u)
    appmod.db.session.commit()
    r = _login(client, 'pw_legacy_short', 'Old8char')
    assert r.status_code == 200
    token = r.get_json()['access_token']
    assert _change(client, token, current='Old8char').status_code == 200
    assert _login(client, 'pw_legacy_short', NEW).status_code == 200


def test_a_successful_change_hashes_exactly_twice(client, monkeypatch):
    """F2: verify the current password, hash the new one. No third hash."""
    token = register_and_login(client, 'pw_two_hashes')
    real_check, real_gen = appmod.check_password_hash, appmod.generate_password_hash
    calls = []
    monkeypatch.setattr(appmod, 'check_password_hash',
                        lambda *a: calls.append('check') or real_check(*a))
    monkeypatch.setattr(appmod, 'generate_password_hash',
                        lambda *a, **k: calls.append('generate') or real_gen(*a, **k))
    assert _change(client, token).status_code == 200
    assert calls == ['check', 'generate']


# ── T10: malformed bodies ────────────────────────────────────────────────────

@pytest.mark.parametrize('body', [
    None, [], 'text', {},
    {'current_password': PASSWORD},
    {'new_password': NEW},
    {'current_password': 123, 'new_password': NEW},
    {'current_password': PASSWORD, 'new_password': ['a']},
    {'current_password': '', 'new_password': NEW},
    {'current_password': PASSWORD, 'new_password': None},
])
def test_a_malformed_body_is_a_400_that_changes_nothing(client, body):
    token = register_and_login(client, 'pw_malformed')
    kwargs = {'json': body} if body is not None else {'data': 'not json',
                                                      'content_type': 'application/json'}
    r = client.post('/api/me/password', headers=auth_headers(token), **kwargs)
    assert r.status_code == 400
    assert _user('pw_malformed').token_version == 0
    assert _login(client, 'pw_malformed', PASSWORD).status_code == 200


def test_no_token_is_a_401(client):
    r = client.post('/api/me/password', json={'current_password': PASSWORD, 'new_password': NEW})
    assert r.status_code == 401


# ── Rate limits ──────────────────────────────────────────────────────────────

@pytest.fixture()
def limiter_on():
    appmod.limiter.reset()
    appmod.limiter.enabled = True
    yield
    appmod.limiter.enabled = False
    appmod.limiter.reset()


def test_wrong_current_passwords_are_throttled_and_the_right_one_then_waits(client, limiter_on):
    token = register_and_login(client, 'pw_grind')
    codes = [_change(client, token, current=f'guess-{i}').status_code for i in range(6)]
    assert codes == [403] * 5 + [429]
    assert _change(client, token).status_code == 429          # even the right one, for now
    assert _login(client, 'pw_grind', PASSWORD).status_code == 200   # login is not locked


def test_policy_mistakes_do_not_spend_the_wrong_password_allowance(client, limiter_on):
    token = register_and_login(client, 'pw_fumble')
    for _ in range(5):
        assert _change(client, token, new='short').status_code == 400
    assert _change(client, token).status_code == 200


def test_there_is_a_daily_ceiling_on_attempts_of_any_kind(client, limiter_on):
    token = register_and_login(client, 'pw_daily')
    codes = [_change(client, token, new='short').status_code for _ in range(11)]
    assert codes == [400] * 10 + [429]


def test_the_throttle_is_per_person_not_per_household(client, limiter_on):
    a = register_and_login(client, 'pw_house_a')
    b = register_and_login(client, 'pw_house_b')
    for i in range(5):
        _change(client, a, current=f'guess-{i}', ip='10.9.9.9')
    assert _change(client, a, ip='10.9.9.9').status_code == 429
    assert _change(client, b, ip='10.9.9.9').status_code == 200


def test_with_the_shared_store_degraded_a_strict_cap_still_applies(client, limiter_on, monkeypatch):
    token = register_and_login(client, 'pw_degraded')
    monkeypatch.setattr(appmod, '_rate_limit_degraded', lambda: True)
    codes = [_change(client, token, current=f'guess-{i}').status_code for i in range(4)]
    assert codes[-1] == 429 and 429 not in codes[:3]


# ── T13: nothing secret is logged ────────────────────────────────────────────

def test_no_password_hash_or_token_reaches_the_log(client, caplog):
    token = register_and_login(client, 'pw_quiet')
    secrets = [PASSWORD, NEW, 'wrong-guess-value', 'q7#Zp']
    with caplog.at_level(logging.DEBUG):
        _change(client, token, current='wrong-guess-value')
        _change(client, token, new='q7#Zp')
        r = _change(client, token)
    new_token = r.get_json()['access_token']
    secrets += [token, new_token, _user('pw_quiet').password_hash]
    text = '\n'.join(rec.getMessage() for rec in caplog.records)
    for s in secrets:
        assert s not in text
    assert 'event=password_changed' in text and 'sessions_revoked=true' in text
    assert 'reason=current_password_wrong' in text and 'reason=too_short' in text
    # Not even the length.
    assert f'{len(NEW)}' not in ''.join(r.getMessage() for r in caplog.records
                                        if 'password' in r.getMessage())


# ── T14: the export ──────────────────────────────────────────────────────────

def test_the_export_says_when_the_password_changed_and_never_what_it_is(client):
    token = register_and_login(client, 'pw_export')
    before = client.get('/api/me/data', headers=auth_headers(token)).get_json()
    assert before['account']['password_changed_at'] is None
    new_token = _change(client, token).get_json()['access_token']
    r = client.get('/api/me/data', headers=auth_headers(new_token))
    after = r.get_json()
    assert after['account']['password_changed_at'].endswith('Z')
    raw = r.get_data(as_text=True)
    assert 'password_hash' not in raw and 'pbkdf2' not in raw and NEW not in raw
    assert 'token_version' not in raw


# ── Every token carries the version ──────────────────────────────────────────

def test_create_access_token_is_only_called_by_the_one_issuer():
    """A token minted anywhere else would carry no `tv` -- version 0 -- and so
    survive no password change at all on an account that has never changed it,
    and be refused on one that has. Either way, wrong. One issuer."""
    src = open(os.path.join(os.path.dirname(appmod.__file__), 'app.py')).read()
    tree = ast.parse(src)
    callers = []
    for fn in ast.walk(tree):
        if isinstance(fn, ast.FunctionDef):
            for node in ast.walk(fn):
                if (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                        and node.func.id == 'create_access_token'):
                    callers.append(fn.name)
    assert callers == ['_issue_access_token']


def test_login_tokens_carry_the_current_version(client):
    from flask_jwt_extended import decode_token
    token = register_and_login(client, 'pw_claims')
    assert decode_token(token)['tv'] == 0
    new_token = _change(client, token).get_json()['access_token']
    assert decode_token(new_token)['tv'] == 1
    login_token = _login(client, 'pw_claims', NEW).get_json()['access_token']
    assert decode_token(login_token)['tv'] == 1


# ── The cost of revocation is one query per request, whatever the data ───────

def test_the_revocation_check_is_one_query_that_does_not_scale(client):
    """Three query-count ceilings rose by exactly 1 with PW-1. This is why
    that is a constant and not an N+1: the same request costs the same number
    of extra queries with 2 teams' worth of data or 12."""
    from test_query_counts import count_queries
    token = register_and_login(client, 'pw_cost')
    uid = _user('pw_cost').id

    def cost(teams):
        for i in range(teams):
            t = appmod.Team(name=f'Cost {uid}-{i}-{teams}', created_by_user_id=uid)
            appmod.db.session.add(t)
            appmod.db.session.commit()
            appmod.db.session.add(appmod.TeamMembership(team_id=t.id, user_id=uid))
        appmod.db.session.commit()
        with count_queries() as n:
            assert client.get('/api/teams', headers=auth_headers(token)).status_code == 200
        return n[0]

    small, large = cost(2), cost(10)
    assert small == large


def test_routes_that_load_the_caller_read_the_row_once_not_twice(client):
    """The revocation check's read is reused by the route (request-scoped
    reference on g). Before the fix /api/me read the user row twice: the
    identity map holds objects weakly, so the loader's instance was collected
    before the route asked for it again."""
    from sqlalchemy import event
    token = register_and_login(client, 'pw_once')
    client.get('/api/daily', headers=auth_headers(token))          # create the day's mission
    seen = []

    def spy(conn, cursor, statement, params, context, executemany):
        s = statement.replace('"', '')
        if 'FROM user' in s and 'WHERE user.id' in s:
            seen.append(s)

    event.listen(appmod.db.engine, 'before_cursor_execute', spy)
    try:
        # Expected = the count on production 1d7b2f2, before PW-1 existed
        # (measured with the same data). /api/daily reads the row twice there
        # too: it commits the day's mission and reads again.
        for path, expected in (('/api/me', 1), ('/api/daily', 2), ('/api/me/data', 1)):
            appmod.db.session.remove()
            seen.clear()
            assert client.get(path, headers=auth_headers(token)).status_code == 200
            assert len(seen) == expected, (path, len(seen))
    finally:
        event.remove(appmod.db.engine, 'before_cursor_execute', spy)


def test_the_cached_row_never_outlives_a_password_change(client):
    """The same app context (and so the same g and session) serves the tablet
    before and after the phone's change: the cached instance must not keep an
    old token_version alive."""
    phone = register_and_login(client, 'pw_cache_stale')
    tablet = _login(client, 'pw_cache_stale', PASSWORD).get_json()['access_token']
    assert client.get('/api/me', headers=auth_headers(tablet)).status_code == 200
    assert _change(client, phone).status_code == 200
    r = client.get('/api/me', headers=auth_headers(tablet))
    assert r.status_code == 401 and r.get_json()['error'] == 'session_ended'
