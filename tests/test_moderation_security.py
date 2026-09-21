"""Adversarial regression tests for the moderation security audit.

Each of the first three classes reproduces a vulnerability that WAS present in
commit 2b862a9 and asserts it is closed. They are written as the attack, not
as the fix: the test says what the attacker tried and what they got, so a
future refactor that reopens the hole fails here with a readable name.

The three shared one root cause -- `POST /api/reports` believed the client's
`team_id` and `reported_user_id`, and resolved content by `public_id` across
the whole database with no authorization. Content is now resolved first and
the team and author are derived from the row.
"""
import io

import pytest

from conftest import register_and_login, auth_headers

from app import db, Report, ReportEvidence, ContentRestriction, \
    UserRestriction, ModerationAction, TeamMessage, CHALLENGE_PRESETS_BY_KEY, DailyCompletion

ADMIN = {'X-Admin-Secret': 's3cret-value'}


@pytest.fixture()
def admin_env(monkeypatch):
    monkeypatch.setenv('ADMIN_SECRET', 's3cret-value')


def mkteam(client, token, name):
    return client.post('/api/teams', json={'name': name},
                       headers=auth_headers(token)).get_json()['team']


def join(client, token, team, expect=200):
    r = client.post(f'/api/teams/{team["id"]}/join',
                    json={'code': team['invite_code']}, headers=auth_headers(token))
    assert r.status_code == expect, r.get_data(as_text=True)
    return r


def say(client, token, team_id, body):
    return client.post(f'/api/teams/{team_id}/messages', json={'body': body},
                       headers=auth_headers(token))


def thread(client, token, team_id):
    return client.get(f'/api/teams/{team_id}/messages',
                      headers=auth_headers(token)).get_json()


def uid(client, token):
    return client.get('/api/me', headers=auth_headers(token)).get_json()['id']


def find(client, token, team_id, body):
    return [m for m in thread(client, token, team_id) if m['body'] == body][0]


def a_preset():
    return next(iter(CHALLENGE_PRESETS_BY_KEY))


def upload(client, token, team_id, caption=None):
    from test_team_photos import VALID_JPEG
    form = {'photo': (io.BytesIO(VALID_JPEG), 'p.jpg')}
    if caption is not None:
        form['caption'] = caption
    return client.post(f'/api/teams/{team_id}/photos', data=form,
                       content_type='multipart/form-data', headers=auth_headers(token))


@pytest.fixture()
def two_teams(client):
    """Mallory is in both teams. The victim is only in team B."""
    mallory = register_and_login(client, 'mallorykay')
    owner = register_and_login(client, 'ownerpat')
    victim = register_and_login(client, 'victimlee')
    team_a = mkteam(client, mallory, 'Team A')
    team_b = mkteam(client, owner, 'Team B')
    join(client, mallory, team_b)
    join(client, victim, team_b)
    return mallory, owner, victim, team_a, team_b


# --- H2: cross-team content reference ----------------------------------------

def test_cannot_report_another_teams_content_by_claiming_your_own_team(
        client, two_teams, admin_env):
    """Reproduces H2. Mallory is in both teams and names the wrong one."""
    mallory, owner, victim, team_a, team_b = two_teams
    say(client, victim, team_b['id'], 'VICTIM SECRET TEXT')
    msg = find(client, mallory, team_b['id'], 'VICTIM SECRET TEXT')

    r = client.post('/api/reports', json={
        'category': 'harassment', 'subject_type': 'message',
        'subject_ref': msg['message_id'],
        'team_id': team_a['id']},              # <-- content lives in team B
        headers=auth_headers(mallory))

    assert r.status_code == 404
    assert db.session.query(Report).count() == 0
    assert db.session.query(ReportEvidence).count() == 0
    assert 'VICTIM SECRET TEXT' not in r.get_data(as_text=True)


