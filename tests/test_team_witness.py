"""The witness layer: whether the people you share a campfire with moved today.

Teams v1 called this the witness-only model — name, today's status, streak
number. It is the reason a family joins a team at all, and until now the roster
returned none of it. These tests pin both the data and, just as importantly,
what the data must NOT become: an ordering, a ranking, or a report on who
failed to show up.
"""
import contextlib
import datetime

from sqlalchemy import event

from app import DailyCompletion, User, db
from conftest import auth_headers, register_and_login

FIVE_KEYS = ['knee_push_up', 'bodyweight_squat', 'superman', 'childs_pose', 'jumping_jack']


@contextlib.contextmanager
def count_queries():
    n = [0]

    def _cb(conn, cursor, statement, params, context, executemany):
        n[0] += 1

    event.listen(db.engine, "after_cursor_execute", _cb)
    try:
        yield n
    finally:
        event.remove(db.engine, "after_cursor_execute", _cb)


def _uid(username):
    return User.query.filter_by(username=username).first().id


def _seed_mission_days(user_id, day_offsets):
    for off in day_offsets:
        when = datetime.date.today() - datetime.timedelta(days=off)
        for key in FIVE_KEYS:
            db.session.add(DailyCompletion(user_id=user_id, date=when, exercise_key=key))
    db.session.commit()


def _complete_today(client, token, count=5):
    daily = client.get('/api/daily', headers=auth_headers(token)).get_json()
    for ex in daily['exercises'][:count]:
        client.post(f"/api/daily/{ex['key']}/complete", headers=auth_headers(token))


def _make_team(client, token, name='The Hills'):
    resp = client.post('/api/teams', json={'name': name}, headers=auth_headers(token))
    return resp.get_json()['team']


def _members_by_name(client, token, team_id):
    resp = client.get(f'/api/teams/{team_id}', headers=auth_headers(token))
    return {m['username']: m for m in resp.get_json()['members']}


# ── The data the roster is for ─────────────────────────────────────────────

def test_roster_reports_who_moved_today_and_how_long_theyve_been_going(client):
    token = register_and_login(client, 'parent')
    team = _make_team(client, token)
    kid_token = register_and_login(client, 'kid')
    client.post(f"/api/teams/{team['id']}/join",
                json={'code': team['invite_code']}, headers=auth_headers(kid_token))

    _seed_mission_days(_uid('kid'), [2, 1])   # two prior days...
    _complete_today(client, kid_token)        # ...and today makes three

    kid = _members_by_name(client, token, team['id'])['kid']
    assert kid['completed_today'] is True
    assert kid['completed_today_count'] == 5
    assert kid['current_streak'] == 3


def test_roster_shows_partial_progress_so_starting_is_visible(client):
    token = register_and_login(client, 'p2')
    team = _make_team(client, token)
    kid_token = register_and_login(client, 'kid2')
    client.post(f"/api/teams/{team['id']}/join",
                json={'code': team['invite_code']}, headers=auth_headers(kid_token))

    _complete_today(client, kid_token, count=2)

    kid = _members_by_name(client, token, team['id'])['kid2']
    assert kid['completed_today'] is False
    assert kid['completed_today_count'] == 2


def test_a_member_who_hasnt_started_today_keeps_yesterdays_streak(client):
    """Mid-day, an untouched streak is still alive. Never punish who showed up."""
    token = register_and_login(client, 'p3')
    team = _make_team(client, token)
    quiet_token = register_and_login(client, 'quiet_one')
    client.post(f"/api/teams/{team['id']}/join",
                json={'code': team['invite_code']}, headers=auth_headers(quiet_token))

    _seed_mission_days(_uid('quiet_one'), [3, 2, 1])   # nothing yet today

    member = _members_by_name(client, token, team['id'])['quiet_one']
    assert member['completed_today'] is False
    assert member['completed_today_count'] == 0
    assert member['current_streak'] == 3   # not zeroed just because today is young


def test_team_list_counts_who_moved_without_naming_who_didnt(client):
    token = register_and_login(client, 'p4')
    team = _make_team(client, token)
    for name in ('m1', 'm2', 'm3'):
        t = register_and_login(client, name)
        client.post(f"/api/teams/{team['id']}/join",
                    json={'code': team['invite_code']}, headers=auth_headers(t))
        if name == 'm1':
            _complete_today(client, t)

    listing = client.get('/api/teams', headers=auth_headers(token)).get_json()
    row = next(r for r in listing if r['id'] == team['id'])
    assert row['member_count'] == 4
    assert row['moved_today'] == 1
    # A count, not a roster of the absent.
    assert 'missed_today' not in row
    assert 'inactive_members' not in row


# ── What it must not become ────────────────────────────────────────────────

def test_roster_is_not_ordered_by_who_is_doing_best(client):
    """Ordering by streak would quietly turn the roster into a leaderboard,
    which every team design document rules out. Creator first, then join order."""
    token = register_and_login(client, 'creator_last_place')
    team = _make_team(client, token)
    strong = register_and_login(client, 'big_streak')
    client.post(f"/api/teams/{team['id']}/join",
                json={'code': team['invite_code']}, headers=auth_headers(strong))
    _seed_mission_days(_uid('big_streak'), [5, 4, 3, 2, 1])

    members = client.get(f"/api/teams/{team['id']}",
                         headers=auth_headers(token)).get_json()['members']

    assert members[0]['username'] == 'creator_last_place'
    assert members[0]['current_streak'] == 0
    assert members[1]['username'] == 'big_streak'
    assert members[1]['current_streak'] == 5


def test_roster_witness_does_not_reintroduce_an_n_plus_1(client):
    token = register_and_login(client, 'host')
    team = _make_team(client, token)
    for i in range(7):
        t = register_and_login(client, f'member_{i}')
        client.post(f"/api/teams/{team['id']}/join",
                    json={'code': team['invite_code']}, headers=auth_headers(t))
        _seed_mission_days(_uid(f'member_{i}'), [2, 1])

    with count_queries() as n:
        resp = client.get(f"/api/teams/{team['id']}", headers=auth_headers(token))

    assert resp.status_code == 200
    assert len(resp.get_json()['members']) == 8
    assert n[0] <= 9, f"query count {n[0]} scales with members — witness added an N+1"


def test_team_list_witness_does_not_scale_with_teams(client):
    token = register_and_login(client, 'joiner')
    for i in range(6):
        owner = register_and_login(client, f'owner_{i}')
        team = _make_team(client, owner, name=f'T{i}')
        client.post(f"/api/teams/{team['id']}/join",
                    json={'code': team['invite_code']}, headers=auth_headers(token))

    with count_queries() as n:
        resp = client.get('/api/teams', headers=auth_headers(token))

    assert resp.status_code == 200
    assert len(resp.get_json()) == 6
    assert n[0] <= 8, f"query count {n[0]} scales with teams — list_teams regressed"


def test_witness_helper_handles_a_member_with_no_history(client):
    """A brand-new member must not blow up or look like a failure."""
    import app as appmod

    brand_new = User(username='never_moved', password_hash='x')
    db.session.add(brand_new)
    db.session.commit()

    witness = appmod._witness_for_ids([brand_new.id])

    assert witness[brand_new.id] == {
        'completed_today_count': 0,
        'completed_today': False,
        'current_streak': 0,
    }


def test_witness_helper_is_empty_for_no_ids(client):
    import app as appmod

    assert appmod._witness_for_ids([]) == {}
    assert appmod._witness_for_ids([None]) == {}
