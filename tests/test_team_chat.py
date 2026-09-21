"""R2.5 Team Chat MVP -- tiny team-scoped chat on top of the existing
team_message table. Covers the verification list from the brief: member
read/post, empty/too-long rejection, outsider blocked from both, and
emoji reactions are just short messages (no separate reaction table).
"""
from conftest import register_and_login, auth_headers


from app import TEAM_MESSAGE_MAX_LENGTH

# These tests check ATTRIBUTION — that the right person is named on a moment,
# a message or a challenge card. Peer-visible surfaces stopped sending login
# identifiers (tests/test_peer_identity_privacy.py), so they send a chosen
# display name or "Member N".
#
# So the fixtures choose a name, which is what a real family does, and every
# assertion below keeps its original meaning. Deliberately NOT done in
# conftest: setting a display name for every account everywhere would make
# the privacy tests vacuous, because those rely on accounts that have not
# chosen one.
_register_without_a_name = register_and_login


def register_and_login(client, username, password='WalkTest123!'):
    token = _register_without_a_name(client, username, password)
    client.patch('/api/me', json={'display_name': username},
                 headers=auth_headers(token))
    return token



def create_team(client, token, name):
    return client.post('/api/teams', json={'name': name}, headers=auth_headers(token))


def join_team(client, token, team_id, code):
    return client.post(f'/api/teams/{team_id}/join', json={'code': code}, headers=auth_headers(token))


def get_messages(client, token, team_id):
    return client.get(f'/api/teams/{team_id}/messages', headers=auth_headers(token))


def post_message(client, token, team_id, body):
    return client.post(f'/api/teams/{team_id}/messages', json={'body': body}, headers=auth_headers(token))


def test_member_can_post_and_read_message(client):
    token = register_and_login(client, 'creator')
    team = create_team(client, token, 'Hill Family').get_json()['team']

    resp = post_message(client, token, team['id'], 'Great job today!')
    assert resp.status_code == 201
    posted = resp.get_json()
    assert posted['sender_type'] == 'user'
    # This file's fixture gives every account a display name (see the top of
    # the file), so the peer-visible label is that chosen name. An account
    # with NO display name gets an ordinal instead -- covered by
    # tests/test_username_exposure.py and tests/test_peer_identity_privacy.py.
    assert posted['sender_username'] == 'creator'
    assert posted['sender_user_id'] is not None
    assert posted['body'] == 'Great job today!'

    messages = get_messages(client, token, team['id']).get_json()
    assert len(messages) == 1
    assert messages[0]['body'] == 'Great job today!'


def test_member_sees_other_members_message(client):
    creator_token = register_and_login(client, 'creator')
    member_token = register_and_login(client, 'member')
    team = create_team(client, creator_token, 'Hill Family').get_json()['team']
    join_team(client, member_token, team['id'], team['invite_code'])

    post_message(client, creator_token, team['id'], 'Hi team')

    # Joining posts a Rickie welcome message (R2.6) ahead of the chat message.
    messages = get_messages(client, member_token, team['id']).get_json()
    assert len(messages) == 2
    assert messages[0]['sender_type'] == 'rickie'
    assert messages[1]['sender_username'] == 'creator'


def test_messages_ordered_chronologically(client):
    token = register_and_login(client, 'creator')
    team = create_team(client, token, 'Hill Family').get_json()['team']

    post_message(client, token, team['id'], 'first')
    post_message(client, token, team['id'], 'second')

    messages = get_messages(client, token, team['id']).get_json()
    assert [m['body'] for m in messages] == ['first', 'second']


def test_empty_message_rejected(client):
    token = register_and_login(client, 'creator')
    team = create_team(client, token, 'Hill Family').get_json()['team']

    resp = post_message(client, token, team['id'], '')
    assert resp.status_code == 400

    resp_ws = post_message(client, token, team['id'], '   ')
    assert resp_ws.status_code == 400

    assert get_messages(client, token, team['id']).get_json() == []


def test_message_is_trimmed(client):
    token = register_and_login(client, 'creator')
    team = create_team(client, token, 'Hill Family').get_json()['team']

    post_message(client, token, team['id'], '  padded message  ')

    messages = get_messages(client, token, team['id']).get_json()
    assert messages[0]['body'] == 'padded message'


def test_too_long_message_rejected(client):
    token = register_and_login(client, 'creator')
    team = create_team(client, token, 'Hill Family').get_json()['team']

    too_long = 'x' * (TEAM_MESSAGE_MAX_LENGTH + 1)
    resp = post_message(client, token, team['id'], too_long)
    assert resp.status_code == 400

    at_limit = 'x' * TEAM_MESSAGE_MAX_LENGTH
    resp_ok = post_message(client, token, team['id'], at_limit)
    assert resp_ok.status_code == 201


def test_outsider_cannot_read_messages(client):
    creator_token = register_and_login(client, 'creator')
    outsider_token = register_and_login(client, 'outsider')
    team = create_team(client, creator_token, 'Hill Family').get_json()['team']

    resp = get_messages(client, outsider_token, team['id'])
    assert resp.status_code == 403


def test_outsider_cannot_post_messages(client):
    creator_token = register_and_login(client, 'creator')
    outsider_token = register_and_login(client, 'outsider')
    team = create_team(client, creator_token, 'Hill Family').get_json()['team']

    resp = post_message(client, outsider_token, team['id'], 'sneaking in')
    assert resp.status_code == 403
    assert get_messages(client, creator_token, team['id']).get_json() == []


def test_reaction_emoji_posts_as_normal_message(client):
    token = register_and_login(client, 'creator')
    team = create_team(client, token, 'Hill Family').get_json()['team']

    resp = post_message(client, token, team['id'], '🔥')
    assert resp.status_code == 201
    assert resp.get_json()['body'] == '🔥'

    messages = get_messages(client, token, team['id']).get_json()
    assert messages[0]['body'] == '🔥'
    assert messages[0]['sender_type'] == 'user'