def test_an_outsider_cannot_reach_a_teams_content_at_all(client, two_teams):
    mallory, owner, victim, team_a, team_b = two_teams
    outsider = register_and_login(client, 'outsidermo')
    team_c = mkteam(client, outsider, 'Team C')
    say(client, victim, team_b['id'], 'not for outsiders')
    msg = find(client, mallory, team_b['id'], 'not for outsiders')

    r = client.post('/api/reports', json={
        'category': 'harassment', 'subject_type': 'message',
        'subject_ref': msg['message_id'], 'team_id': team_c['id']},
        headers=auth_headers(outsider))
    assert r.status_code == 404
    assert db.session.query(ReportEvidence).count() == 0


def test_a_correct_team_id_is_still_accepted(client, two_teams, admin_env):
    """The fix must not break the ordinary case."""
    mallory, owner, victim, team_a, team_b = two_teams
    say(client, victim, team_b['id'], 'ordinary complaint')
    msg = find(client, mallory, team_b['id'], 'ordinary complaint')
    r = client.post('/api/reports', json={
        'category': 'harassment', 'subject_type': 'message',
        'subject_ref': msg['message_id'], 'team_id': team_b['id']},
        headers=auth_headers(mallory))
    assert r.status_code == 201
    rep = db.session.query(Report).one()
    assert rep.team_id == team_b['id']


def test_team_id_may_be_omitted_entirely_and_is_derived(client, two_teams):
    mallory, owner, victim, team_a, team_b = two_teams
    say(client, victim, team_b['id'], 'derive my team')
    msg = find(client, mallory, team_b['id'], 'derive my team')
    r = client.post('/api/reports', json={
        'category': 'spam', 'subject_type': 'message',
        'subject_ref': msg['message_id']},
        headers=auth_headers(mallory))
    assert r.status_code == 201
    assert db.session.query(Report).one().team_id == team_b['id']


# --- H1: forged reported_user_id ---------------------------------------------

def test_a_forged_reported_user_id_is_ignored_not_trusted(client, two_teams, admin_env):
    """Reproduces H1. The report blames a bystander for the victim's message."""
    mallory, owner, victim, team_a, team_b = two_teams
    bystander = register_and_login(client, 'bystandav')
    join(client, bystander, team_b)
    say(client, victim, team_b['id'], 'authored by the victim')
    msg = find(client, mallory, team_b['id'], 'authored by the victim')

    r = client.post('/api/reports', json={
        'category': 'harassment', 'subject_type': 'message',
        'subject_ref': msg['message_id'], 'team_id': team_b['id'],
        'reported_user_id': uid(client, bystander)},     # <-- the forgery
        headers=auth_headers(mallory))
    assert r.status_code == 201

    rep = db.session.query(Report).one()
    assert rep.reported_user_id == uid(client, victim), \
        'reported_user_id must come from the content, not the request body'
    assert rep.reported_user_id != uid(client, bystander)


def test_a_forged_report_cannot_get_an_unrelated_account_suspended(
        client, two_teams, admin_env):
    """The end of the H1 chain: the operator acts, and the wrong person used
    to be the one suspended."""
    mallory, owner, victim, team_a, team_b = two_teams
    bystander = register_and_login(client, 'bystandav')
    join(client, bystander, team_b)
    say(client, victim, team_b['id'], 'authored by the victim')
    msg = find(client, mallory, team_b['id'], 'authored by the victim')
    client.post('/api/reports', json={
        'category': 'harassment', 'subject_type': 'message',
        'subject_ref': msg['message_id'], 'team_id': team_b['id'],
        'reported_user_id': uid(client, bystander)},
        headers=auth_headers(mallory))
    rep = db.session.query(Report).one()

    client.post(f'/api/admin/reports/{rep.public_id}/action',
                json={'action': 'suspend_social'}, headers=ADMIN)

    assert say(client, bystander, team_b['id'], 'am I ok?').status_code == 201, \
        'the bystander must be untouched'
    assert say(client, victim, team_b['id'], 'and me?').status_code == 403


# --- H3: pre-join history --------------------------------------------------

