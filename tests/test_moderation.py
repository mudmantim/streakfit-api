"""Moderation: blocking, reporting, operator review, and enforcement.

The tests are written against the three surfaces a person can actually be
reached through in StreakFit -- team chat, team photos, and a challenge that
names somebody -- because there are no direct messages and "close the DM
channel" is not available as an answer here.

Usernames throughout are person-shaped (`emmacarter`, not `qa_user_1789…`).
A suite assembled from machine-shaped handles passes against a name helper
that leaks every ordinary name, which is the mistake the peer-identity work
already made once.
"""
import io
import json

import pytest

from conftest import register_and_login, auth_headers

from app import db, UserBlock, Report, ReportEvidence, ModerationAction, \
    ContentRestriction, UserRestriction, TeamMessage, DailyCompletion

ADMIN = {'X-Admin-Secret': 's3cret-value'}


@pytest.fixture()
def admin_env(monkeypatch):
    monkeypatch.setenv('ADMIN_SECRET', 's3cret-value')


def mkteam(client, creator_token, name='Carter Family'):
    return client.post('/api/teams', json={'name': name},
                       headers=auth_headers(creator_token)).get_json()['team']


def join(client, token, team_id, code):
    return client.post(f'/api/teams/{team_id}/join', json={'code': code},
                       headers=auth_headers(token))


def say(client, token, team_id, body):
    return client.post(f'/api/teams/{team_id}/messages', json={'body': body},
                       headers=auth_headers(token))


def thread(client, token, team_id):
    return client.get(f'/api/teams/{team_id}/messages',
                      headers=auth_headers(token)).get_json()


@pytest.fixture()
def pair(client):
    """Two people in one team: emma (creator) and jacob."""
    a = register_and_login(client, 'emmacarter')
    b = register_and_login(client, 'jacobreed')
    team = mkteam(client, a)
    join(client, b, team['id'], team['invite_code'])
    return a, b, team


# --- Blocking ----------------------------------------------------------------

def test_block_and_unblock_round_trip(client, pair):
    a, b, team = pair
    me = client.get('/api/me', headers=auth_headers(b)).get_json()

    assert client.get('/api/blocks', headers=auth_headers(a)).get_json() == []
    assert client.put(f'/api/blocks/{me["id"]}', headers=auth_headers(a)).status_code == 204

    blocks = client.get('/api/blocks', headers=auth_headers(a)).get_json()
    assert [x['user_id'] for x in blocks] == [me['id']]

    assert client.delete(f'/api/blocks/{me["id"]}', headers=auth_headers(a)).status_code == 204
    assert client.get('/api/blocks', headers=auth_headers(a)).get_json() == []


def test_blocking_is_idempotent_and_never_duplicates(client, pair):
    a, b, team = pair
    bid = client.get('/api/me', headers=auth_headers(b)).get_json()['id']
    for _ in range(3):
        assert client.put(f'/api/blocks/{bid}', headers=auth_headers(a)).status_code == 204
    assert db.session.query(UserBlock).count() == 1


def test_you_cannot_block_yourself(client, pair):
    a, _, _ = pair
    me = client.get('/api/me', headers=auth_headers(a)).get_json()
    r = client.put(f'/api/blocks/{me["id"]}', headers=auth_headers(a))
    assert r.status_code == 400
    assert db.session.query(UserBlock).count() == 0


def test_block_route_is_not_an_account_enumeration_oracle(client, pair):
    """A real id and an id that was never issued must be indistinguishable."""
    a, b, _ = pair
    real = client.get('/api/me', headers=auth_headers(b)).get_json()['id']
    missing = 99_999

    r_real = client.put(f'/api/blocks/{real}', headers=auth_headers(a))
    r_missing = client.put(f'/api/blocks/{missing}', headers=auth_headers(a))
    assert r_real.status_code == r_missing.status_code == 204
    assert r_real.get_data() == r_missing.get_data()

    d_real = client.delete(f'/api/blocks/{real}', headers=auth_headers(a))
    d_missing = client.delete(f'/api/blocks/{missing}', headers=auth_headers(a))
    assert d_real.status_code == d_missing.status_code == 204


