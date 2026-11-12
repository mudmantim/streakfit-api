"""The authentication status contract, one case per kind of credential.

The client (static/app.js `api()`) signs out on 401 and on nothing else. So:
  * every way of NOT being authenticated must be 401, never 422 or 403, or
    the person is stuck with a dead session (the bug fixed on this branch);
  * every way of being authenticated but NOT ALLOWED must be 403, never 401,
    or a permission error would sign a valid person out.
"""
import base64
import json
from datetime import timedelta

import jwt as pyjwt
import pytest
from flask_jwt_extended import create_access_token

from tests.conftest import auth_headers, register_and_login

ME = '/api/me'


def _b64(obj):
    return base64.urlsafe_b64encode(json.dumps(obj).encode()).rstrip(b'=').decode()


@pytest.fixture
def tokens(app, client):
    live = register_and_login(client, 'contract_live')
    uid = client.get(ME, headers=auth_headers(live)).get_json()['id']
    claims = pyjwt.decode(live, options={'verify_signature': False})
    with app.app_context():
        expired = create_access_token(identity=str(uid), expires_delta=timedelta(seconds=-1))
    head, body, _ = live.split('.')
    return {
        'live': live,
        'expired': expired,
        'foreign-signed': pyjwt.encode(claims, 'another-servers-key-0123456789abcdef', algorithm='HS256'),
        'signature replaced': f'{head}.{body}.' + 'x' * 43,
        'alg none': f'{_b64({"alg": "none", "typ": "JWT"})}.{body}.',
        'garbage': 'not-a-jwt-at-all',
        'two segments': f'{head}.{body}',
    }


CASES = [
    # (description, header builder, expected status, expected error)
    ('no Authorization header', lambda t: {}, 401, 'Missing or invalid token'),
    ('wrong scheme', lambda t: {'Authorization': 'Basic ' + t['live']}, 401, 'Missing or invalid token'),
    ('Bearer with no token', lambda t: {'Authorization': 'Bearer'}, 401, 'Invalid token'),
    ('expired', lambda t: auth_headers(t['expired']), 401, 'Token has expired'),
    ('foreign-signed', lambda t: auth_headers(t['foreign-signed']), 401, 'Invalid token'),
    ('signature replaced', lambda t: auth_headers(t['signature replaced']), 401, 'Invalid token'),
    ('alg none', lambda t: auth_headers(t['alg none']), 401, 'Invalid token'),
    ('garbage', lambda t: auth_headers(t['garbage']), 401, 'Invalid token'),
    ('two segments', lambda t: auth_headers(t['two segments']), 401, 'Invalid token'),
]


@pytest.mark.parametrize('desc,headers,status,error', CASES, ids=[c[0] for c in CASES])
def test_every_unauthenticated_request_is_a_401_the_client_signs_out_on(client, tokens, desc, headers, status, error):
    r = client.get(ME, headers=headers(tokens))
    assert (r.status_code, r.get_json()) == (status, {'error': error}), desc


def test_a_live_token_is_accepted(client, tokens):
    assert client.get(ME, headers=auth_headers(tokens['live'])).status_code == 200


def test_authenticated_but_not_allowed_is_403_never_401(client, tokens):
    """A permission error must not sign a valid person out."""
    owner = register_and_login(client, 'contract_owner')
    team = client.post('/api/teams', json={'name': 'Contract Team'}, headers=auth_headers(owner)).get_json()
    team_id = (team.get('team') or team)['id']
    r = client.get(f'/api/teams/{team_id}', headers=auth_headers(tokens['live']))   # not a member
    assert r.status_code == 403
    assert client.get('/api/admin/stats', headers=auth_headers(tokens['live'])).status_code == 403  # admin secret


def test_a_token_for_a_deleted_account_is_not_a_403(app, client):
    """After account deletion the old token must not look like a permission
    problem; whatever it is, the client must end up signed out or told plainly."""
    tok = register_and_login(client, 'contract_gone', password='WalkTest123!')
    assert client.delete(ME, json={'password': 'WalkTest123!'}, headers=auth_headers(tok)).status_code == 200
    assert client.get(ME, headers=auth_headers(tok)).status_code in (401, 404)


def test_the_client_signs_out_exactly_on_401():
    """The other half of the contract, read from the shipped client."""
    from pathlib import Path
    js = (Path(__file__).resolve().parent.parent / 'static' / 'app.js').read_text()
    assert "if (res.status === 401 && token) {" in js
    assert "localStorage.removeItem('streakfit_token');" in js
    assert '=== 422' not in js and '=== 403 && token' not in js