def test_content_from_before_you_joined_cannot_be_reported(client, two_teams, admin_env):
    """Reproduces H3. The latecomer cannot see it and must not be able to
    launder it into an evidence record either."""
    mallory, owner, victim, team_a, team_b = two_teams
    say(client, victim, team_b['id'], 'BEFORE THE LATECOMER')
    msg = find(client, mallory, team_b['id'], 'BEFORE THE LATECOMER')

    late = register_and_login(client, 'latecomerj')
    join(client, late, team_b)
    assert 'BEFORE THE LATECOMER' not in [m['body'] for m in thread(client, late, team_b['id'])]

    r = client.post('/api/reports', json={
        'category': 'harassment', 'subject_type': 'message',
        'subject_ref': msg['message_id'], 'team_id': team_b['id']},
        headers=auth_headers(late))
    assert r.status_code == 404
    assert db.session.query(ReportEvidence).count() == 0


# --- Unauthorized automatic takedown -----------------------------------------

def test_a_child_safety_report_cannot_take_down_another_teams_content(
        client, two_teams):
    """The most damaging half of H2: the auto-restrict path."""
    mallory, owner, victim, team_a, team_b = two_teams
    say(client, victim, team_b['id'], 'SECOND VICTIM MESSAGE')
    msg = find(client, mallory, team_b['id'], 'SECOND VICTIM MESSAGE')

    r = client.post('/api/reports', json={
        'category': 'child_safety', 'subject_type': 'message',
        'subject_ref': msg['message_id'], 'team_id': team_a['id']},
        headers=auth_headers(mallory))
    assert r.status_code == 404
    assert db.session.query(ContentRestriction).count() == 0
    assert 'SECOND VICTIM MESSAGE' in [m['body'] for m in thread(client, owner, team_b['id'])]


def test_an_authorized_child_safety_report_still_takes_content_down(client, two_teams):
    """The protection must not have been removed along with the hole."""
    mallory, owner, victim, team_a, team_b = two_teams
    say(client, victim, team_b['id'], 'genuinely flagged')
    msg = find(client, mallory, team_b['id'], 'genuinely flagged')
    r = client.post('/api/reports', json={
        'category': 'child_safety', 'subject_type': 'message',
        'subject_ref': msg['message_id'], 'team_id': team_b['id']},
        headers=auth_headers(mallory))
    assert r.status_code == 201
    assert 'genuinely flagged' not in [m['body'] for m in thread(client, owner, team_b['id'])]


# --- L1: nonexistent / inaccessible references -------------------------------

def test_a_nonexistent_reference_creates_nothing(client, two_teams):
    mallory, owner, victim, team_a, team_b = two_teams
    r = client.post('/api/reports', json={
        'category': 'spam', 'subject_type': 'message',
        'subject_ref': 'f' * 32, 'team_id': team_b['id']},
        headers=auth_headers(mallory))
    assert r.status_code == 404
    assert db.session.query(Report).count() == 0
    assert db.session.query(ReportEvidence).count() == 0


def test_a_deleted_photo_cannot_be_reported(client, two_teams):
    mallory, owner, victim, team_a, team_b = two_teams
    up = upload(client, victim, team_b['id'], caption='will be deleted')
    assert up.status_code == 201
    pid = up.get_json()['photo']['public_id']
    client.delete(f'/api/teams/{team_b["id"]}/photos/{pid}', headers=auth_headers(victim))

    r = client.post('/api/reports', json={
        'category': 'inappropriate_content', 'subject_type': 'photo',
        'subject_ref': pid, 'team_id': team_b['id']},
        headers=auth_headers(mallory))
    assert r.status_code == 404
    assert db.session.query(ReportEvidence).count() == 0


def test_already_restricted_content_can_still_be_reported_by_someone_else(client, two_teams):
    """Reversed deliberately in the operations milestone.

    The security pass refused this, reasoning that confirming a takedown
    exists is a disclosure. It is not -- the reporter gets a 201 either way --
    and refusing broke the case that matters: two people alarmed by the same
    message, where the second is turned away because the first got there
    first. Each report now owns its own restriction row, so both holds must be
    lifted before the content returns.
    """
    mallory, owner, victim, team_a, team_b = two_teams
    say(client, victim, team_b['id'], 'already withheld')
    msg = find(client, mallory, team_b['id'], 'already withheld')
    db.session.add(ContentRestriction(subject_type='message',
                                      subject_ref=msg['message_id'], reason='moderator'))
    db.session.commit()
    r = client.post('/api/reports', json={
        'category': 'spam', 'subject_type': 'message',
        'subject_ref': msg['message_id'], 'team_id': team_b['id']},
        headers=auth_headers(mallory))
    assert r.status_code == 201
    # The authorization that matters is unchanged: an outsider still cannot.
    outsider = register_and_login(client, 'outsidermo')
    team_c = mkteam(client, outsider, 'Team C')
    assert client.post('/api/reports', json={
        'category': 'spam', 'subject_type': 'message',
        'subject_ref': msg['message_id'], 'team_id': team_c['id']},
        headers=auth_headers(outsider)).status_code == 404


