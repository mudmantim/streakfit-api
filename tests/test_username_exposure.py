"""Hotfix regression: a login identifier must never reach a teammate.

`/api/login` takes `username`. Four team-facing serializers were sending that
exact field to every other member of the team — the roster, the team history,
the chat list, and the chat POST echo.

**Every username here is person-shaped on purpose.** The tempting fix is a
helper that prints the login whenever it "looks like a real name" and hides it
otherwise. Tests built from machine-shaped handles (`qa_user_17899…`) or email
addresses pass against that helper while every name a real family would
actually choose still leaks. `oliviahill` and `timhill` are the case that
matters, so they are the case under test.
"""
from conftest import register_and_login, auth_headers


# Distinctive enough that a substring sweep cannot false-positive on ordinary
# response text, ordinary enough that any "looks like a real name" heuristic
# would wave them straight through.
CREATOR = 'timhill'
JOINER = 'oliviahill'
THIRD = 'sarahjones'


def create_team(client, token, name='Hill Family'):
    return client.post('/api/teams', json={'name': name},
                       headers=auth_headers(token)).get_json()['team']


def join_team(client, token, team_id, code):
    return client.post(f'/api/teams/{team_id}/join', json={'code': code},
                       headers=auth_headers(token))


def build_team(client):
    """A creator and a joiner in one team. Returns (tokens, team, code)."""
    creator = register_and_login(client, CREATOR)
    team = create_team(client, creator)
    code = team['invite_code']
    joiner = register_and_login(client, JOINER)
    join_team(client, joiner, team['id'], code)
    return creator, joiner, team, code


def team_facing_bodies(client, token, team_id):
    """Every team-facing response a member can read, as raw text."""
    paths = [
        f'/api/teams/{team_id}',
        f'/api/teams/{team_id}/moments',
        f'/api/teams/{team_id}/messages',
        f'/api/teams/{team_id}/campfire',
        '/api/teams',
    ]
    out = {}
    for p in paths:
        resp = client.get(p, headers=auth_headers(token))
        assert resp.status_code == 200, f'{p} -> {resp.status_code}'
        out[p] = resp.get_data(as_text=True)
    return out


# --- The exposure itself ----------------------------------------------------

def test_no_team_facing_response_contains_any_members_login(client):
    """The sweep. If any field anywhere leaks a login, this fails."""
    creator, joiner, team, _ = build_team(client)

    client.post(f'/api/teams/{team["id"]}/messages',
                json={'body': 'morning all'}, headers=auth_headers(creator))
    client.post(f'/api/teams/{team["id"]}/messages',
                json={'body': 'on my way'}, headers=auth_headers(joiner))

    for reader, who in ((creator, CREATOR), (joiner, JOINER)):
        for path, body in team_facing_bodies(client, reader, team['id']).items():
            for login in (CREATOR, JOINER):
                if login == who and path == '/api/teams':
                    continue  # nothing there names anybody at all
                assert login not in body, (
                    f'{path} exposed the login {login!r} to {who!r}: {body}')


def test_roster_labels_are_not_derived_from_the_login(client):
    creator, joiner, team, _ = build_team(client)

    roster = client.get(f'/api/teams/{team["id"]}',
                        headers=auth_headers(joiner)).get_json()
    names = [m['name'] for m in roster['members']]

    assert names == ['Member 1', 'Member 2'], names
    for m in roster['members']:
        assert m['name'] not in (CREATOR, JOINER)
        # The deprecated compat key is allowed to exist, but only carrying the
        # same label. What is forbidden is the login, under any key.
        assert m.get('username') == m['name']
        assert not {CREATOR, JOINER} & set(str(v) for v in m.values())


def test_team_history_never_names_a_member_by_login(client):
    creator, joiner, team, _ = build_team(client)

    moments = client.get(f'/api/teams/{team["id"]}/moments',
                         headers=auth_headers(joiner)).get_json()
    kinds = {m['moment_type'] for m in moments}
    assert {'team_created', 'member_joined'} <= kinds

    for m in moments:
        assert m['subject_username'] not in (CREATOR, JOINER)
        if m['display_text']:
            assert CREATOR not in m['display_text']
            assert JOINER not in m['display_text']

    created = next(m for m in moments if m['moment_type'] == 'team_created')
    assert created['display_text'] == 'Member 1 created the team'