def test_blocks_list_carries_no_login_identifier(client, pair):
    a, b, _ = pair
    bid = client.get('/api/me', headers=auth_headers(b)).get_json()['id']
    client.put(f'/api/blocks/{bid}', headers=auth_headers(a))
    raw = client.get('/api/blocks', headers=auth_headers(a)).get_data(as_text=True)
    assert 'jacobreed' not in raw
    assert 'emmacarter' not in raw


def test_you_cannot_block_on_someone_elses_behalf(client, pair):
    """The blocker is always the caller; there is no route that takes one."""
    a, b, _ = pair
    aid = client.get('/api/me', headers=auth_headers(a)).get_json()['id']
    bid = client.get('/api/me', headers=auth_headers(b)).get_json()['id']
    client.put(f'/api/blocks/{bid}', headers=auth_headers(a))
    row = db.session.query(UserBlock).one()
    assert row.blocker_user_id == aid and row.blocked_user_id == bid


def test_blocking_requires_authentication(client, pair):
    _, b, _ = pair
    assert client.put('/api/blocks/1').status_code == 401
    assert client.get('/api/blocks').status_code == 401


# --- Blocking inside a shared team -------------------------------------------

def test_a_block_hides_the_thread_both_ways_without_removing_anyone(client, pair):
    a, b, team = pair
    tid = team['id']
    say(client, a, tid, 'emma speaking')
    say(client, b, tid, 'jacob speaking')
    bid = client.get('/api/me', headers=auth_headers(b)).get_json()['id']

    client.put(f'/api/blocks/{bid}', headers=auth_headers(a))

    bodies_a = [m['body'] for m in thread(client, a, tid)]
    bodies_b = [m['body'] for m in thread(client, b, tid)]
    assert 'jacob speaking' not in bodies_a      # the blocker stops seeing them
    assert 'emma speaking' not in bodies_b       # and symmetrically, so the
    assert 'emma speaking' in bodies_a           # block is not observable
    assert 'jacob speaking' in bodies_b

    # Nobody was removed and nothing was deleted.
    roster = client.get(f'/api/teams/{tid}', headers=auth_headers(a)).get_json()
    assert roster['member_count'] == 2
    assert db.session.query(TeamMessage).filter_by(team_id=tid,
                                                   sender_type='user').count() == 2


def test_a_block_stops_a_challenge_naming_the_blocker(client, pair):
    a, b, team = pair
    tid = team['id']
    aid = client.get('/api/me', headers=auth_headers(a)).get_json()['id']
    bid = client.get('/api/me', headers=auth_headers(b)).get_json()['id']
    client.put(f'/api/blocks/{bid}', headers=auth_headers(a))

    # jacob, who does not know he is blocked, tries to name emma.
    r = client.post(f'/api/teams/{tid}/challenges',
                    json={'preset_key': _any_preset(), 'target_user_id': aid},
                    headers=auth_headers(b))
    assert r.status_code == 400
    # Same code a non-member gets: the refusal must not disclose the block.
    assert r.get_json()['error'] == 'not_a_team_member'


def test_unblocking_restores_the_thread(client, pair):
    a, b, team = pair
    tid = team['id']
    say(client, b, tid, 'jacob speaking')
    bid = client.get('/api/me', headers=auth_headers(b)).get_json()['id']
    client.put(f'/api/blocks/{bid}', headers=auth_headers(a))
    assert 'jacob speaking' not in [m['body'] for m in thread(client, a, tid)]
    client.delete(f'/api/blocks/{bid}', headers=auth_headers(a))
    assert 'jacob speaking' in [m['body'] for m in thread(client, a, tid)]


def _any_preset():
    from app import CHALLENGE_PRESETS_BY_KEY
    return next(iter(CHALLENGE_PRESETS_BY_KEY))


# --- Reporting ---------------------------------------------------------------

def test_report_a_person(client, pair):
    a, b, team = pair
    bid = client.get('/api/me', headers=auth_headers(b)).get_json()['id']
    r = client.post('/api/reports', json={
        'category': 'harassment', 'subject_type': 'user',
        'reported_user_id': bid, 'team_id': team['id'],
        'note': 'kept at it after I asked them to stop'},
        headers=auth_headers(a))
    assert r.status_code == 201
    body = r.get_json()
    assert body['status'] == 'pending' and body['report_id']
    # The response tells the reporter nothing about the other person.
    assert 'jacobreed' not in r.get_data(as_text=True)