def test_a_blocked_persons_content_cannot_be_reported(client, two_teams):
    mallory, owner, victim, team_a, team_b = two_teams
    say(client, victim, team_b['id'], 'from someone blocked')
    msg = find(client, mallory, team_b['id'], 'from someone blocked')
    client.put(f'/api/blocks/{uid(client, victim)}', headers=auth_headers(mallory))
    r = client.post('/api/reports', json={
        'category': 'spam', 'subject_type': 'message',
        'subject_ref': msg['message_id'], 'team_id': team_b['id']},
        headers=auth_headers(mallory))
    assert r.status_code == 404


def test_every_refusal_gives_the_same_answer(client, two_teams):
    """Missing, someone else's, pre-join and deleted must be indistinguishable."""
    mallory, owner, victim, team_a, team_b = two_teams
    say(client, victim, team_b['id'], 'real message')
    real = find(client, mallory, team_b['id'], 'real message')
    outsider = register_and_login(client, 'outsidermo')
    team_c = mkteam(client, outsider, 'Team C')

    answers = []
    for ref in ('f' * 32, real['message_id']):
        r = client.post('/api/reports', json={
            'category': 'spam', 'subject_type': 'message',
            'subject_ref': ref, 'team_id': team_c['id']},
            headers=auth_headers(outsider))
        answers.append((r.status_code, r.get_json()))
    assert answers[0] == answers[1] == (404, {'error': 'not_found'})


# --- M1: challenge restriction ------------------------------------------------

def test_a_restricted_challenge_disappears_from_the_thread(client, two_teams, admin_env):
    mallory, owner, victim, team_a, team_b = two_teams
    ch = client.post(f'/api/teams/{team_b["id"]}/challenges',
                     json={'preset_key': a_preset()}, headers=auth_headers(owner))
    assert ch.status_code == 201
    cpid = (ch.get_json().get('challenge') or ch.get_json())['public_id']

    assert any((m.get('challenge') or {}).get('public_id') == cpid
               for m in thread(client, mallory, team_b['id']))

    r = client.post('/api/reports', json={
        'category': 'inappropriate_content', 'subject_type': 'challenge',
        'subject_ref': cpid, 'team_id': team_b['id']}, headers=auth_headers(mallory))
    assert r.status_code == 201
    rep = db.session.query(Report).one()
    client.post(f'/api/admin/reports/{rep.public_id}/action',
                json={'action': 'restrict_content'}, headers=ADMIN)

    assert not any((m.get('challenge') or {}).get('public_id') == cpid
                   for m in thread(client, mallory, team_b['id']))
    assert not any((m.get('challenge') or {}).get('public_id') == cpid
                   for m in thread(client, owner, team_b['id']))


def test_a_restricted_challenge_cannot_be_completed_by_direct_request(
        client, two_teams, admin_env):
    mallory, owner, victim, team_a, team_b = two_teams
    ch = client.post(f'/api/teams/{team_b["id"]}/challenges',
                     json={'preset_key': a_preset()}, headers=auth_headers(owner))
    cpid = (ch.get_json().get('challenge') or ch.get_json())['public_id']
    db.session.add(ContentRestriction(subject_type='challenge', subject_ref=cpid,
                                      reason='moderator'))
    db.session.commit()
    r = client.post(f'/api/teams/{team_b["id"]}/challenges/{cpid}/complete',
                    headers=auth_headers(mallory))
    assert r.status_code == 404