def test_chat_list_never_names_a_sender_by_login(client):
    creator, joiner, team, _ = build_team(client)
    client.post(f'/api/teams/{team["id"]}/messages',
                json={'body': 'hello'}, headers=auth_headers(creator))

    msgs = client.get(f'/api/teams/{team["id"]}/messages',
                      headers=auth_headers(joiner)).get_json()
    user_msgs = [m for m in msgs if m['sender_type'] == 'user']
    assert user_msgs, msgs
    for m in user_msgs:
        assert m['sender_username'] not in (CREATOR, JOINER)
        assert m['sender_username'] == 'Member 1'


def test_chat_post_echo_never_returns_a_login(client):
    """The POST response takes the one-off branch of the serializer — the one
    a fix applied only to the batch path would leave leaking."""
    creator, joiner, team, _ = build_team(client)

    resp = client.post(f'/api/teams/{team["id"]}/messages',
                       json={'body': 'posting'}, headers=auth_headers(joiner))
    assert resp.status_code == 201
    echoed = resp.get_json()

    assert echoed['sender_username'] == 'Member 2'
    assert JOINER not in resp.get_data(as_text=True)


def test_a_departed_members_login_is_not_disclosed(client):
    creator, joiner, team, _ = build_team(client)
    client.post(f'/api/teams/{team["id"]}/leave', headers=auth_headers(joiner))

    moments = client.get(f'/api/teams/{team["id"]}/moments',
                         headers=auth_headers(creator)).get_json()
    left = next(m for m in moments if m['moment_type'] == 'member_left')

    assert JOINER not in (left['display_text'] or '')
    assert left['subject_username'] != JOINER
    assert left['display_text'] == 'A member left'


# --- The fix must not break the product -------------------------------------

def test_the_roster_still_works(client):
    creator, joiner, team, code = build_team(client)
    third = register_and_login(client, THIRD)
    join_team(client, third, team['id'], code)

    roster = client.get(f'/api/teams/{team["id"]}',
                        headers=auth_headers(creator)).get_json()

    assert roster['member_count'] == 3
    assert roster['name'] == 'Hill Family'
    assert roster['is_creator'] is True
    assert [m['is_creator'] for m in roster['members']] == [True, False, False]
    assert len({m['name'] for m in roster['members']}) == 3
    assert all(m['user_id'] for m in roster['members'])


def test_labels_are_stable_across_requests(client):
    """A label that renumbered between reads would make the history unreadable
    and the roster untrustworthy."""
    creator, joiner, team, _ = build_team(client)

    first = client.get(f'/api/teams/{team["id"]}',
                       headers=auth_headers(creator)).get_json()['members']
    second = client.get(f'/api/teams/{team["id"]}',
                        headers=auth_headers(joiner)).get_json()['members']

    assert {m['user_id']: m['name'] for m in first} == \
           {m['user_id']: m['name'] for m in second}


def test_the_client_can_still_tell_its_own_messages_apart(client):
    """Self-detection used to compare sender_username to the viewer's own
    username. With the login gone, the id is what makes it possible."""
    creator, joiner, team, _ = build_team(client)
    client.post(f'/api/teams/{team["id"]}/messages',
                json={'body': 'mine'}, headers=auth_headers(joiner))

    me = client.get('/api/me', headers=auth_headers(joiner)).get_json()
    msgs = client.get(f'/api/teams/{team["id"]}/messages',
                      headers=auth_headers(joiner)).get_json()
    mine = [m for m in msgs if m.get('sender_user_id') == me['id']]

    assert len(mine) == 1
    assert mine[0]['body'] == 'mine'


def test_your_own_account_still_tells_you_your_username(client):
    """The login is still yours to see. Only peers are cut off."""
    creator, _, _, _ = build_team(client)
    me = client.get('/api/me', headers=auth_headers(creator)).get_json()
    assert me['username'] == CREATOR
