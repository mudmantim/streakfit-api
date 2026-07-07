"""R2.1 Team Foundations — covers exactly the verification list from the brief:
create team, invite code generated, join via code, leave team, multiple teams
per user, 10-team cap enforced, 8-member cap enforced, Plus override logic.
Plus a handful of correctness checks (wrong code, permissions, duplicates)
that the manual smoke pass also exercised.
"""
from conftest import register_and_login, auth_headers

from app import db, User


def create_team(client, token, name):
    return client.post('/api/teams', json={'name': name}, headers=auth_headers(token))


def join_team(client, token, team_id, code):
    return client.post(f'/api/teams/{team_id}/join', json={'code': code}, headers=auth_headers(token))


def test_create_team(client):
    token = register_and_login(client, 'creator')
    resp = create_team(client, token, 'Hill Family')
    assert resp.status_code == 201
    body = resp.get_json()['team']
    assert body['name'] == 'Hill Family'
    assert 'id' in body


def test_invite_code_generated_on_create(client):
    token = register_and_login(client, 'creator')
    resp = create_team(client, token, 'Hill Family')
    code = resp.get_json()['team']['invite_code']
    assert isinstance(code, str)
    assert len(code) == 6
    assert code.isalnum()


def test_join_via_code(client):
    creator_token = register_and_login(client, 'creator')
    member_token = register_and_login(client, 'member')

    team = create_team(client, creator_token, 'Hill Family').get_json()['team']
    resp = join_team(client, member_token, team['id'], team['invite_code'])
    assert resp.status_code == 200

    detail = client.get(f"/api/teams/{team['id']}", headers=auth_headers(creator_token)).get_json()
    assert detail['member_count'] == 2


def test_join_with_wrong_code_rejected(client):
    creator_token = register_and_login(client, 'creator')
    member_token = register_and_login(client, 'member')
    team = create_team(client, creator_token, 'Hill Family').get_json()['team']

    resp = join_team(client, member_token, team['id'], 'WRONGCODE')
    assert resp.status_code == 403


def test_join_twice_rejected(client):
    creator_token = register_and_login(client, 'creator')
    member_token = register_and_login(client, 'member')
    team = create_team(client, creator_token, 'Hill Family').get_json()['team']

    join_team(client, member_token, team['id'], team['invite_code'])
    resp = join_team(client, member_token, team['id'], team['invite_code'])
    assert resp.status_code == 400


def test_leave_team(client):
    creator_token = register_and_login(client, 'creator')
    member_token = register_and_login(client, 'member')
    team = create_team(client, creator_token, 'Hill Family').get_json()['team']
    join_team(client, member_token, team['id'], team['invite_code'])

    resp = client.post(f"/api/teams/{team['id']}/leave", headers=auth_headers(member_token))
    assert resp.status_code == 200

    # Access is revoked immediately after leaving.
    detail_resp = client.get(f"/api/teams/{team['id']}", headers=auth_headers(member_token))
    assert detail_resp.status_code == 403


def test_creator_can_leave_own_team(client):
    """No admin succession logic in R2.1 (TEAM_SYSTEM_BASELINE Section 4) —
    the creator can leave freely, same as anyone else; the team just keeps
    existing without an active safety-power-holder until someone addresses it."""
    creator_token = register_and_login(client, 'creator')
    team = create_team(client, creator_token, 'Hill Family').get_json()['team']

    resp = client.post(f"/api/teams/{team['id']}/leave", headers=auth_headers(creator_token))
    assert resp.status_code == 200


def test_multiple_teams_per_user(client):
    token = register_and_login(client, 'creator')
    create_team(client, token, 'Hill Family')
    create_team(client, token, 'Beta Testers')

    teams = client.get('/api/teams', headers=auth_headers(token)).get_json()
    assert len(teams) == 2
    assert {t['name'] for t in teams} == {'Hill Family', 'Beta Testers'}


def test_ten_team_cap_enforced_for_free_users(client):
    token = register_and_login(client, 'creator')
    for i in range(10):
        resp = create_team(client, token, f'Team {i}')
        assert resp.status_code == 201

    resp = create_team(client, token, 'Team 11')
    assert resp.status_code == 403
    assert 'error' in resp.get_json()


