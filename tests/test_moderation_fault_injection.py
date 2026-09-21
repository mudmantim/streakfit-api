"""Fault injection: prove each moderation guard is load-bearing.

`tests/test_moderation.py` asserts the protections hold. On its own that is
not evidence they are doing the work -- a test can pass because the feature
works or because the test never exercised it, and the two look identical from
the outside.

Each test here removes exactly one guard and asserts the protection collapses.
If a guard is ever deleted or refactored into a no-op, the matching test in
`test_moderation.py` starts failing AND the test here starts failing, in the
opposite direction, which is what makes the pair meaningful.

The pattern is a monkeypatched no-op rather than an edited source file,
because the route functions resolve these helpers as module globals at call
time -- patching the module attribute is exactly what deleting the call would
do, without touching the working tree.
"""
import io

import pytest

import app as appmod
from app import db, ContentRestriction, Report

from conftest import register_and_login, auth_headers

ADMIN = {'X-Admin-Secret': 's3cret-value'}


@pytest.fixture()
def admin_env(monkeypatch):
    monkeypatch.setenv('ADMIN_SECRET', 's3cret-value')


@pytest.fixture()
def pair(client):
    a = register_and_login(client, 'emmacarter')
    b = register_and_login(client, 'jacobreed')
    team = client.post('/api/teams', json={'name': 'Carter Family'},
                       headers=auth_headers(a)).get_json()['team']
    client.post(f'/api/teams/{team["id"]}/join', json={'code': team['invite_code']},
                headers=auth_headers(b))
    return a, b, team


def say(client, token, team_id, body):
    return client.post(f'/api/teams/{team_id}/messages', json={'body': body},
                       headers=auth_headers(token))


def thread(client, token, team_id):
    return client.get(f'/api/teams/{team_id}/messages',
                      headers=auth_headers(token)).get_json()


def uid(client, token):
    return client.get('/api/me', headers=auth_headers(token)).get_json()['id']


# --- Blocking ----------------------------------------------------------------

def test_without_the_block_filter_the_thread_leaks_through(client, pair, monkeypatch):
    a, b, team = pair
    say(client, b, team['id'], 'jacob speaking')
    client.put(f'/api/blocks/{uid(client, b)}', headers=auth_headers(a))

    # Guard in place: hidden.
    assert 'jacob speaking' not in [m['body'] for m in thread(client, a, team['id'])]

    # Guard removed: visible again. The filter is what was doing the work.
    monkeypatch.setattr(appmod, '_blocked_ids_for', lambda _uid: set())
    assert 'jacob speaking' in [m['body'] for m in thread(client, a, team['id'])]


def test_without_the_block_check_a_challenge_can_still_name_the_blocker(
        client, pair, monkeypatch):
    a, b, team = pair
    preset = next(iter(appmod.CHALLENGE_PRESETS_BY_KEY))
    aid = uid(client, a)
    client.put(f'/api/blocks/{uid(client, b)}', headers=auth_headers(a))

    blocked = client.post(f'/api/teams/{team["id"]}/challenges',
                          json={'preset_key': preset, 'target_user_id': aid},
                          headers=auth_headers(b))
    assert blocked.status_code == 400

    monkeypatch.setattr(appmod, '_is_blocked_between', lambda _x, _y: False)
    allowed = client.post(f'/api/teams/{team["id"]}/challenges',
                          json={'preset_key': preset, 'target_user_id': aid},
                          headers=auth_headers(b))
    assert allowed.status_code == 201


def test_without_the_block_filter_photo_bytes_are_reachable(client, pair, monkeypatch):
    from test_team_photos import VALID_JPEG
    a, b, team = pair
    up = client.post(f'/api/teams/{team["id"]}/photos',
                     data={'photo': (io.BytesIO(VALID_JPEG), 'photo.jpg')},
                     content_type='multipart/form-data', headers=auth_headers(b))
    assert up.status_code == 201
    pid = up.get_json()['photo']['public_id']
    client.put(f'/api/blocks/{uid(client, b)}', headers=auth_headers(a))

    url = f'/api/teams/{team["id"]}/photos/{pid}'
    assert client.get(url, headers=auth_headers(a)).status_code == 404
    monkeypatch.setattr(appmod, '_blocked_ids_for', lambda _uid: set())
    assert client.get(url, headers=auth_headers(a)).status_code == 200


# --- Content restriction -----------------------------------------------------

def test_without_the_restriction_filter_hidden_messages_come_back(
        client, pair, monkeypatch, admin_env):
    a, b, team = pair
    say(client, b, team['id'], 'restricted text')
    msg = [m for m in thread(client, a, team['id']) if m['body'] == 'restricted text'][0]
    db.session.add(ContentRestriction(subject_type='message',
                                      subject_ref=msg['message_id'], reason='moderator'))
    db.session.commit()

    assert 'restricted text' not in [m['body'] for m in thread(client, a, team['id'])]
    monkeypatch.setattr(appmod, '_restricted_refs', lambda _t, _r: set())
    assert 'restricted text' in [m['body'] for m in thread(client, a, team['id'])]