def test_challenge_evidence_carries_real_text(client, two_teams, admin_env):
    """It used to read a `title` attribute TeamChallenge does not have, so
    every challenge report reached a reviewer empty."""
    mallory, owner, victim, team_a, team_b = two_teams
    ch = client.post(f'/api/teams/{team_b["id"]}/challenges',
                     json={'preset_key': a_preset()}, headers=auth_headers(owner))
    cpid = (ch.get_json().get('challenge') or ch.get_json())['public_id']
    client.post('/api/reports', json={
        'category': 'spam', 'subject_type': 'challenge',
        'subject_ref': cpid, 'team_id': team_b['id']}, headers=auth_headers(mallory))
    rep = db.session.query(Report).one()
    detail = client.get(f'/api/admin/reports/{rep.public_id}', headers=ADMIN).get_json()
    assert detail['evidence'][0]['content_text'], 'a reviewer must see what was reported'
    assert detail['evidence'][0]['context']['preset_key']


# --- M2: photo restriction is complete ---------------------------------------

def test_a_restricted_photo_loses_its_card_caption_metadata_and_bytes(
        client, two_teams, admin_env):
    mallory, owner, victim, team_a, team_b = two_teams
    up = upload(client, victim, team_b['id'], caption='SENSITIVE CAPTION TEXT')
    assert up.status_code == 201
    pid = up.get_json()['photo']['public_id']

    client.post('/api/reports', json={
        'category': 'inappropriate_content', 'subject_type': 'photo',
        'subject_ref': pid, 'team_id': team_b['id']}, headers=auth_headers(mallory))
    rep = db.session.query(Report).one()
    client.post(f'/api/admin/reports/{rep.public_id}/action',
                json={'action': 'restrict_content'}, headers=ADMIN)

    raw = client.get(f'/api/teams/{team_b["id"]}/messages',
                     headers=auth_headers(mallory)).get_data(as_text=True)
    msgs = thread(client, mallory, team_b['id'])
    assert not any((m.get('photo') or {}).get('public_id') == pid for m in msgs), 'card'
    assert 'SENSITIVE CAPTION TEXT' not in raw, 'caption'
    assert pid not in raw, 'metadata'
    assert client.get(f'/api/teams/{team_b["id"]}/photos/{pid}',
                      headers=auth_headers(mallory)).status_code == 404, 'bytes'


# --- M3: social suspension coverage -------------------------------------------

@pytest.fixture()
def suspended(client, two_teams, admin_env):
    mallory, owner, victim, team_a, team_b = two_teams
    client.post('/api/reports', json={
        'category': 'threats', 'subject_type': 'user',
        'reported_user_id': uid(client, victim), 'team_id': team_b['id']},
        headers=auth_headers(owner))
    rep = db.session.query(Report).one()
    client.post(f'/api/admin/reports/{rep.public_id}/action',
                json={'action': 'suspend_social'}, headers=ADMIN)
    return mallory, owner, victim, team_a, team_b


def test_suspension_blocks_creating_a_team(client, suspended):
    _, _, victim, _, _ = suspended
    r = client.post('/api/teams', json={'name': 'Fresh Start'},
                    headers=auth_headers(victim))
    assert r.status_code == 403 and r.get_json()['code'] == 'social_suspended'


def test_suspension_blocks_joining_a_team(client, suspended):
    mallory, owner, victim, team_a, team_b = suspended
    elsewhere = mkteam(client, owner, 'Elsewhere')
    r = client.post(f'/api/teams/{elsewhere["id"]}/join',
                    json={'code': elsewhere['invite_code']}, headers=auth_headers(victim))
    assert r.status_code == 403 and r.get_json()['code'] == 'social_suspended'


def test_suspension_blocks_completing_a_challenge(client, suspended):
    mallory, owner, victim, team_a, team_b = suspended
    ch = client.post(f'/api/teams/{team_b["id"]}/challenges',
                     json={'preset_key': a_preset()}, headers=auth_headers(owner))
    cpid = (ch.get_json().get('challenge') or ch.get_json())['public_id']
    r = client.post(f'/api/teams/{team_b["id"]}/challenges/{cpid}/complete',
                    headers=auth_headers(victim))
    assert r.status_code == 403 and r.get_json()['code'] == 'social_suspended'