def test_report_a_specific_message(client, pair):
    a, b, team = pair
    say(client, b, team['id'], 'something unpleasant')
    msg = [m for m in thread(client, a, team['id']) if m['body'] == 'something unpleasant'][0]
    assert msg['message_id']

    r = client.post('/api/reports', json={
        'category': 'inappropriate_content', 'subject_type': 'message',
        'subject_ref': msg['message_id'], 'team_id': team['id']},
        headers=auth_headers(a))
    assert r.status_code == 201
    rep = db.session.query(Report).one()
    assert rep.subject_type == 'message'
    assert rep.reported_user_id is not None      # derived from the evidence


@pytest.mark.parametrize('category',
                         ['harassment', 'threats', 'inappropriate_content',
                          'child_safety', 'spam', 'other'])
def test_every_documented_category_is_accepted(client, pair, category):
    a, b, team = pair
    bid = client.get('/api/me', headers=auth_headers(b)).get_json()['id']
    r = client.post('/api/reports', json={
        'category': category, 'subject_type': 'user',
        'reported_user_id': bid, 'team_id': team['id']},
        headers=auth_headers(a))
    assert r.status_code == 201


def test_an_unknown_category_is_refused_and_the_real_ones_are_named(client, pair):
    a, b, team = pair
    bid = client.get('/api/me', headers=auth_headers(b)).get_json()['id']
    r = client.post('/api/reports', json={
        'category': 'because-i-say-so', 'subject_type': 'user',
        'reported_user_id': bid, 'team_id': team['id']},
        headers=auth_headers(a))
    assert r.status_code == 400
    assert 'child_safety' in r.get_json()['categories']


def test_reporting_cannot_be_used_to_reach_content_you_cannot_see(client, pair):
    """The report route must not become a fetch-anything primitive."""
    a, b, team = pair
    outsider = register_and_login(client, 'strangerpat')
    say(client, b, team['id'], 'private to this team')
    msg = [m for m in thread(client, a, team['id'])][-1]

    r = client.post('/api/reports', json={
        'category': 'harassment', 'subject_type': 'message',
        'subject_ref': msg['message_id'], 'team_id': team['id']},
        headers=auth_headers(outsider))
    # 404, not 403. The security pass made every refusal identical -- missing,
    # someone else's, pre-join, deleted, already withheld -- because a 403 that
    # only appears for content that EXISTS confirms it exists.
    assert r.status_code == 404
    assert r.get_json() == {'error': 'not_found'}
    assert db.session.query(Report).count() == 0
    assert db.session.query(ReportEvidence).count() == 0
    assert 'private to this team' not in r.get_data(as_text=True)


def test_you_cannot_report_a_stranger_by_id(client, pair):
    a, _, team = pair
    stranger = register_and_login(client, 'strangerpat')
    sid = client.get('/api/me', headers=auth_headers(stranger)).get_json()['id']
    r = client.post('/api/reports', json={
        'category': 'harassment', 'subject_type': 'user',
        'reported_user_id': sid, 'team_id': team['id']},
        headers=auth_headers(a))
    assert r.status_code == 403


def test_you_cannot_report_yourself(client, pair):
    a, _, team = pair
    aid = client.get('/api/me', headers=auth_headers(a)).get_json()['id']
    r = client.post('/api/reports', json={
        'category': 'spam', 'subject_type': 'user',
        'reported_user_id': aid, 'team_id': team['id']},
        headers=auth_headers(a))
    assert r.status_code == 400


def test_reporting_requires_authentication(client):
    assert client.post('/api/reports', json={'category': 'spam'}).status_code == 401


# --- Reporter anonymity ------------------------------------------------------

def test_the_reported_person_cannot_discover_the_report(client, pair):
    a, b, team = pair
    bid = client.get('/api/me', headers=auth_headers(b)).get_json()['id']
    client.post('/api/reports', json={
        'category': 'harassment', 'subject_type': 'user',
        'reported_user_id': bid, 'team_id': team['id']},
        headers=auth_headers(a))

    # Nothing on any surface jacob can read mentions a report or the reporter.
    for path in (f'/api/teams/{team["id"]}',
                 f'/api/teams/{team["id"]}/messages',
                 f'/api/teams/{team["id"]}/moments',
                 '/api/me', '/api/teams'):
        raw = client.get(path, headers=auth_headers(b)).get_data(as_text=True)
        assert 'report' not in raw.lower()
        assert 'emmacarter' not in raw


