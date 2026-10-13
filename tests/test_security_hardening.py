"""Security hardening, 2026-10-12 (overnight audit findings S3, S4 and D1).

S4  Malformed credentials are a 400, never a 500. A number, list or object
    where a string belongs used to reach `.strip()`, `len()` or the password
    hasher and come back as "Internal server error".
S3  The seven moderation routes are rate limited. They checked X-Admin-Secret
    correctly but had no throttle at all; failures are now charged to one
    bucket shared by all seven, so guesses cannot be spread across routes.
D1  Effort and skill changes name the day the page is showing. A page open
    since yesterday could rebuild or ease TODAY's five after midnight; now it
    gets 409 day_changed and nothing is written. A page that sends no day (a
    client from before this change) is served exactly as before.
"""
import datetime
import logging
import pathlib

import pytest

import app as appmod
from app import DailyEffort, DailyMission, User, db
from conftest import auth_headers, register_and_login

REPO = pathlib.Path(__file__).resolve().parents[1]
D = datetime.date(2031, 5, 12)
ONE = datetime.timedelta(days=1)
PASSWORD = 'WalkTest-Lantern-123!'


class _Clock:
    def __init__(self, day=D, hour=12):
        self.day, self.hour = day, hour

    def now(self):
        return datetime.datetime.combine(self.day, datetime.time(self.hour, 0))


@pytest.fixture()
def clock(monkeypatch):
    c = _Clock()
    monkeypatch.setattr(appmod, 'server_clock', c)
    return c


def _user(client, name):
    h = auth_headers(register_and_login(client, name, PASSWORD))
    uid = db.session.execute(db.select(User.id).where(User.username == name)).scalar()
    return uid, h


# ── S4: malformed credentials ────────────────────────────────────────────────

MALFORMED_BODIES = [
    [1, 2],
    'just a string',
    5,
    {'username': 123, 'password': 'abcdefgh1'},
    {'username': ['alice'], 'password': 'abcdefgh1'},
    {'username': {'a': 1}, 'password': 'abcdefgh1'},
    {'username': 'alice', 'password': 12345678},
    {'username': 'alice', 'password': ['abcdefgh1']},
    {'username': 'alice', 'password': True},
]


@pytest.mark.parametrize('body', MALFORMED_BODIES)
def test_malformed_registration_is_a_400(client, body):
    r = client.post('/api/register', json=body)
    assert r.status_code == 400, r.get_data(as_text=True)
    assert db.session.execute(db.select(db.func.count(User.id))).scalar() == 0


@pytest.mark.parametrize('body', MALFORMED_BODIES)
def test_malformed_login_is_a_400(client, body):
    client.post('/api/register', json={'username': 'alice', 'password': PASSWORD})
    r = client.post('/api/login', json=body)
    assert r.status_code == 400, r.get_data(as_text=True)


@pytest.mark.parametrize('route', ['/api/register', '/api/login'])
def test_a_body_that_is_not_json_is_a_400(client, route):
    r = client.post(route, data='username=alice&password=x',
                    content_type='application/x-www-form-urlencoded')
    assert r.status_code == 400


def test_well_formed_credentials_behave_as_before(client):
    assert client.post('/api/register',
                       json={'username': '  bob  ', 'password': PASSWORD}).status_code == 201
    # registration still trims the name
    assert client.post('/api/login', json={'username': 'bob', 'password': PASSWORD}).status_code == 200
    assert client.post('/api/login', json={'username': 'bob', 'password': 'wrong-pass'}).status_code == 401
    assert client.post('/api/login', json={'username': 'bob'}).status_code == 400
    assert client.post('/api/register', json={'username': 'bob', 'password': PASSWORD}).status_code == 400


@pytest.mark.parametrize('kwargs', [
    {'json': {'password': 12345678}},
    {'json': {'password': ['x']}},
    {'json': [PASSWORD]},
    {'data': 'not json', 'content_type': 'application/json'},
])
def test_malformed_account_deletion_is_a_400_and_deletes_nothing(client, kwargs):
    uid, h = _user(client, 'carol')
    r = client.delete('/api/me', headers=h, **kwargs)
    assert r.status_code == 400, r.get_data(as_text=True)
    assert db.session.get(User, uid) is not None


def test_account_deletion_keeps_its_existing_answers(client):
    uid, h = _user(client, 'dave')
    assert client.delete('/api/me', headers=h).status_code == 403            # no body
    assert client.delete('/api/me', headers=h, json={}).status_code == 403   # no password
    assert client.delete('/api/me', headers=h, json={'password': 'nope-nope'}).status_code == 403
    assert db.session.get(User, uid) is not None
    r = client.delete('/api/me', headers=h, json={'password': PASSWORD})
    assert r.status_code == 200 and r.get_json()['deleted'] is True


@pytest.mark.parametrize('body', [
    [1], {'skill_level': ['beginner']}, {'display_mode': {'x': 1}}, {'rickie_mode': 3}])