def test_suspension_blocks_rotating_an_invite_code(client, two_teams, admin_env):
    """The suspended person here is the team's CREATOR -- the earlier audit
    recorded this as blocked when it was only the creator check firing."""
    mallory, owner, victim, team_a, team_b = two_teams
    client.post('/api/reports', json={
        'category': 'threats', 'subject_type': 'user',
        'reported_user_id': uid(client, owner), 'team_id': team_b['id']},
        headers=auth_headers(victim))
    rep = db.session.query(Report).one()
    client.post(f'/api/admin/reports/{rep.public_id}/action',
                json={'action': 'suspend_social'}, headers=ADMIN)
    r = client.post(f'/api/teams/{team_b["id"]}/rotate-invite', headers=auth_headers(owner))
    assert r.status_code == 403 and r.get_json()['code'] == 'social_suspended'


def test_suspension_blocks_deleting_a_photo(client, two_teams, admin_env):
    mallory, owner, victim, team_a, team_b = two_teams
    up = upload(client, victim, team_b['id'], caption='mine')
    pid = up.get_json()['photo']['public_id']
    client.post('/api/reports', json={
        'category': 'threats', 'subject_type': 'user',
        'reported_user_id': uid(client, victim), 'team_id': team_b['id']},
        headers=auth_headers(owner))
    rep = db.session.query(Report).one()
    client.post(f'/api/admin/reports/{rep.public_id}/action',
                json={'action': 'suspend_social'}, headers=ADMIN)
    r = client.delete(f'/api/teams/{team_b["id"]}/photos/{pid}', headers=auth_headers(victim))
    assert r.status_code == 403


def test_a_suspended_person_can_still_protect_themselves(client, suspended):
    """Owner decision: suspension must never remove somebody's ability to
    block or to report. Being moderated does not make you unsafe."""
    mallory, owner, victim, team_a, team_b = suspended
    assert client.put(f'/api/blocks/{uid(client, mallory)}',
                      headers=auth_headers(victim)).status_code == 204
    assert client.get('/api/blocks', headers=auth_headers(victim)).status_code == 200
    r = client.post('/api/reports', json={
        'category': 'harassment', 'subject_type': 'user',
        'reported_user_id': uid(client, mallory), 'team_id': team_b['id']},
        headers=auth_headers(victim))
    assert r.status_code == 201


def test_suspension_leaves_the_solo_product_untouched(client, suspended):
    mallory, owner, victim, team_a, team_b = suspended
    daily = client.get('/api/daily', headers=auth_headers(victim)).get_json()
    for ex in daily['exercises']:
        assert client.post(f"/api/daily/{ex['key']}/complete",
                           headers=auth_headers(victim)).status_code in (200, 201)
    me = client.get('/api/me', headers=auth_headers(victim)).get_json()
    assert me['total_missions'] >= 1
    assert db.session.query(DailyCompletion).count() >= 5


# --- L2: operator actions cannot wander --------------------------------------

def test_an_operator_action_cannot_name_an_unrelated_user(client, two_teams, admin_env):
    mallory, owner, victim, team_a, team_b = two_teams
    client.post('/api/reports', json={
        'category': 'threats', 'subject_type': 'user',
        'reported_user_id': uid(client, victim), 'team_id': team_b['id']},
        headers=auth_headers(owner))
    rep = db.session.query(Report).one()

    r = client.post(f'/api/admin/reports/{rep.public_id}/action',
                    json={'action': 'suspend_social',
                          'target_user_id': uid(client, mallory)}, headers=ADMIN)
    assert r.status_code == 400
    assert r.get_json()['code'] == 'target_mismatch'
    assert db.session.query(UserRestriction).count() == 0
    assert say(client, mallory, team_b['id'], 'untouched').status_code == 201


def test_an_operator_action_cannot_name_an_unrelated_team(client, two_teams, admin_env):
    mallory, owner, victim, team_a, team_b = two_teams
    client.post('/api/reports', json={
        'category': 'harassment', 'subject_type': 'user',
        'reported_user_id': uid(client, victim), 'team_id': team_b['id']},
        headers=auth_headers(owner))
    rep = db.session.query(Report).one()
    r = client.post(f'/api/admin/reports/{rep.public_id}/action',
                    json={'action': 'remove_from_team', 'team_id': team_a['id']},
                    headers=ADMIN)
    assert r.status_code == 400
    assert r.get_json()['code'] == 'target_mismatch'


