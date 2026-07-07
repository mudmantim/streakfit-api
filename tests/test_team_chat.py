"""R2.5 Team Chat MVP -- tiny team-scoped chat on top of the existing
team_message table. Covers the verification list from the brief: member
read/post, empty/too-long rejection, outsider blocked from both, and
emoji reactions are just short messages (no separate reaction table).
"""
from conftest import register_and_login, auth_headers

from app import app as flask_app, db, TEAM_MESSAGE_MAX_LENGTH


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
    assert posted['sender_username'] == 'creator'
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