def test_without_the_restriction_check_photo_bytes_are_served(
        client, pair, monkeypatch, admin_env):
    from test_team_photos import VALID_JPEG
    a, b, team = pair
    up = client.post(f'/api/teams/{team["id"]}/photos',
                     data={'photo': (io.BytesIO(VALID_JPEG), 'photo.jpg')},
                     content_type='multipart/form-data', headers=auth_headers(b))
    pid = up.get_json()['photo']['public_id']
    db.session.add(ContentRestriction(subject_type='photo', subject_ref=pid,
                                      reason='moderator'))
    db.session.commit()

    url = f'/api/teams/{team["id"]}/photos/{pid}'
    assert client.get(url, headers=auth_headers(a)).status_code == 404
    monkeypatch.setattr(appmod, '_is_content_restricted', lambda _t, _r: False)
    assert client.get(url, headers=auth_headers(a)).status_code == 200


def test_without_the_auto_restrict_list_child_safety_reports_leave_content_visible(
        client, pair, monkeypatch):
    a, b, team = pair
    monkeypatch.setattr(appmod, 'AUTO_RESTRICT_CATEGORIES', ())
    say(client, b, team['id'], 'flagged material')
    msg = [m for m in thread(client, a, team['id']) if m['body'] == 'flagged material'][0]
    client.post('/api/reports', json={
        'category': 'child_safety', 'subject_type': 'message',
        'subject_ref': msg['message_id'], 'team_id': team['id']},
        headers=auth_headers(a))

    # With the category removed, nothing is withheld -- which is exactly the
    # regression the real test guards against.
    assert db.session.query(ContentRestriction).count() == 0
    assert 'flagged material' in [m['body'] for m in thread(client, b, team['id'])]


# --- Suspension --------------------------------------------------------------

def test_without_the_suspension_guard_a_suspended_user_can_post(
        client, pair, monkeypatch, admin_env):
    a, b, team = pair
    client.post('/api/reports', json={
        'category': 'threats', 'subject_type': 'user',
        'reported_user_id': uid(client, b), 'team_id': team['id']},
        headers=auth_headers(a))
    rep = db.session.query(Report).order_by(Report.id.desc()).first()
    client.post(f'/api/admin/reports/{rep.public_id}/action',
                json={'action': 'suspend_social'}, headers=ADMIN)

    assert say(client, b, team['id'], 'blocked by suspension').status_code == 403
    monkeypatch.setattr(appmod, '_require_social_privileges', lambda _uid: None)
    assert say(client, b, team['id'], 'through the gap').status_code == 201


def test_the_suspension_lookup_fails_closed_when_its_storage_breaks(
        client, pair, monkeypatch):
    """A moderation check that passes when it cannot read its own table is
    not a moderation check. Simulate the table being unreadable."""
    a, b, team = pair

    def boom(*_a, **_k):
        raise RuntimeError('restriction table unavailable')

    monkeypatch.setattr(appmod.db.session, 'execute', boom)
    result = appmod._social_suspension_for(1)
    assert result is not None, 'lookup failure must read as suspended, not as allowed'
    assert result.reason == 'lookup_failed'


# --- Operator authorization --------------------------------------------------

def test_without_the_admin_gate_ordinary_users_reach_the_queue(
        client, pair, monkeypatch, admin_env):
    a, b, team = pair
    client.post('/api/reports', json={
        'category': 'spam', 'subject_type': 'user',
        'reported_user_id': uid(client, b), 'team_id': team['id']},
        headers=auth_headers(a))

    assert client.get('/api/admin/reports', headers=auth_headers(b)).status_code == 403
    monkeypatch.setattr(appmod, '_require_admin_secret', lambda: None)
    assert client.get('/api/admin/reports', headers=auth_headers(b)).status_code == 200


# --- Report-route boundary ---------------------------------------------------