def test_malformed_profile_update_is_a_400(client, body):
    _uid, h = _user(client, 'erin')
    assert client.patch('/api/me', headers=h, json=body).status_code == 400


def test_malformed_effort_body_is_a_400(client):
    _uid, h = _user(client, 'fay')
    assert client.put('/api/daily/effort', headers=h, json=['easy']).status_code == 400


# ── S3: moderation route throttles ───────────────────────────────────────────

SECRET = 'test-admin-secret-' + 'x' * 24
MODERATION_ROUTES = [
    ('GET', '/api/admin/reports'),
    ('GET', '/api/admin/reports/nope'),
    ('GET', '/api/admin/reports/nope/photo-evidence'),
    ('POST', '/api/admin/reports/nope/legal-hold'),
    ('POST', '/api/admin/reports/nope/action'),
    ('GET', '/api/admin/appeals'),
    ('POST', '/api/admin/appeals/nope/decide'),
]


@pytest.fixture()
def limiter_on(monkeypatch):
    monkeypatch.setenv('ADMIN_SECRET', SECRET)
    appmod.limiter.reset()
    appmod.limiter.enabled = True
    yield
    appmod.limiter.enabled = False
    appmod.limiter.reset()


def _call(client, method, path, secret=None, ip='10.0.0.1'):
    headers = {'X-Admin-Secret': secret} if secret is not None else {}
    return client.open(path, method=method, headers=headers, json={},
                       environ_overrides={'REMOTE_ADDR': ip})


@pytest.mark.parametrize('method,path', MODERATION_ROUTES)
def test_authorization_is_unchanged(client, limiter_on, method, path):
    assert _call(client, method, path).status_code == 403
    assert _call(client, method, path, secret='wrong').status_code == 403
    assert _call(client, method, path, secret=SECRET).status_code not in (403, 429)


def test_wrong_secrets_are_throttled_across_all_seven_routes_together(client, limiter_on):
    seen = []
    for i in range(14):
        method, path = MODERATION_ROUTES[i % len(MODERATION_ROUTES)]
        seen.append(_call(client, method, path, secret='guess-%d' % i).status_code)
    assert seen[:10] == [403] * 10, seen
    assert seen[10:] == [429] * 4, seen


def test_requests_without_a_secret_are_refused_but_not_charged(client, limiter_on):
    """The verification suite checks the no-secret refusal on every route. Those
    are not guesses, and must not spend the operator's failure budget."""
    for _ in range(3):
        for method, path in MODERATION_ROUTES:
            assert _call(client, method, path).status_code == 403
    assert _call(client, 'GET', '/api/admin/reports', secret=SECRET).status_code == 200
    seen = [_call(client, 'GET', '/api/admin/appeals', secret='guess-%d' % i).status_code
            for i in range(11)]
    assert seen == [403] * 10 + [429], seen


def test_the_failure_bucket_is_per_address(client, limiter_on):
    for i in range(11):
        _call(client, 'GET', '/api/admin/reports', secret='guess-%d' % i, ip='10.0.0.66')
    assert _call(client, 'GET', '/api/admin/reports', secret=SECRET,
                 ip='10.0.0.66').status_code == 429
    assert _call(client, 'GET', '/api/admin/reports', secret=SECRET,
                 ip='10.0.0.7').status_code == 200


def test_an_operator_with_the_right_secret_is_never_charged_for_failures(client, limiter_on):
    for _ in range(40):
        assert _call(client, 'GET', '/api/admin/reports', secret=SECRET).status_code == 200


def test_a_write_route_has_its_own_ceiling(client, limiter_on):
    codes = [_call(client, 'POST', '/api/admin/reports/nope/action', secret=SECRET).status_code
             for _ in range(31)]
    assert 429 not in codes[:30], codes
    assert codes[30] == 429, codes


# ── D1: effort and skill name their day ─────────────────────────────────────

def _mission(uid, day):
    m = db.session.get(DailyMission, (uid, day))
    db.session.refresh(m)
    return (m.effort, m.skill_level, m.revision, [s['key'] for s in m.slots])


def _yesterday_page_after_midnight(client, clock, name):
    uid, h = _user(client, name)
    page = client.get('/api/daily', headers=h).get_json()
    assert page['mission_date'] == D.isoformat()
    clock.day = D + ONE
    clock.hour = 0
    client.get('/api/daily', headers=h)            # the new day exists, unseen by this page
    return uid, h, page['mission_date']


@pytest.mark.parametrize('level', ['easy', 'more'])
def test_a_stale_effort_choice_changes_nothing(client, clock, level):
    uid, h, stale = _yesterday_page_after_midnight(client, clock, 'gus_' + level)
    before = _mission(uid, D + ONE)
    r = client.put('/api/daily/effort', headers=h, json={'level': level, 'mission_date': stale})
    assert r.status_code == 409
    body = r.get_json()
    assert body['error'] == 'day_changed'
    assert body['mission_date'] == stale and body['today'] == (D + ONE).isoformat()
    assert _mission(uid, D + ONE) == before
    assert db.session.execute(db.select(DailyEffort).where(
        DailyEffort.user_id == uid)).first() is None