def test_ordinary_members_have_no_route_to_reports(client, pair):
    a, b, team = pair
    bid = client.get('/api/me', headers=auth_headers(b)).get_json()['id']
    client.post('/api/reports', json={
        'category': 'threats', 'subject_type': 'user',
        'reported_user_id': bid, 'team_id': team['id']},
        headers=auth_headers(a))
    rep = db.session.query(Report).one()

    # Even the team creator, who has the creator safety exception elsewhere.
    for tok in (a, b):
        assert client.get('/api/admin/reports', headers=auth_headers(tok)).status_code == 403
        assert client.get(f'/api/admin/reports/{rep.public_id}',
                          headers=auth_headers(tok)).status_code == 403


# --- Evidence preservation ---------------------------------------------------

def test_evidence_survives_the_message_being_deleted(client, pair, admin_env):
    a, b, team = pair
    say(client, b, team['id'], 'the thing that was said')
    msg = [m for m in thread(client, a, team['id']) if m['body'] == 'the thing that was said'][0]
    client.post('/api/reports', json={
        'category': 'harassment', 'subject_type': 'message',
        'subject_ref': msg['message_id'], 'team_id': team['id']},
        headers=auth_headers(a))

    # The content disappears after the report.
    row = db.session.query(TeamMessage).filter_by(public_id=msg['message_id']).one()
    db.session.delete(row)
    db.session.commit()

    rep = db.session.query(Report).one()
    detail = client.get(f'/api/admin/reports/{rep.public_id}', headers=ADMIN).get_json()
    assert detail['evidence'][0]['content_text'] == 'the thing that was said'


def test_evidence_is_captured_at_report_time_not_read_time(client, pair, admin_env):
    a, b, team = pair
    say(client, b, team['id'], 'original wording')
    msg = [m for m in thread(client, a, team['id']) if m['body'] == 'original wording'][0]
    client.post('/api/reports', json={
        'category': 'harassment', 'subject_type': 'message',
        'subject_ref': msg['message_id'], 'team_id': team['id']},
        headers=auth_headers(a))

    row = db.session.query(TeamMessage).filter_by(public_id=msg['message_id']).one()
    row.body = 'innocuous replacement'
    db.session.commit()

    rep = db.session.query(Report).one()
    detail = client.get(f'/api/admin/reports/{rep.public_id}', headers=ADMIN).get_json()
    assert detail['evidence'][0]['content_text'] == 'original wording'


# --- Operator authorization --------------------------------------------------

def test_admin_routes_fail_closed_without_a_configured_secret(client, pair, monkeypatch):
    monkeypatch.delenv('ADMIN_SECRET', raising=False)
    assert client.get('/api/admin/reports').status_code == 403
    assert client.get('/api/admin/reports', headers={'X-Admin-Secret': ''}).status_code == 403


def test_admin_routes_reject_a_wrong_secret(client, pair, admin_env):
    assert client.get('/api/admin/reports',
                      headers={'X-Admin-Secret': 'nope'}).status_code == 403


def test_admin_queue_is_readable_with_the_secret(client, pair, admin_env):
    a, b, team = pair
    bid = client.get('/api/me', headers=auth_headers(b)).get_json()['id']
    client.post('/api/reports', json={
        'category': 'threats', 'subject_type': 'user',
        'reported_user_id': bid, 'team_id': team['id']},
        headers=auth_headers(a))
    q = client.get('/api/admin/reports', headers=ADMIN)
    assert q.status_code == 200
    body = q.get_json()
    # The queue is now an object: counts for the owner's daily glance, then
    # the rows. See the operations milestone.
    assert len(body['reports']) == 1
    assert body['counts']['pending'] == 1
    # The queue view withholds the reporter even from the operator listing.
    assert 'reporter_user_id' not in body['reports'][0]


# --- Moderation actions and enforcement --------------------------------------