def test_eight_member_cap_enforced_for_free_team(client):
    creator_token = register_and_login(client, 'creator')
    team = create_team(client, creator_token, 'Hill Family').get_json()['team']

    # creator is member #1; fill to 8 total with 7 more joins
    for i in range(7):
        member_token = register_and_login(client, f'member{i}')
        resp = join_team(client, member_token, team['id'], team['invite_code'])
        assert resp.status_code == 200

    overflow_token = register_and_login(client, 'overflow')
    resp = join_team(client, overflow_token, team['id'], team['invite_code'])
    assert resp.status_code == 403
    assert 'error' in resp.get_json()


def test_plus_override_raises_member_cap_for_whole_team(client, app):
    creator_token = register_and_login(client, 'creator')
    team = create_team(client, creator_token, 'Hill Family').get_json()['team']

    for i in range(7):
        member_token = register_and_login(client, f'member{i}')
        join_team(client, member_token, team['id'], team['invite_code'])

    # Team is now at the free cap (8). Upgrade the creator to Plus directly —
    # no billing integration exists yet, this is the only way to set it.
    with app.app_context():
        creator = db.session.execute(
            db.select(User).where(User.username == 'creator')
        ).scalar_one()
        creator.is_plus = True
        db.session.commit()

    detail = client.get(f"/api/teams/{team['id']}", headers=auth_headers(creator_token)).get_json()
    assert detail['member_cap'] == 25

    # The join that was rejected before the upgrade now succeeds.
    overflow_token = register_and_login(client, 'overflow')
    resp = join_team(client, overflow_token, team['id'], team['invite_code'])
    assert resp.status_code == 200


def test_plus_user_has_no_team_count_cap(client, app):
    token = register_and_login(client, 'creator')
    with app.app_context():
        user = db.session.execute(db.select(User).where(User.username == 'creator')).scalar_one()
        user.is_plus = True
        db.session.commit()

    for i in range(12):  # more than the free 10-team cap
        resp = create_team(client, token, f'Team {i}')
        assert resp.status_code == 201


def test_rotate_invite_is_creator_only(client):
    creator_token = register_and_login(client, 'creator')
    member_token = register_and_login(client, 'member')
    team = create_team(client, creator_token, 'Hill Family').get_json()['team']
    join_team(client, member_token, team['id'], team['invite_code'])

    forbidden = client.post(f"/api/teams/{team['id']}/rotate-invite", headers=auth_headers(member_token))
    assert forbidden.status_code == 403

    allowed = client.post(f"/api/teams/{team['id']}/rotate-invite", headers=auth_headers(creator_token))
    assert allowed.status_code == 200
    new_code = allowed.get_json()['invite_code']
    assert new_code != team['invite_code']


def test_rotated_code_invalidates_old_code(client):
    creator_token = register_and_login(client, 'creator')
    member_token = register_and_login(client, 'member')
    team = create_team(client, creator_token, 'Hill Family').get_json()['team']
    old_code = team['invite_code']

    client.post(f"/api/teams/{team['id']}/rotate-invite", headers=auth_headers(creator_token))

    resp = join_team(client, member_token, team['id'], old_code)
    assert resp.status_code == 403


def test_campfire_stage_derivation(client):
    """Stage is computed on read from total_team_missions (TEAM_SYSTEM_BASELINE
    Section 2) — new team starts at Kindling with zero missions."""
    token = register_and_login(client, 'creator')
    team = create_team(client, token, 'Hill Family').get_json()['team']

    campfire = client.get(f"/api/teams/{team['id']}/campfire", headers=auth_headers(token)).get_json()
    assert campfire['total_team_missions'] == 0
    assert campfire['stage'] == 'Kindling'


def test_non_member_forbidden_from_team_detail(client):
    creator_token = register_and_login(client, 'creator')
    outsider_token = register_and_login(client, 'outsider')
    team = create_team(client, creator_token, 'Hill Family').get_json()['team']

    resp = client.get(f"/api/teams/{team['id']}", headers=auth_headers(outsider_token))
    assert resp.status_code == 403


def test_lookup_by_code_returns_preview_without_joining(client):
    creator_token = register_and_login(client, 'creator')
    outsider_token = register_and_login(client, 'outsider')
    team = create_team(client, creator_token, 'Hill Family').get_json()['team']

    resp = client.get(f"/api/teams/lookup/{team['invite_code']}", headers=auth_headers(outsider_token))
    assert resp.status_code == 200
    preview = resp.get_json()
    assert preview['name'] == 'Hill Family'
    assert preview['member_count'] == 1

    # Preview alone doesn't create membership.
    detail = client.get(f"/api/teams/{team['id']}", headers=auth_headers(outsider_token))
    assert detail.status_code == 403