def test_a_stale_skill_change_changes_nothing_not_even_the_theme(client, clock):
    uid, h, stale = _yesterday_page_after_midnight(client, clock, 'hal')
    before = _mission(uid, D + ONE)
    r = client.patch('/api/me', headers=h, json={
        'skill_level': 'advanced', 'display_mode': 'classic', 'mission_date': stale})
    assert r.status_code == 409 and r.get_json()['error'] == 'day_changed'
    user = db.session.get(User, uid)
    db.session.refresh(user)
    assert user.skill_level == 'beginner' and user.display_mode != 'classic'
    assert _mission(uid, D + ONE) == before


def test_a_current_page_still_changes_effort_and_skill(client, clock):
    uid, h = _user(client, 'ivy')
    today = client.get('/api/daily', headers=h).get_json()['mission_date']
    r = client.put('/api/daily/effort', headers=h, json={'level': 'easy', 'mission_date': today})
    assert r.status_code == 200 and r.get_json()['applies'] == 'today'
    assert _mission(uid, D)[0] == 'easy'
    r = client.patch('/api/me', headers=h, json={'skill_level': 'advanced', 'mission_date': today})
    assert r.status_code == 200 and r.get_json()['skill_applies'] == 'today'
    assert _mission(uid, D)[1] == 'advanced'


@pytest.mark.parametrize('sent,error', [
    ((D + ONE).isoformat(), 'mission_date_in_future'),
    ('yesterday', 'invalid_mission_date'),
    (20310512, 'invalid_mission_date'),
])
def test_an_impossible_day_is_a_400(client, clock, sent, error):
    uid, h = _user(client, 'jo_' + error[:7])
    client.get('/api/daily', headers=h)
    before = _mission(uid, D)
    for call in (lambda: client.put('/api/daily/effort', headers=h,
                                    json={'level': 'easy', 'mission_date': sent}),
                 lambda: client.patch('/api/me', headers=h,
                                      json={'skill_level': 'advanced', 'mission_date': sent})):
        r = call()
        assert r.status_code == 400 and r.get_json()['error'] == error
    assert _mission(uid, D) == before


@pytest.mark.parametrize('mission_date_field', ['absent', 'null'])
def test_an_old_client_without_a_day_is_served_exactly_as_before(
        client, clock, caplog, mission_date_field):
    """A page loaded before this deploy sends no mission_date. It must keep
    working -- same 200, same rebuild -- and be visible in the log."""
    uid, h = _user(client, 'kim_' + mission_date_field)
    client.get('/api/daily', headers=h)
    extra = {} if mission_date_field == 'absent' else {'mission_date': None}
    with caplog.at_level(logging.INFO):
        r = client.put('/api/daily/effort', headers=h, json={'level': 'easy', **extra})
        assert r.status_code == 200 and r.get_json()['applies'] == 'today'
        r = client.patch('/api/me', headers=h, json={'skill_level': 'advanced', **extra})
        assert r.status_code == 200 and r.get_json()['skill_applies'] == 'today'
    assert _mission(uid, D)[:2] == ('easy', 'advanced')
    logged = [rec.getMessage() for rec in caplog.records if 'undated_day_write' in rec.getMessage()]
    assert any('route=effort' in m for m in logged) and any('route=skill' in m for m in logged)
    assert not any('advanced' in m or 'easy' in m for m in logged)   # no request values logged


def test_an_old_client_after_midnight_keeps_its_old_behaviour(client, clock):
    """Documented compatibility, not an endorsement: a pre-change page cannot
    name its day, so it is applied to today exactly as production does now."""
    uid, h, _stale = _yesterday_page_after_midnight(client, clock, 'lee')
    r = client.put('/api/daily/effort', headers=h, json={'level': 'easy'})
    assert r.status_code == 200 and r.get_json()['mission_date'] == (D + ONE).isoformat()


def test_profile_changes_without_skill_need_no_day(client, clock):
    _uid, h = _user(client, 'max')
    r = client.patch('/api/me', headers=h, json={'display_mode': 'classic'})
    assert r.status_code == 200 and r.get_json()['display_mode'] == 'classic'


def test_the_client_names_its_day_and_handles_day_changed():
    """The page side of the contract: both writes send the day the page shows,
    and both treat 409 day_changed by moving to the new day."""
    js = (REPO / 'static' / 'app.js').read_text()
    effort = js[js.index('async function setEffort'):js.index('function renderDailyExercise')]
    skill = js[js.index('async function handleSkillLevelChange'):js.index('async function loadChallenges')]
    for src in (effort, skill):
        assert 'mission_date: currentMissionDate' in src
        assert "error === 'day_changed'" in src and '_showCurrentMission(' in src