def _file_message_report(client, a, b, team, body='needs review', category='harassment'):
    say(client, b, team['id'], body)
    msg = [m for m in thread(client, a, team['id']) if m['body'] == body][0]
    client.post('/api/reports', json={
        'category': category, 'subject_type': 'message',
        'subject_ref': msg['message_id'], 'team_id': team['id']},
        headers=auth_headers(a))
    return db.session.query(Report).order_by(Report.id.desc()).first(), msg


def test_restricting_content_hides_it_from_everyone(client, pair, admin_env):
    a, b, team = pair
    rep, msg = _file_message_report(client, a, b, team)
    assert 'needs review' in [m['body'] for m in thread(client, b, team['id'])]

    r = client.post(f'/api/admin/reports/{rep.public_id}/action',
                    json={'action': 'restrict_content'}, headers=ADMIN)
    assert r.status_code == 200

    # Gone for the author, the reporter, and anyone else.
    for tok in (a, b):
        assert 'needs review' not in [m['body'] for m in thread(client, tok, team['id'])]


def test_a_child_safety_report_restricts_the_content_immediately(client, pair, admin_env):
    """Fail-closed: hidden before a human has looked, not after."""
    a, b, team = pair
    rep, msg = _file_message_report(client, a, b, team,
                                    body='flagged for child safety',
                                    category='child_safety')
    assert 'flagged for child safety' not in [m['body'] for m in thread(client, b, team['id'])]
    assert db.session.query(ContentRestriction).filter_by(lifted_at=None).count() == 1
    # And the automatic action is in the audit trail, attributed to the system.
    act = db.session.query(ModerationAction).filter_by(action='content_restricted').one()
    assert act.actor == 'system'


def test_a_harassment_report_does_not_auto_restrict(client, pair, admin_env):
    """The asymmetry is deliberate and must stay narrow."""
    a, b, team = pair
    _file_message_report(client, a, b, team, body='ordinary complaint',
                         category='harassment')
    assert 'ordinary complaint' in [m['body'] for m in thread(client, b, team['id'])]
    assert db.session.query(ContentRestriction).count() == 0


def test_unrestricting_content_puts_it_back(client, pair, admin_env):
    a, b, team = pair
    rep, _ = _file_message_report(client, a, b, team, body='wrongly flagged',
                                  category='child_safety')
    assert 'wrongly flagged' not in [m['body'] for m in thread(client, b, team['id'])]
    client.post(f'/api/admin/reports/{rep.public_id}/action',
                json={'action': 'unrestrict_content'}, headers=ADMIN)
    assert 'wrongly flagged' in [m['body'] for m in thread(client, b, team['id'])]


def test_suspension_stops_posting_but_never_touches_progress(client, pair, admin_env):
    a, b, team = pair
    tid = team['id']

    # jacob earns some progress first.
    daily = client.get('/api/daily', headers=auth_headers(b)).get_json()
    for ex in daily['exercises']:
        client.post(f"/api/daily/{ex['key']}/complete", headers=auth_headers(b))
    before = client.get('/api/me', headers=auth_headers(b)).get_json()
    completions_before = db.session.query(DailyCompletion).count()

    bid = before['id']
    client.post('/api/reports', json={
        'category': 'threats', 'subject_type': 'user',
        'reported_user_id': bid, 'team_id': tid}, headers=auth_headers(a))
    rep = db.session.query(Report).order_by(Report.id.desc()).first()
    client.post(f'/api/admin/reports/{rep.public_id}/action',
                json={'action': 'suspend_social'}, headers=ADMIN)

    # Social writes refuse.
    r = say(client, b, tid, 'still here')
    assert r.status_code == 403
    assert r.get_json()['code'] == 'social_suspended'
    assert client.post(f'/api/teams/{tid}/challenges',
                       json={'preset_key': _any_preset()},
                       headers=auth_headers(b)).status_code == 403

    # Everything solo is untouched.
    after = client.get('/api/me', headers=auth_headers(b)).get_json()
    assert after['current_streak'] == before['current_streak']
    assert after['total_missions'] == before['total_missions']
    assert db.session.query(DailyCompletion).count() == completions_before
    # And they can still see their team; suspension is not exile.
    assert client.get(f'/api/teams/{tid}', headers=auth_headers(b)).status_code == 200


