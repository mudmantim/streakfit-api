"""The block list must not undo the block route's anti-enumeration promise.

PUT /api/blocks/<id> answers 204 for a real id and a missing one alike, so it
cannot be used to learn which accounts exist. But it created a row only for a
real account, and GET /api/blocks then listed that row with the account's
display name. Any account could walk the id space and read off every
account's name, including people it had never shared a team with
(E2E campaign R1, D3; reproduced twice by an independent agent).

Blocking is offered only from a team roster, so a block now takes effect only
for somebody the blocker currently shares a team with. For everybody else,
real or not, nothing the blocker can observe changes.
"""
from conftest import register_and_login, auth_headers


def _me(client, token):
    return client.get('/api/me', headers=auth_headers(token)).get_json()


def _blocks(client, token):
    r = client.get('/api/blocks', headers=auth_headers(token))
    assert r.status_code == 200
    return r.get_json()


def test_blocking_a_stranger_is_indistinguishable_from_blocking_a_missing_id(client):
    prober = register_and_login(client, 'oracle_prober')
    stranger = register_and_login(client, 'oracle_stranger')
    assert client.patch('/api/me', json={'display_name': 'Stranger Name'},
                        headers=auth_headers(stranger)).status_code == 200
    stranger_id = _me(client, stranger)['id']

    r_missing = client.put('/api/blocks/999999', headers=auth_headers(prober))
    after_missing = _blocks(client, prober)
    r_real = client.put(f'/api/blocks/{stranger_id}', headers=auth_headers(prober))
    after_real = _blocks(client, prober)

    assert r_missing.status_code == r_real.status_code == 204
    assert after_missing == after_real == []
    assert 'Stranger Name' not in str(after_real)


def test_a_teammate_can_still_be_blocked_and_listed(client):
    a = register_and_login(client, 'oracle_owner')
    b = register_and_login(client, 'oracle_mate')
    team = client.post('/api/teams', json={'name': 'Oracle Team'},
                       headers=auth_headers(a)).get_json()['team']
    assert client.post(f"/api/teams/{team['id']}/join", json={'code': team['invite_code']},
                       headers=auth_headers(b)).status_code == 200
    b_id = _me(client, b)['id']

    assert client.put(f'/api/blocks/{b_id}', headers=auth_headers(a)).status_code == 204
    assert [x['user_id'] for x in _blocks(client, a)] == [b_id]


def test_a_block_made_while_teammates_survives_them_leaving(client):
    """The block is about the person, not the team: leaving must not quietly
    unblock someone, or rejoining with the same code would bring them back."""
    a = register_and_login(client, 'oracle_keeper')
    b = register_and_login(client, 'oracle_leaver')
    team = client.post('/api/teams', json={'name': 'Oracle Two'},
                       headers=auth_headers(a)).get_json()['team']
    client.post(f"/api/teams/{team['id']}/join", json={'code': team['invite_code']},
                headers=auth_headers(b))
    b_id = _me(client, b)['id']
    client.put(f'/api/blocks/{b_id}', headers=auth_headers(a))
    assert client.post(f"/api/teams/{team['id']}/leave",
                       headers=auth_headers(b)).status_code == 200

    assert [x['user_id'] for x in _blocks(client, a)] == [b_id]
