"""R2.8 Operation: No Dead Ends -- remove-member is the second of the two
creator safety powers TEAM_SYSTEM_BASELINE Section 4 designed (the first,
rotate-invite, already existed since R2.1). Creator-only, can't target
yourself (Leave Team is that action), and a removed member immediately
loses read access to everything team-scoped -- same membership check
every other team route already enforces, nothing new to verify there
beyond confirming it actually takes effect after removal.
"""
from conftest import register_and_login, auth_headers


def create_team(client, token, name):
    return client.post('/api/teams', json={'name': name}, headers=auth_headers(token))


def join_team(client, token, team_id, code):
    return client.post(f'/api/teams/{team_id}/join', json={'code': code}, headers=auth_headers(token))


def remove_member(client, token, team_id, member_user_id):
    return client.delete(f'/api/teams/{team_id}/members/{member_user_id}', headers=auth_headers(token))


def get_user_id(client, token):
    return client.get('/api/me', headers=auth_headers(token)).get_json()['id']


def test_creator_can_remove_member(client):
    creator_token = register_and_login(client, 'creator')
    member_token = register_and_login(client, 'member')
    team = create_team(client, creator_token, 'Hill Family').get_json()['team']
    join_team(client, member_token, team['id'], team['invite_code'])
    member_id = get_user_id(client, member_token)

    resp = remove_member(client, creator_token, team['id'], member_id)
    assert resp.status_code == 200

    team_detail = client.get(f"/api/teams/{team['id']}", headers=auth_headers(creator_token)).get_json()
    assert team_detail['member_count'] == 1
    assert all(m['user_id'] != member_id for m in team_detail['members'])


def test_non_creator_cannot_remove_member(client):
    creator_token = register_and_login(client, 'creator')
    member_token = register_and_login(client, 'member')
    other_token = register_and_login(client, 'other')
    team = create_team(client, creator_token, 'Hill Family').get_json()['team']
    join_team(client, member_token, team['id'], team['invite_code'])
    join_team(client, other_token, team['id'], team['invite_code'])
    member_id = get_user_id(client, member_token)

    resp = remove_member(client, other_token, team['id'], member_id)
    assert resp.status_code == 403

    team_detail = client.get(f"/api/teams/{team['id']}", headers=auth_headers(creator_token)).get_json()
    assert team_detail['member_count'] == 3


def test_outsider_cannot_remove_member(client):
    creator_token = register_and_login(client, 'creator')
    member_token = register_and_login(client, 'member')
    outsider_token = register_and_login(client, 'outsider')
    team = create_team(client, creator_token, 'Hill Family').get_json()['team']
    join_team(client, member_token, team['id'], team['invite_code'])
    member_id = get_user_id(client, member_token)

    resp = remove_member(client, outsider_token, team['id'], member_id)
    assert resp.status_code == 403


def test_creator_cannot_remove_self(client):
    creator_token = register_and_login(client, 'creator')
    team = create_team(client, creator_token, 'Hill Family').get_json()['team']
    creator_id = get_user_id(client, creator_token)

    resp = remove_member(client, creator_token, team['id'], creator_id)
    assert resp.status_code == 400

    team_detail = client.get(f"/api/teams/{team['id']}", headers=auth_headers(creator_token)).get_json()
    assert team_detail['member_count'] == 1


def test_remove_nonmember_returns_404(client):
    creator_token = register_and_login(client, 'creator')
    not_a_member_token = register_and_login(client, 'not_a_member')
    team = create_team(client, creator_token, 'Hill Family').get_json()['team']
    not_a_member_id = get_user_id(client, not_a_member_token)

    resp = remove_member(client, creator_token, team['id'], not_a_member_id)
    assert resp.status_code == 404


def test_removed_member_loses_chat_access(client):
    creator_token = register_and_login(client, 'creator')
    member_token = register_and_login(client, 'member')
    team = create_team(client, creator_token, 'Hill Family').get_json()['team']
    join_team(client, member_token, team['id'], team['invite_code'])
    member_id = get_user_id(client, member_token)

    remove_member(client, creator_token, team['id'], member_id)

    get_resp = client.get(f"/api/teams/{team['id']}/messages", headers=auth_headers(member_token))
    assert get_resp.status_code == 403

    post_resp = client.post(
        f"/api/teams/{team['id']}/messages", json={'body': 'sneaking back in'},
        headers=auth_headers(member_token),
    )
    assert post_resp.status_code == 403


def test_removed_member_loses_moments_access(client):
    creator_token = register_and_login(client, 'creator')
    member_token = register_and_login(client, 'member')
    team = create_team(client, creator_token, 'Hill Family').get_json()['team']
    join_team(client, member_token, team['id'], team['invite_code'])
    member_id = get_user_id(client, member_token)

    remove_member(client, creator_token, team['id'], member_id)

    resp = client.get(f"/api/teams/{team['id']}/moments", headers=auth_headers(member_token))
    assert resp.status_code == 403


def test_removed_member_loses_team_detail_access(client):
    creator_token = register_and_login(client, 'creator')
    member_token = register_and_login(client, 'member')
    team = create_team(client, creator_token, 'Hill Family').get_json()['team']
    join_team(client, member_token, team['id'], team['invite_code'])
    member_id = get_user_id(client, member_token)

    remove_member(client, creator_token, team['id'], member_id)

    resp = client.get(f"/api/teams/{team['id']}", headers=auth_headers(member_token))
    assert resp.status_code == 403


def test_remove_member_does_not_create_moment_or_message(client):
    """Removal is deliberately silent -- no team_moment, no Rickie/chat
    message -- unlike join/leave which both create a moment."""
    creator_token = register_and_login(client, 'creator')
    member_token = register_and_login(client, 'member')
    team = create_team(client, creator_token, 'Hill Family').get_json()['team']
    join_team(client, member_token, team['id'], team['invite_code'])
    member_id = get_user_id(client, member_token)

    moments_before = client.get(f"/api/teams/{team['id']}/moments", headers=auth_headers(creator_token)).get_json()
    messages_before = client.get(f"/api/teams/{team['id']}/messages", headers=auth_headers(creator_token)).get_json()

    remove_member(client, creator_token, team['id'], member_id)

    moments_after = client.get(f"/api/teams/{team['id']}/moments", headers=auth_headers(creator_token)).get_json()
    messages_after = client.get(f"/api/teams/{team['id']}/messages", headers=auth_headers(creator_token)).get_json()

    assert len(moments_after) == len(moments_before)
    assert len(messages_after) == len(messages_before)