def test_lifting_a_suspension_restores_posting(client, pair, admin_env):
    a, b, team = pair
    bid = client.get('/api/me', headers=auth_headers(b)).get_json()['id']
    client.post('/api/reports', json={
        'category': 'threats', 'subject_type': 'user',
        'reported_user_id': bid, 'team_id': team['id']}, headers=auth_headers(a))
    rep = db.session.query(Report).order_by(Report.id.desc()).first()
    client.post(f'/api/admin/reports/{rep.public_id}/action',
                json={'action': 'suspend_social'}, headers=ADMIN)
    assert say(client, b, team['id'], 'x').status_code == 403
    client.post(f'/api/admin/reports/{rep.public_id}/action',
                json={'action': 'lift_suspension'}, headers=ADMIN)
    assert say(client, b, team['id'], 'x').status_code == 201


def test_removing_from_a_team_keeps_the_account_and_other_teams(client, pair, admin_env):
    a, b, team = pair
    other = mkteam(client, b, name='Reed Household')

    bid = client.get('/api/me', headers=auth_headers(b)).get_json()['id']
    client.post('/api/reports', json={
        'category': 'harassment', 'subject_type': 'user',
        'reported_user_id': bid, 'team_id': team['id']}, headers=auth_headers(a))
    rep = db.session.query(Report).order_by(Report.id.desc()).first()
    client.post(f'/api/admin/reports/{rep.public_id}/action',
                json={'action': 'remove_from_team'}, headers=ADMIN)

    assert client.get(f'/api/teams/{team["id"]}', headers=auth_headers(b)).status_code == 403
    assert client.get(f'/api/teams/{other["id"]}', headers=auth_headers(b)).status_code == 200
    assert client.get('/api/me', headers=auth_headers(b)).status_code == 200


def _upload_photo(client, token, team_id):
    """Reuses the photo suite's own valid JPEG rather than inventing bytes --
    a hand-rolled stub that the validator rejects turns this into a skip, and
    a skipped enforcement test is indistinguishable from an absent one."""
    from test_team_photos import VALID_JPEG
    return client.post(f'/api/teams/{team_id}/photos',
                       data={'photo': (io.BytesIO(VALID_JPEG), 'photo.jpg')},
                       content_type='multipart/form-data',
                       headers=auth_headers(token))


def test_restricted_photo_cannot_be_fetched_by_direct_api_request(client, pair, admin_env):
    """Not merely filtered out of the thread -- unreachable by its own id.

    This is the shape the `joined_at` work already had to get right: hiding a
    photo from the list while the bytes stay one request away is not hiding
    it at all.
    """
    a, b, team = pair
    up = _upload_photo(client, b, team['id'])
    assert up.status_code == 201, up.get_data(as_text=True)
    public_id = up.get_json()['photo']['public_id']

    # Readable before.
    assert client.get(f'/api/teams/{team["id"]}/photos/{public_id}',
                      headers=auth_headers(a)).status_code == 200

    db.session.add(ContentRestriction(subject_type='photo', subject_ref=public_id,
                                      reason='moderator'))
    db.session.commit()

    assert client.get(f'/api/teams/{team["id"]}/photos/{public_id}',
                      headers=auth_headers(a)).status_code == 404
    # Including for the person who posted it.
    assert client.get(f'/api/teams/{team["id"]}/photos/{public_id}',
                      headers=auth_headers(b)).status_code == 404


def test_a_blocked_senders_photo_bytes_are_unreachable(client, pair):
    a, b, team = pair
    up = _upload_photo(client, b, team['id'])
    assert up.status_code == 201, up.get_data(as_text=True)
    public_id = up.get_json()['photo']['public_id']
    assert client.get(f'/api/teams/{team["id"]}/photos/{public_id}',
                      headers=auth_headers(a)).status_code == 200

    bid = client.get('/api/me', headers=auth_headers(b)).get_json()['id']
    client.put(f'/api/blocks/{bid}', headers=auth_headers(a))

    assert client.get(f'/api/teams/{team["id"]}/photos/{public_id}',
                      headers=auth_headers(a)).status_code == 404
    # Still the sender's own photo, still theirs to see.
    assert client.get(f'/api/teams/{team["id"]}/photos/{public_id}',
                      headers=auth_headers(b)).status_code == 200