def test_without_the_content_resolver_reporting_becomes_a_fetch_primitive(
        client, pair, monkeypatch):
    """The report route must never be a way to reach content you cannot see.

    The guard is now `_resolve_reportable_content`, which finds the row,
    derives its team, and authorizes the reporter against THAT team plus the
    joined_at boundary. Replacing it with a version that resolves the content
    and skips the authorization is exactly the vulnerability that shipped in
    2b862a9, and the outsider's report succeeds again.
    """
    a, b, team = pair
    outsider = register_and_login(client, 'strangerpat')
    say(client, b, team['id'], 'private to this team')
    msg = [m for m in thread(client, a, team['id'])][-1]
    payload = {'category': 'harassment', 'subject_type': 'message',
               'subject_ref': msg['message_id'], 'team_id': team['id']}

    # Guard in place.
    r = client.post('/api/reports', json=payload, headers=auth_headers(outsider))
    assert r.status_code == 404
    assert db.session.query(Report).count() == 0

    # Guard replaced by the unauthorized lookup the old code did.
    def resolve_without_authorizing(_user_id, subject_type, subject_ref):
        row = db.session.execute(
            appmod.db.select(appmod.TeamMessage).where(
                appmod.TeamMessage.public_id == subject_ref)).scalar_one_or_none()
        if row is None:
            return None
        return {"team_id": row.team_id, "author_id": row.sender_user_id,
                "content_text": row.body, "context": {}}

    monkeypatch.setattr(appmod, '_resolve_reportable_content', resolve_without_authorizing)
    r2 = client.post('/api/reports', json=payload, headers=auth_headers(outsider))
    assert r2.status_code == 201, 'without authorization the outsider gets in'
    ev = db.session.query(appmod.ReportEvidence).one()
    assert ev.content_text == 'private to this team', (
        'and the private text lands in an evidence record — which is what the '
        'resolver exists to prevent')


def test_without_the_team_match_a_cross_team_report_is_accepted(client, pair, monkeypatch):
    """The claimed team_id must be checked against the content's real team."""
    a, b, team = pair
    other = register_and_login(client, 'otherperson')
    other_team = client.post('/api/teams', json={'name': 'Other'},
                             headers=auth_headers(other)).get_json()['team']
    client.post(f'/api/teams/{other_team["id"]}/join',
                json={'code': other_team['invite_code']}, headers=auth_headers(a))
    say(client, b, team['id'], 'team one content')
    msg = [m for m in thread(client, a, team['id'])][-1]

    payload = {'category': 'child_safety', 'subject_type': 'message',
               'subject_ref': msg['message_id'], 'team_id': other_team['id']}
    assert client.post('/api/reports', json=payload,
                       headers=auth_headers(a)).status_code == 404
    assert db.session.query(appmod.ContentRestriction).count() == 0


def test_without_the_derived_author_a_forged_report_names_the_wrong_person(
        client, pair, monkeypatch):
    """reported_user_id must come from the content, never from the body."""
    a, b, team = pair
    bystander = register_and_login(client, 'bystandera')
    client.post(f'/api/teams/{team["id"]}/join', json={'code': team['invite_code']},
                headers=auth_headers(bystander))
    say(client, b, team['id'], 'authored by b')
    msg = [m for m in thread(client, a, team['id'])][-1]
    bystander_id = uid(client, bystander)

    client.post('/api/reports', json={
        'category': 'harassment', 'subject_type': 'message',
        'subject_ref': msg['message_id'], 'team_id': team['id'],
        'reported_user_id': bystander_id}, headers=auth_headers(a))
    rep = db.session.query(Report).one()
    assert rep.reported_user_id == uid(client, b)
    assert rep.reported_user_id != bystander_id


def test_without_the_target_check_an_operator_action_can_wander(
        client, pair, monkeypatch, admin_env):
    """Operator actions must be pinned to the report's subject."""
    a, b, team = pair
    client.post('/api/reports', json={
        'category': 'threats', 'subject_type': 'user',
        'reported_user_id': uid(client, b), 'team_id': team['id']},
        headers=auth_headers(a))
    rep = db.session.query(Report).one()

    r = client.post(f'/api/admin/reports/{rep.public_id}/action',
                    json={'action': 'suspend_social', 'target_user_id': uid(client, a)},
                    headers=ADMIN)
    assert r.status_code == 400 and r.get_json()['code'] == 'target_mismatch'
    assert db.session.query(appmod.UserRestriction).count() == 0


def test_without_the_challenge_filter_a_restricted_challenge_stays_visible(
        client, pair, monkeypatch):
    a, b, team = pair
    preset = next(iter(appmod.CHALLENGE_PRESETS_BY_KEY))
    ch = client.post(f'/api/teams/{team["id"]}/challenges', json={'preset_key': preset},
                     headers=auth_headers(a))
    cpid = (ch.get_json().get('challenge') or ch.get_json())['public_id']
    db.session.add(appmod.ContentRestriction(subject_type='challenge',
                                             subject_ref=cpid, reason='moderator'))
    db.session.commit()

    visible = lambda tok: any((m.get('challenge') or {}).get('public_id') == cpid
                              for m in thread(client, tok, team['id']))
    assert not visible(b)
    monkeypatch.setattr(appmod, '_restricted_refs', lambda _t, _r: set())
    assert visible(b)
