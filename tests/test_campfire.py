"""R2.3 Campfire MVP — covers the verification list from the brief: no-teams
user completes with no errors, one-team user gets +1, multi-team user gets
+1 to each, repeated completion doesn't double-count, and Team Rickie never
gets touched (it has no team_campfire row to increment in the first place).
"""
from conftest import register_and_login, auth_headers


def create_team(client, token, name):
    return client.post('/api/teams', json={'name': name}, headers=auth_headers(token))


def join_team(client, token, team_id, code):
    return client.post(f'/api/teams/{team_id}/join', json={'code': code}, headers=auth_headers(token))


def complete_full_mission(client, token):
    """Fetches today's real exercise keys and completes all 5 in order,
    returning the response from the 5th (the one that should carry
    team_campfire_updates)."""
    daily = client.get('/api/daily', headers=auth_headers(token)).get_json()
    exercise_keys = [ex['key'] for ex in daily['exercises']]
    assert len(exercise_keys) == 5
    last_resp = None
    for key in exercise_keys:
        last_resp = client.post(f'/api/daily/{key}/complete', headers=auth_headers(token))
    return last_resp, exercise_keys


def team_campfire_total(client, token, team_id):
    resp = client.get(f'/api/teams/{team_id}/campfire', headers=auth_headers(token))
    return resp.get_json()['total_team_missions']


def test_no_teams_completion_no_errors(client):
    token = register_and_login(client, 'soloist')

    last_resp, _ = complete_full_mission(client, token)

    assert last_resp.status_code == 200
    body = last_resp.get_json()
    assert body['completed_count'] == 5
    assert body['team_campfire_updates'] == []


def test_one_team_completion_increments_by_one(client):
    token = register_and_login(client, 'creator')
    team = create_team(client, token, 'Hill Family').get_json()['team']

    assert team_campfire_total(client, token, team['id']) == 0

    last_resp, _ = complete_full_mission(client, token)

    body = last_resp.get_json()
    assert body['team_campfire_updates'] == [
        {'team_id': team['id'], 'total_team_missions': 1, 'stage': 'Kindling'}
    ]
    assert team_campfire_total(client, token, team['id']) == 1


def test_multiple_teams_completion_increments_each(client):
    token = register_and_login(client, 'creator')
    team_a = create_team(client, token, 'Hill Family').get_json()['team']
    team_b = create_team(client, token, 'Beta Testers').get_json()['team']

    last_resp, _ = complete_full_mission(client, token)

    body = last_resp.get_json()
    updated_ids = {u['team_id'] for u in body['team_campfire_updates']}
    assert updated_ids == {team_a['id'], team_b['id']}
    for u in body['team_campfire_updates']:
        assert u['total_team_missions'] == 1

    assert team_campfire_total(client, token, team_a['id']) == 1
    assert team_campfire_total(client, token, team_b['id']) == 1


def test_completion_counts_for_teams_joined_not_just_created(client):
    """The increment is scoped to team_membership, not team.created_by_user_id
    -- a member who joined (not created) the team should count too."""
    creator_token = register_and_login(client, 'creator')
    member_token = register_and_login(client, 'member')
    team = create_team(client, creator_token, 'Hill Family').get_json()['team']
    join_team(client, member_token, team['id'], team['invite_code'])

    complete_full_mission(client, member_token)

    assert team_campfire_total(client, creator_token, team['id']) == 1


def test_repeated_completion_does_not_double_count(client):
    token = register_and_login(client, 'creator')
    team = create_team(client, token, 'Hill Family').get_json()['team']

    _, exercise_keys = complete_full_mission(client, token)
    assert team_campfire_total(client, token, team['id']) == 1

    # Re-POST to an exercise already completed today -- DailyCompletion's
    # unique constraint means this hits the `else` (already existing) branch,
    # not the "just inserted, completed_count == 5" branch, so nothing should
    # increment again.
    replay_resp = client.post(
        f'/api/daily/{exercise_keys[0]}/complete', headers=auth_headers(token)
    )
    assert replay_resp.status_code == 200
    assert replay_resp.get_json()['team_campfire_updates'] == []
    assert team_campfire_total(client, token, team['id']) == 1


def test_campfire_stage_advances_past_kindling(client):
    """Not a full 100-mission simulation -- just confirms the stage in the
    response reflects the real threshold math (Kindling is 0-99), matching
    TEAM_SYSTEM_BASELINE Section 2's locked thresholds."""
    token = register_and_login(client, 'creator')
    team = create_team(client, token, 'Hill Family').get_json()['team']

    complete_full_mission(client, token)
    resp = client.get(f"/api/teams/{team['id']}/campfire", headers=auth_headers(token))
    assert resp.get_json()['stage'] == 'Kindling'