def test_matching_values_are_still_accepted(client, two_teams, admin_env):
    mallory, owner, victim, team_a, team_b = two_teams
    client.post('/api/reports', json={
        'category': 'harassment', 'subject_type': 'user',
        'reported_user_id': uid(client, victim), 'team_id': team_b['id']},
        headers=auth_headers(owner))
    rep = db.session.query(Report).one()
    r = client.post(f'/api/admin/reports/{rep.public_id}/action',
                    json={'action': 'suspend_social',
                          'target_user_id': uid(client, victim),
                          'team_id': team_b['id']}, headers=ADMIN)
    assert r.status_code == 200


def test_the_audit_row_names_the_real_target(client, two_teams, admin_env):
    mallory, owner, victim, team_a, team_b = two_teams
    client.post('/api/reports', json={
        'category': 'harassment', 'subject_type': 'user',
        'reported_user_id': uid(client, victim), 'team_id': team_b['id']},
        headers=auth_headers(owner))
    rep = db.session.query(Report).one()
    client.post(f'/api/admin/reports/{rep.public_id}/action',
                json={'action': 'suspend_social'}, headers=ADMIN)
    act = db.session.query(ModerationAction).filter_by(action='suspend_social').one()
    assert act.target_user_id == uid(client, victim)
    assert act.team_id == team_b['id']


# --- privacy regressions that must survive all of the above -------------------

def test_reporter_identity_is_still_withheld(client, two_teams, admin_env):
    mallory, owner, victim, team_a, team_b = two_teams
    say(client, victim, team_b['id'], 'reported thing')
    msg = find(client, mallory, team_b['id'], 'reported thing')
    client.post('/api/reports', json={
        'category': 'harassment', 'subject_type': 'message',
        'subject_ref': msg['message_id'], 'team_id': team_b['id']},
        headers=auth_headers(mallory))
    q = client.get('/api/admin/reports', headers=ADMIN).get_json()
    assert 'reporter_user_id' not in q['reports'][0]
    for path in (f'/api/teams/{team_b["id"]}', f'/api/teams/{team_b["id"]}/messages'):
        raw = client.get(path, headers=auth_headers(victim)).get_data(as_text=True)
        assert 'mallorykay' not in raw


def test_evidence_still_survives_deletion(client, two_teams, admin_env):
    mallory, owner, victim, team_a, team_b = two_teams
    say(client, victim, team_b['id'], 'will vanish')
    msg = find(client, mallory, team_b['id'], 'will vanish')
    client.post('/api/reports', json={
        'category': 'harassment', 'subject_type': 'message',
        'subject_ref': msg['message_id'], 'team_id': team_b['id']},
        headers=auth_headers(mallory))
    row = db.session.query(TeamMessage).filter_by(public_id=msg['message_id']).one()
    db.session.delete(row)
    db.session.commit()
    rep = db.session.query(Report).one()
    detail = client.get(f'/api/admin/reports/{rep.public_id}', headers=ADMIN).get_json()
    assert detail['evidence'][0]['content_text'] == 'will vanish'


@pytest.mark.parametrize('path_tmpl', [
    '/api/teams/{tid}', '/api/teams/{tid}/moments', '/api/teams/{tid}/messages'])
def test_the_four_username_paths_still_carry_no_login(client, two_teams, path_tmpl):
    mallory, owner, victim, team_a, team_b = two_teams
    say(client, victim, team_b['id'], 'hello')
    raw = client.get(path_tmpl.format(tid=team_b['id']),
                     headers=auth_headers(mallory)).get_data(as_text=True)
    for login in ('mallorykay', 'ownerpat', 'victimlee'):
        assert login not in raw


def test_the_chat_post_echo_still_carries_no_peer_login(client, two_teams):
    mallory, owner, victim, team_a, team_b = two_teams
    r = say(client, victim, team_b['id'], 'echo')
    assert r.status_code == 201
    assert 'mallorykay' not in r.get_data(as_text=True)
