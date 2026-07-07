"""R2.4 Team Moments MVP — covers the verification list from the brief:
team creation/joining/leaving/campfire-log/stage-crossing all create the
right moment, unauthorized reads are blocked, and repeated completion
doesn't create a duplicate stage moment.
"""
from conftest import register_and_login, auth_headers

from app import app as flask_app, db, TeamCampfire


def create_team(client, token, name):
    return client.post('/api/teams', json={'name': name}, headers=auth_headers(token))


def join_team(client, token, team_id, code):
    return client.post(f'/api/teams/{team_id}/join', json={'code': code}, headers=auth_headers(token))


def get_moments(client, token, team_id):
    return client.get(f'/api/teams/{team_id}/moments', headers=auth_headers(token))


def complete_full_mission(client, token):
    daily = client.get('/api/daily', headers=auth_headers(token)).get_json()
    exercise_keys = [ex['key'] for ex in daily['exercises']]
    assert len(exercise_keys) == 5
    last_resp = None
    for key in exercise_keys:
        last_resp = client.post(f'/api/daily/{key}/complete', headers=auth_headers(token))
    return last_resp, exercise_keys


def test_team_creation_creates_moment(client):
    token = register_and_login(client, 'creator')
    team = create_team(client, token, 'Hill Family').get_json()['team']

    moments = get_moments(client, token, team['id']).get_json()
    assert len(moments) == 1
    assert moments[0]['moment_type'] == 'team_created'
    assert moments[0]['subject_username'] == 'creator'
    assert moments[0]['display_text'] == 'creator created the team'


def test_joining_creates_moment(client):
    creator_token = register_and_login(client, 'creator')
    member_token = register_and_login(client, 'member')
    team = create_team(client, creator_token, 'Hill Family').get_json()['team']
    join_team(client, member_token, team['id'], team['invite_code'])

    moments = get_moments(client, creator_token, team['id']).get_json()
    types = [m['moment_type'] for m in moments]
    assert types.count('member_joined') == 1
    joined_moment = next(m for m in moments if m['moment_type'] == 'member_joined')
    assert joined_moment['subject_username'] == 'member'


def test_leaving_creates_moment(client):
    creator_token = register_and_login(client, 'creator')
    member_token = register_and_login(client, 'member')
    team = create_team(client, creator_token, 'Hill Family').get_json()['team']
    join_team(client, member_token, team['id'], team['invite_code'])

    client.post(f"/api/teams/{team['id']}/leave", headers=auth_headers(member_token))

    # The member who left can no longer read moments (not a member anymore)
    # -- check via the creator, who's still in the team.
    moments = get_moments(client, creator_token, team['id']).get_json()
    left_moment = next(m for m in moments if m['moment_type'] == 'member_left')
    assert left_moment['subject_username'] == 'member'


def test_campfire_log_creates_moment(client):
    token = register_and_login(client, 'creator')
    team = create_team(client, token, 'Hill Family').get_json()['team']

    complete_full_mission(client, token)

    moments = get_moments(client, token, team['id']).get_json()
    log_moment = next(m for m in moments if m['moment_type'] == 'campfire_log_added')
    assert log_moment['subject_username'] == 'creator'
    assert log_moment['metadata']['total_team_missions'] == 1
    assert log_moment['display_text'] == 'creator added a log to the campfire'


def test_campfire_stage_threshold_creates_moment(client, app):
    token = register_and_login(client, 'creator')
    team = create_team(client, token, 'Hill Family').get_json()['team']

    # Push the campfire to 99 directly -- Kindling caps at 99, Small Flame
    # starts at 100 (TEAM_SYSTEM_BASELINE Section 2's locked thresholds).
    # One more completion should cross it.
    with app.app_context():
        campfire = db.session.execute(
            db.select(TeamCampfire).where(TeamCampfire.team_id == team['id'])
        ).scalar_one()
        campfire.total_team_missions = 99
        db.session.commit()

    complete_full_mission(client, token)

    campfire_resp = client.get(f"/api/teams/{team['id']}/campfire", headers=auth_headers(token))
    assert campfire_resp.get_json()['stage'] == 'Small Flame'

    moments = get_moments(client, token, team['id']).get_json()
    stage_moments = [m for m in moments if m['moment_type'] == 'campfire_stage_reached']
    assert len(stage_moments) == 1
    assert stage_moments[0]['subject_username'] is None  # team-wide, not one person
    assert stage_moments[0]['metadata']['stage'] == 'Small Flame'
    assert stage_moments[0]['display_text'] == 'The campfire reached Small Flame'


def test_no_stage_moment_when_threshold_not_crossed(client):
    """Confirms the stage moment isn't created on every log -- only on 4
    (Kindling covers 0-99) does a completion NOT create one."""
    token = register_and_login(client, 'creator')
    team = create_team(client, token, 'Hill Family').get_json()['team']

    complete_full_mission(client, token)

    moments = get_moments(client, token, team['id']).get_json()
    stage_moments = [m for m in moments if m['moment_type'] == 'campfire_stage_reached']
    assert stage_moments == []


def test_unauthorized_user_cannot_read_moments(client):
    creator_token = register_and_login(client, 'creator')
    outsider_token = register_and_login(client, 'outsider')
    team = create_team(client, creator_token, 'Hill Family').get_json()['team']

    resp = get_moments(client, outsider_token, team['id'])
    assert resp.status_code == 403


def test_no_duplicate_stage_moment_on_repeated_completion(client, app):
    token = register_and_login(client, 'creator')
    team = create_team(client, token, 'Hill Family').get_json()['team']

    with app.app_context():
        campfire = db.session.execute(
            db.select(TeamCampfire).where(TeamCampfire.team_id == team['id'])
        ).scalar_one()
        campfire.total_team_missions = 99
        db.session.commit()

    _, exercise_keys = complete_full_mission(client, token)

    moments_after_first = get_moments(client, token, team['id']).get_json()
    stage_count_after_first = len([m for m in moments_after_first if m['moment_type'] == 'campfire_stage_reached'])
    assert stage_count_after_first == 1

    # Replay an already-completed exercise -- should not create a second
    # stage moment (or a second log moment, since the campfire loop only
    # runs inside the "not existing" / completed_count == 5 branch).
    client.post(f'/api/daily/{exercise_keys[0]}/complete', headers=auth_headers(token))

    moments_after_replay = get_moments(client, token, team['id']).get_json()
    stage_count_after_replay = len([m for m in moments_after_replay if m['moment_type'] == 'campfire_stage_reached'])
    log_count_after_replay = len([m for m in moments_after_replay if m['moment_type'] == 'campfire_log_added'])
    assert stage_count_after_replay == 1
    assert log_count_after_replay == 1


def test_moments_ordered_newest_first(client):
    token = register_and_login(client, 'creator')
    team = create_team(client, token, 'Hill Family').get_json()['team']
    complete_full_mission(client, token)

    moments = get_moments(client, token, team['id']).get_json()
    timestamps = [m['occurred_at'] for m in moments]
    assert timestamps == sorted(timestamps, reverse=True)
    # team_created happened first chronologically, so it should be last here.
    assert moments[-1]['moment_type'] == 'team_created'
