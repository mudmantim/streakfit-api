"""R2.6 Rickie Team Reactions MVP -- Rickie occasionally posts into team
chat (via the existing team_message table) for a small, fixed set of
meaningful events: member_joined, the team's first-ever campfire log, and
campfire_stage_reached. No AI generation (fixed templates only), no new
tables, and no post on every ordinary campfire_log_added -- that's the
exact noise this sprint is built to avoid.
"""
from conftest import register_and_login, auth_headers

from app import app as flask_app, db, TeamCampfire


def create_team(client, token, name):
    return client.post('/api/teams', json={'name': name}, headers=auth_headers(token))


def join_team(client, token, team_id, code):
    return client.post(f'/api/teams/{team_id}/join', json={'code': code}, headers=auth_headers(token))


def get_messages(client, token, team_id):
    return client.get(f'/api/teams/{team_id}/messages', headers=auth_headers(token))


def complete_full_mission(client, token):
    daily = client.get('/api/daily', headers=auth_headers(token)).get_json()
    exercise_keys = [ex['key'] for ex in daily['exercises']]
    assert len(exercise_keys) == 5
    last_resp = None
    for key in exercise_keys:
        last_resp = client.post(f'/api/daily/{key}/complete', headers=auth_headers(token))
    return last_resp, exercise_keys


def test_joining_creates_one_rickie_message(client):
    creator_token = register_and_login(client, 'creator')
    member_token = register_and_login(client, 'member')
    team = create_team(client, creator_token, 'Hill Family').get_json()['team']

    join_team(client, member_token, team['id'], team['invite_code'])

    messages = get_messages(client, creator_token, team['id']).get_json()
    rickie_messages = [m for m in messages if m['sender_type'] == 'rickie']
    assert len(rickie_messages) == 1
    assert rickie_messages[0]['sender_username'] is None
    assert rickie_messages[0]['body'] in (
        "Welcome to the campfire.",
        "Glad you're here.",
    )


def test_first_log_creates_one_rickie_message(client):
    token = register_and_login(client, 'creator')
    team = create_team(client, token, 'Hill Family').get_json()['team']

    complete_full_mission(client, token)

    messages = get_messages(client, token, team['id']).get_json()
    rickie_messages = [m for m in messages if m['sender_type'] == 'rickie']
    assert len(rickie_messages) == 1
    assert rickie_messages[0]['body'] == "First log added. The fire is starting."


def test_stage_reached_creates_one_rickie_message(client, app):
    token = register_and_login(client, 'creator')
    team = create_team(client, token, 'Hill Family').get_json()['team']

    with app.app_context():
        campfire = db.session.execute(
            db.select(TeamCampfire).where(TeamCampfire.team_id == team['id'])
        ).scalar_one()
        campfire.total_team_missions = 99
        db.session.commit()

    complete_full_mission(client, token)

    messages = get_messages(client, token, team['id']).get_json()
    rickie_messages = [m for m in messages if m['sender_type'] == 'rickie']
    assert len(rickie_messages) == 1
    assert rickie_messages[0]['body'] in (
        "The campfire grew brighter.",
        "You built this together.",
    )


def test_repeated_ordinary_logs_do_not_spam_chat(client, app):
    """Only the first-ever log gets a Rickie message. A second completion
    that doesn't cross a stage threshold should add zero new Rickie posts."""
    token = register_and_login(client, 'creator')
    team = create_team(client, token, 'Hill Family').get_json()['team']

    complete_full_mission(client, token)
    messages_after_first = get_messages(client, token, team['id']).get_json()
    rickie_after_first = [m for m in messages_after_first if m['sender_type'] == 'rickie']
    assert len(rickie_after_first) == 1

    user_id = client.get('/api/me', headers=auth_headers(token)).get_json()['id']

    # Advance to a new day so a second full mission can be completed.
    with app.app_context():
        from app import DailyCompletion
        from datetime import date, timedelta
        db.session.execute(
            db.update(DailyCompletion)
            .where(DailyCompletion.user_id == user_id)
            .values(date=date.today() - timedelta(days=1))
        )
        db.session.commit()

    complete_full_mission(client, token)
    messages_after_second = get_messages(client, token, team['id']).get_json()
    rickie_after_second = [m for m in messages_after_second if m['sender_type'] == 'rickie']
    assert len(rickie_after_second) == 1  # still just the one from the first log


def test_rickie_messages_have_null_sender_user_id_via_api(client):
    """API-level proxy for sender_user_id IS NULL: sender_username is None
    for a Rickie message even though the row conceptually "belongs" to no
    user account."""
    creator_token = register_and_login(client, 'creator')
    member_token = register_and_login(client, 'member')
    team = create_team(client, creator_token, 'Hill Family').get_json()['team']
    join_team(client, member_token, team['id'], team['invite_code'])

    messages = get_messages(client, creator_token, team['id']).get_json()
    rickie_messages = [m for m in messages if m['sender_type'] == 'rickie']
    assert len(rickie_messages) == 1
    assert rickie_messages[0]['sender_username'] is None


def test_rickie_message_sender_user_id_is_null_in_db(client, app):
    creator_token = register_and_login(client, 'creator')
    member_token = register_and_login(client, 'member')
    team = create_team(client, creator_token, 'Hill Family').get_json()['team']
    join_team(client, member_token, team['id'], team['invite_code'])

    with app.app_context():
        from app import TeamMessage
        rickie_row = db.session.execute(
            db.select(TeamMessage).where(TeamMessage.team_id == team['id'], TeamMessage.sender_type == 'rickie')
        ).scalar_one()
        assert rickie_row.sender_user_id is None