def test_a_suspended_user_cannot_upload_a_photo(client, pair, admin_env):
    a, b, team = pair
    bid = client.get('/api/me', headers=auth_headers(b)).get_json()['id']
    client.post('/api/reports', json={
        'category': 'inappropriate_content', 'subject_type': 'user',
        'reported_user_id': bid, 'team_id': team['id']}, headers=auth_headers(a))
    rep = db.session.query(Report).order_by(Report.id.desc()).first()
    client.post(f'/api/admin/reports/{rep.public_id}/action',
                json={'action': 'suspend_social'}, headers=ADMIN)

    up = _upload_photo(client, b, team['id'])
    assert up.status_code == 403
    assert up.get_json()['code'] == 'social_suspended'


def test_every_action_writes_an_audit_row(client, pair, admin_env):
    a, b, team = pair
    rep, _ = _file_message_report(client, a, b, team)
    before = db.session.query(ModerationAction).count()
    client.post(f'/api/admin/reports/{rep.public_id}/action',
                json={'action': 'dismiss', 'note': 'looked at it, fine'}, headers=ADMIN)
    rows = db.session.query(ModerationAction).all()
    assert len(rows) == before + 1
    assert rows[-1].action == 'dismiss' and rows[-1].actor == 'operator'
    assert rows[-1].note == 'looked at it, fine'


def test_an_unknown_action_is_refused(client, pair, admin_env):
    a, b, team = pair
    rep, _ = _file_message_report(client, a, b, team)
    r = client.post(f'/api/admin/reports/{rep.public_id}/action',
                    json={'action': 'delete_everything'}, headers=ADMIN)
    assert r.status_code == 400
    assert db.session.query(ModerationAction).filter_by(action='delete_everything').count() == 0


def test_actions_require_the_operator_secret(client, pair, admin_env):
    a, b, team = pair
    rep, _ = _file_message_report(client, a, b, team)
    for headers in ({}, auth_headers(a), {'X-Admin-Secret': 'wrong'}):
        assert client.post(f'/api/admin/reports/{rep.public_id}/action',
                           json={'action': 'dismiss'}, headers=headers).status_code == 403
    assert db.session.query(Report).filter_by(status='closed').count() == 0


# --- Boundaries that must not regress ----------------------------------------

def test_moderation_did_not_reopen_the_history_boundary(client, pair):
    """Nothing said before you arrived -- still true with the new filters."""
    a, b, team = pair
    say(client, a, team['id'], 'said before the latecomer arrived')
    late = register_and_login(client, 'noahpatel')
    join(client, late, team['id'], team['invite_code'])
    assert 'said before the latecomer arrived' not in [
        m['body'] for m in thread(client, late, team['id'])]


@pytest.mark.parametrize('path_tmpl', [
    '/api/teams/{tid}',
    '/api/teams/{tid}/moments',
    '/api/teams/{tid}/messages',
])
def test_the_four_username_paths_still_carry_no_login(client, pair, path_tmpl):
    a, b, team = pair
    say(client, b, team['id'], 'hello')
    raw = client.get(path_tmpl.format(tid=team['id']),
                     headers=auth_headers(a)).get_data(as_text=True)
    assert 'emmacarter' not in raw
    assert 'jacobreed' not in raw


def test_the_chat_post_echo_still_carries_no_peer_login(client, pair):
    a, b, team = pair
    r = say(client, b, team['id'], 'echo check')
    assert r.status_code == 201
    assert 'emmacarter' not in r.get_data(as_text=True)


def test_moderation_tables_are_not_exposed_through_any_user_route(client, pair, admin_env):
    a, b, team = pair
    rep, _ = _file_message_report(client, a, b, team, body='evidence text here')
    for path in (f'/api/teams/{team["id"]}', f'/api/teams/{team["id"]}/messages',
                 f'/api/teams/{team["id"]}/moments', '/api/teams', '/api/me',
                 '/api/blocks'):
        raw = client.get(path, headers=auth_headers(b)).get_data(as_text=True)
        assert rep.public_id not in raw
