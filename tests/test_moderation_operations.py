"""Review deadlines, encrypted photo evidence, retention, and appeals.

The owner's seven decisions, checked against what the code does:

  1 reviewer is the owner       -> operator routes only, no reviewer account
  2 24h child_safety / 72h else -> test_deadlines_*
  3 hidden until DECIDED        -> test_child_safety_content_stays_hidden_*
  4 photos encrypted, 30 days   -> test_photo_evidence_*
  5 private appeals             -> test_appeal_*
  6 evidence 30 days post-close -> test_retention_*
  7 false reports need a human  -> test_reporting_restriction_*
"""
import io
import json
import os
from datetime import datetime, timedelta

import pytest

from conftest import register_and_login, auth_headers

from app import db, Report, ReportEvidence, PhotoEvidence, EvidenceAccess, \
    ContentRestriction, UserRestriction, ModerationAction, Appeal, TeamPhoto, \
    DailyCompletion, ModerationNotice, _sweep_moderation_evidence, \
    _review_due_at, _generate_moderation_notices, _review_queue_counts

ADMIN = {'X-Admin-Secret': 's3cret-value'}
# A throwaway Fernet key generated for the test process only. Never a default,
# never committed as an application value, never read by app code except from
# the environment.
TEST_KEY = 'Zm9vYmFyYmF6cXV1eGNvcmdlZ3JhdWx0Z2FycGx5MTIzND0='


@pytest.fixture()
def admin_env(monkeypatch):
    monkeypatch.setenv('ADMIN_SECRET', 's3cret-value')


@pytest.fixture()
def evidence_key(monkeypatch):
    from cryptography.fernet import Fernet
    monkeypatch.setenv('STREAKFIT_EVIDENCE_KEY', Fernet.generate_key().decode())


@pytest.fixture()
def no_evidence_key(monkeypatch):
    monkeypatch.delenv('STREAKFIT_EVIDENCE_KEY', raising=False)


@pytest.fixture()
def team(client):
    owner = register_and_login(client, 'ownerpat')
    member = register_and_login(client, 'memberjo')
    t = client.post('/api/teams', json={'name': 'Ops Team'},
                    headers=auth_headers(owner)).get_json()['team']
    client.post(f'/api/teams/{t["id"]}/join', json={'code': t['invite_code']},
                headers=auth_headers(member))
    return owner, member, t


def say(client, tok, tid, body):
    return client.post(f'/api/teams/{tid}/messages', json={'body': body},
                       headers=auth_headers(tok))


def thread(client, tok, tid):
    return client.get(f'/api/teams/{tid}/messages', headers=auth_headers(tok)).get_json()


def uid(client, tok):
    return client.get('/api/me', headers=auth_headers(tok)).get_json()['id']


def upload(client, tok, tid, caption=None):
    from test_team_photos import VALID_JPEG
    form = {'photo': (io.BytesIO(VALID_JPEG), 'p.jpg')}
    if caption is not None:
        form['caption'] = caption
    return client.post(f'/api/teams/{tid}/photos', data=form,
                       content_type='multipart/form-data', headers=auth_headers(tok))


def report_message(client, tok, tid, body, category='harassment', author_tok=None):
    say(client, author_tok, tid, body)
    msg = [m for m in thread(client, tok, tid) if m['body'] == body][0]
    r = client.post('/api/reports', json={
        'category': category, 'subject_type': 'message',
        'subject_ref': msg['message_id'], 'team_id': tid}, headers=auth_headers(tok))
    return r, msg


# --- Decision 2: review deadlines ---------------------------------------------

def test_child_safety_reports_are_due_in_24_hours(client, team, admin_env):
    owner, member, t = team
    r, _ = report_message(client, owner, t['id'], 'urgent thing',
                          category='child_safety', author_tok=member)
    assert r.status_code == 201
    rep = db.session.query(Report).one()
    assert rep.due_at is not None
    assert abs((rep.due_at - rep.created_at) - timedelta(hours=24)) < timedelta(seconds=2)


@pytest.mark.parametrize('category', ['harassment', 'threats',
                                      'inappropriate_content', 'spam', 'other'])
def test_other_reports_are_due_in_72_hours(client, team, admin_env, category):
    owner, member, t = team
    r, _ = report_message(client, owner, t['id'], f'thing {category}',
                          category=category, author_tok=member)
    rep = db.session.query(Report).one()
    assert abs((rep.due_at - rep.created_at) - timedelta(hours=72)) < timedelta(seconds=2)


def test_the_deadline_is_computed_once_and_never_moves(client, team, admin_env):
    """Escalating, or any other status change, must not restart the clock."""
    owner, member, t = team
    report_message(client, owner, t['id'], 'thing', author_tok=member)
    rep = db.session.query(Report).one()
    original = rep.due_at

    client.post(f'/api/admin/reports/{rep.public_id}/action',
                json={'action': 'escalate'}, headers=ADMIN)
    db.session.refresh(rep)
    assert rep.due_at == original
    assert rep.escalated_at is not None
    assert rep.status == 'pending', 'escalation is not a disposition'


def test_an_overdue_report_is_reported_as_overdue(client, team, admin_env):
    owner, member, t = team
    report_message(client, owner, t['id'], 'thing', author_tok=member)
    rep = db.session.query(Report).one()
    rep.due_at = datetime.utcnow() - timedelta(hours=1)
    db.session.commit()

    q = client.get('/api/admin/reports', headers=ADMIN).get_json()
    assert q['counts']['overdue'] == 1
    assert q['reports'][0]['overdue'] is True

    only = client.get('/api/admin/reports?status=overdue', headers=ADMIN).get_json()
    assert len(only['reports']) == 1


def test_the_queue_puts_urgent_work_first(client, team, admin_env):
    owner, member, t = team
    report_message(client, owner, t['id'], 'slow thing',
                   category='spam', author_tok=member)
    report_message(client, owner, t['id'], 'fast thing',
                   category='child_safety', author_tok=member)
    q = client.get('/api/admin/reports', headers=ADMIN).get_json()
    assert q['reports'][0]['category'] == 'child_safety'
    assert q['reports'][0]['urgent'] is True
    assert q['counts']['urgent_pending'] == 1


def test_the_queue_command_runs(client, team, admin_env):
    """The owner's actual discovery path while there is no notification
    channel: a CLI command, not a browser."""
    owner, member, t = team
    report_message(client, owner, t['id'], 'thing', author_tok=member)
    runner = client.application.test_cli_runner()
    out = runner.invoke(args=['moderation-queue']).output
    assert 'pending 1' in out


# --- Decision 3: hidden until decided -----------------------------------------

def test_child_safety_content_stays_hidden_past_its_deadline(client, team, admin_env):
    """An expired deadline must NOT restore content. Only a decision does."""
    owner, member, t = team
    r, msg = report_message(client, owner, t['id'], 'flagged material',
                            category='child_safety', author_tok=member)
    assert 'flagged material' not in [m['body'] for m in thread(client, member, t['id'])]

    rep = db.session.query(Report).one()
    rep.due_at = datetime.utcnow() - timedelta(days=7)
    db.session.commit()
    _sweep_moderation_evidence()
    db.session.commit()

    assert 'flagged material' not in [m['body'] for m in thread(client, member, t['id'])]
    assert db.session.query(ContentRestriction).filter_by(lifted_at=None).count() == 1


def test_only_a_moderation_action_lifts_a_restriction(client, team, admin_env):
    owner, member, t = team
    report_message(client, owner, t['id'], 'held', category='child_safety',
                   author_tok=member)
    rep = db.session.query(Report).one()
    # A member cannot lift it.
    assert client.post(f'/api/admin/reports/{rep.public_id}/action',
                       json={'action': 'unrestrict_content'},
                       headers=auth_headers(member)).status_code == 403
    assert 'held' not in [m['body'] for m in thread(client, member, t['id'])]

    client.post(f'/api/admin/reports/{rep.public_id}/action',
                json={'action': 'unrestrict_content'}, headers=ADMIN)
    assert 'held' in [m['body'] for m in thread(client, member, t['id'])]


# --- Overlapping reports ------------------------------------------------------

def test_closing_one_report_does_not_lift_another_reports_hold(client, team, admin_env):
    """Two people report the same message. Dismissing one must not un-hide it."""
    owner, member, t = team
    third = register_and_login(client, 'thirdana')
    client.post(f'/api/teams/{t["id"]}/join', json={'code': t['invite_code']},
                headers=auth_headers(third))
    say(client, member, t['id'], 'doubly reported')
    msg = [m for m in thread(client, owner, t['id']) if m['body'] == 'doubly reported'][0]

    for tok, cat in ((owner, 'child_safety'), (third, 'child_safety')):
        assert client.post('/api/reports', json={
            'category': cat, 'subject_type': 'message',
            'subject_ref': msg['message_id'], 'team_id': t['id']},
            headers=auth_headers(tok)).status_code == 201

    reports = db.session.query(Report).order_by(Report.id).all()
    assert len(reports) == 2
    assert db.session.query(ContentRestriction).filter_by(lifted_at=None).count() == 2

    client.post(f'/api/admin/reports/{reports[0].public_id}/action',
                json={'action': 'unrestrict_content'}, headers=ADMIN)

    assert 'doubly reported' not in [m['body'] for m in thread(client, member, t['id'])], \
        'the second report still holds it'
    assert db.session.query(ContentRestriction).filter_by(lifted_at=None).count() == 1
    db.session.refresh(reports[0])
    assert reports[0].disposition == 'content_allowed_other_holds_remain'

    client.post(f'/api/admin/reports/{reports[1].public_id}/action',
                json={'action': 'unrestrict_content'}, headers=ADMIN)
    assert 'doubly reported' in [m['body'] for m in thread(client, member, t['id'])]


# --- Decision 4: encrypted photo evidence -------------------------------------

def test_photo_evidence_is_encrypted_at_rest(client, team, admin_env, evidence_key):
    owner, member, t = team
    from test_team_photos import VALID_JPEG
    up = upload(client, member, t['id'], caption='a caption')
    pid = up.get_json()['photo']['public_id']
    client.post('/api/reports', json={
        'category': 'inappropriate_content', 'subject_type': 'photo',
        'subject_ref': pid, 'team_id': t['id']}, headers=auth_headers(owner))

    ev = db.session.query(PhotoEvidence).one()
    assert ev.ciphertext is not None
    assert VALID_JPEG not in ev.ciphertext, 'the plaintext image must not be stored'
    assert not ev.ciphertext.startswith(b'\xff\xd8'), 'not a bare JPEG'
    assert ev.key_id and len(ev.key_id) == 16
    assert abs((ev.expires_at - ev.captured_at) - timedelta(days=30)) < timedelta(seconds=2)


def test_an_authorized_reviewer_can_retrieve_the_image(client, team, admin_env, evidence_key):
    owner, member, t = team
    from test_team_photos import VALID_JPEG
    up = upload(client, member, t['id'])
    pid = up.get_json()['photo']['public_id']
    client.post('/api/reports', json={
        'category': 'inappropriate_content', 'subject_type': 'photo',
        'subject_ref': pid, 'team_id': t['id']}, headers=auth_headers(owner))
    rep = db.session.query(Report).one()

    r = client.get(f'/api/admin/reports/{rep.public_id}/photo-evidence', headers=ADMIN)
    assert r.status_code == 200
    assert r.get_data() == VALID_JPEG
    assert r.headers['Cache-Control'] == 'no-store'


def test_evidence_retrieval_is_operator_only(client, team, admin_env, evidence_key):
    owner, member, t = team
    up = upload(client, member, t['id'])
    pid = up.get_json()['photo']['public_id']
    client.post('/api/reports', json={
        'category': 'inappropriate_content', 'subject_type': 'photo',
        'subject_ref': pid, 'team_id': t['id']}, headers=auth_headers(owner))
    rep = db.session.query(Report).one()
    url = f'/api/admin/reports/{rep.public_id}/photo-evidence'
    for headers in ({}, auth_headers(owner), auth_headers(member),
                    {'X-Admin-Secret': 'wrong'}):
        assert client.get(url, headers=headers).status_code == 403


def test_every_evidence_access_writes_an_audit_row(client, team, admin_env, evidence_key):
    owner, member, t = team
    up = upload(client, member, t['id'])
    pid = up.get_json()['photo']['public_id']
    client.post('/api/reports', json={
        'category': 'inappropriate_content', 'subject_type': 'photo',
        'subject_ref': pid, 'team_id': t['id']}, headers=auth_headers(owner))
    rep = db.session.query(Report).one()

    assert db.session.query(EvidenceAccess).count() == 0
    client.get(f'/api/admin/reports/{rep.public_id}/photo-evidence', headers=ADMIN)
    client.get(f'/api/admin/reports/{rep.public_id}/photo-evidence', headers=ADMIN)
    rows = db.session.query(EvidenceAccess).all()
    assert len(rows) == 2
    assert all(x.outcome == 'served' and x.evidence_kind == 'photo' for x in rows)


def test_a_failed_access_is_audited_too(client, team, admin_env, evidence_key):
    owner, member, t = team
    report_message(client, owner, t['id'], 'text only', author_tok=member)
    rep = db.session.query(Report).one()
    r = client.get(f'/api/admin/reports/{rep.public_id}/photo-evidence', headers=ADMIN)
    assert r.status_code == 404
    assert db.session.query(EvidenceAccess).one().outcome == 'unavailable'


def test_with_no_key_nothing_is_captured_and_the_reason_is_recorded(
        client, team, admin_env, no_evidence_key):
    """Fail closed: never store a reported image in the clear."""
    owner, member, t = team
    up = upload(client, member, t['id'])
    pid = up.get_json()['photo']['public_id']
    client.post('/api/reports', json={
        'category': 'inappropriate_content', 'subject_type': 'photo',
        'subject_ref': pid, 'team_id': t['id']}, headers=auth_headers(owner))
    ev = db.session.query(PhotoEvidence).one()
    assert ev.ciphertext is None
    assert ev.unavailable_reason == 'no_evidence_key'


def test_evidence_is_not_reachable_through_ordinary_photo_routes(
        client, team, admin_env, evidence_key):
    owner, member, t = team
    up = upload(client, member, t['id'])
    pid = up.get_json()['photo']['public_id']
    client.post('/api/reports', json={
        'category': 'child_safety', 'subject_type': 'photo',
        'subject_ref': pid, 'team_id': t['id']}, headers=auth_headers(owner))

    # The auto-restrict hid the original; the evidence copy is not a way back in.
    assert client.get(f'/api/teams/{t["id"]}/photos/{pid}',
                      headers=auth_headers(owner)).status_code == 404
    assert client.get(f'/api/teams/{t["id"]}/photos/{pid}',
                      headers=auth_headers(member)).status_code == 404
    raw = client.get(f'/api/teams/{t["id"]}/messages',
                     headers=auth_headers(member)).get_data(as_text=True)
    assert pid not in raw


def test_a_deleted_original_still_leaves_a_recorded_reason(
        client, team, admin_env, evidence_key):
    owner, member, t = team
    up = upload(client, member, t['id'], caption='caption survives')
    pid = up.get_json()['photo']['public_id']
    client.delete(f'/api/teams/{t["id"]}/photos/{pid}', headers=auth_headers(member))
    # The photo is gone, so it is no longer reportable at all — and that is the
    # documented limitation: evidence capture happens at report time.
    r = client.post('/api/reports', json={
        'category': 'inappropriate_content', 'subject_type': 'photo',
        'subject_ref': pid, 'team_id': t['id']}, headers=auth_headers(owner))
    assert r.status_code == 404
    assert db.session.query(PhotoEvidence).count() == 0


# --- Decisions 4 + 6: retention ------------------------------------------------

def test_photo_evidence_is_purged_at_thirty_days(client, team, admin_env, evidence_key):
    owner, member, t = team
    up = upload(client, member, t['id'])
    pid = up.get_json()['photo']['public_id']
    client.post('/api/reports', json={
        'category': 'inappropriate_content', 'subject_type': 'photo',
        'subject_ref': pid, 'team_id': t['id']}, headers=auth_headers(owner))
    ev = db.session.query(PhotoEvidence).one()
    ev.expires_at = datetime.utcnow() - timedelta(seconds=1)
    db.session.commit()

    result = _sweep_moderation_evidence()
    db.session.commit()
    assert result['photo_evidence_purged'] == 1
    db.session.refresh(ev)
    assert ev.ciphertext is None and ev.purged_at is not None
    assert ev.unavailable_reason == 'retention_expired'


def test_text_evidence_is_purged_thirty_days_after_closure(client, team, admin_env):
    owner, member, t = team
    report_message(client, owner, t['id'], 'the words', author_tok=member)
    rep = db.session.query(Report).one()
    client.post(f'/api/admin/reports/{rep.public_id}/action',
                json={'action': 'dismiss'}, headers=ADMIN)
    db.session.refresh(rep)
    rep.reviewed_at = datetime.utcnow() - timedelta(days=31)
    db.session.commit()

    result = _sweep_moderation_evidence()
    db.session.commit()
    assert result['text_evidence_purged'] == 1
    ev = db.session.query(ReportEvidence).one()
    assert ev.content_text is None and ev.purged_at is not None
    # The minimal audit record survives.
    db.session.refresh(rep)
    assert rep.category == 'harassment' and rep.disposition == 'dismissed'
    assert rep.evidence_purged_at is not None


def test_an_open_report_keeps_its_evidence(client, team, admin_env):
    owner, member, t = team
    report_message(client, owner, t['id'], 'still open', author_tok=member)
    rep = db.session.query(Report).one()
    rep.created_at = datetime.utcnow() - timedelta(days=90)
    db.session.commit()
    _sweep_moderation_evidence()
    db.session.commit()
    assert db.session.query(ReportEvidence).one().content_text == 'still open'


def test_a_legal_hold_suppresses_deletion_and_releasing_it_restores_the_schedule(
        client, team, admin_env, evidence_key):
    owner, member, t = team
    report_message(client, owner, t['id'], 'preserve me', author_tok=member)
    rep = db.session.query(Report).one()
    client.post(f'/api/admin/reports/{rep.public_id}/action',
                json={'action': 'dismiss'}, headers=ADMIN)

    r = client.post(f'/api/admin/reports/{rep.public_id}/legal-hold',
                    json={'hold': True, 'reason': 'preservation request 2026-09'},
                    headers=ADMIN)
    assert r.status_code == 200
    db.session.refresh(rep)
    rep.reviewed_at = datetime.utcnow() - timedelta(days=31)
    db.session.commit()

    result = _sweep_moderation_evidence()
    db.session.commit()
    assert result['held_by_legal_hold'] == 1
    assert db.session.query(ReportEvidence).one().content_text == 'preserve me'

    client.post(f'/api/admin/reports/{rep.public_id}/legal-hold',
                json={'hold': False}, headers=ADMIN)
    _sweep_moderation_evidence()
    db.session.commit()
    assert db.session.query(ReportEvidence).one().content_text is None


def test_a_legal_hold_requires_a_written_reason(client, team, admin_env):
    owner, member, t = team
    report_message(client, owner, t['id'], 'x', author_tok=member)
    rep = db.session.query(Report).one()
    r = client.post(f'/api/admin/reports/{rep.public_id}/legal-hold',
                    json={'hold': True}, headers=ADMIN)
    assert r.status_code == 400


def test_the_sweep_touches_no_unrelated_user_data(client, team, admin_env):
    owner, member, t = team
    daily = client.get('/api/daily', headers=auth_headers(member)).get_json()
    for ex in daily['exercises']:
        client.post(f"/api/daily/{ex['key']}/complete", headers=auth_headers(member))
    before_me = client.get('/api/me', headers=auth_headers(member)).get_json()
    before_completions = db.session.query(DailyCompletion).count()
    say(client, member, t['id'], 'an ordinary message')

    report_message(client, owner, t['id'], 'reported thing', author_tok=member)
    rep = db.session.query(Report).one()
    client.post(f'/api/admin/reports/{rep.public_id}/action',
                json={'action': 'dismiss'}, headers=ADMIN)
    db.session.refresh(rep)
    rep.reviewed_at = datetime.utcnow() - timedelta(days=31)
    db.session.commit()
    _sweep_moderation_evidence()
    db.session.commit()

    after_me = client.get('/api/me', headers=auth_headers(member)).get_json()
    assert after_me['current_streak'] == before_me['current_streak']
    assert after_me['total_missions'] == before_me['total_missions']
    assert db.session.query(DailyCompletion).count() == before_completions
    assert 'an ordinary message' in [m['body'] for m in thread(client, member, t['id'])]
    assert 'reported thing' in [m['body'] for m in thread(client, member, t['id'])]


def test_the_prune_command_runs_and_reports(client, team, admin_env):
    runner = client.application.test_cli_runner()
    out = runner.invoke(args=['moderation-prune']).output
    assert 'purged' in out and 'legal hold' in out


# --- Decision 5: private appeals ----------------------------------------------

def _suspend(client, owner, member, t):
    client.post('/api/reports', json={
        'category': 'threats', 'subject_type': 'user',
        'reported_user_id': uid(client, member), 'team_id': t['id']},
        headers=auth_headers(owner))
    rep = db.session.query(Report).order_by(Report.id.desc()).first()
    client.post(f'/api/admin/reports/{rep.public_id}/action',
                json={'action': 'suspend_social'}, headers=ADMIN)
    return db.session.query(ModerationAction).filter_by(action='suspend_social').one()


def test_a_person_can_see_and_appeal_a_decision_about_them(client, team, admin_env):
    owner, member, t = team
    action = _suspend(client, owner, member, t)

    decisions = client.get('/api/moderation/decisions',
                           headers=auth_headers(member)).get_json()
    assert len(decisions) == 1 and decisions[0]['action'] == 'suspend_social'
    assert decisions[0]['appealable'] is True

    r = client.post('/api/appeals', json={
        'decision_id': decisions[0]['decision_id'],
        'reason': 'this was not me'}, headers=auth_headers(member))
    assert r.status_code == 201
    assert db.session.query(Appeal).one().user_id == uid(client, member)


def test_you_cannot_appeal_someone_elses_case(client, team, admin_env):
    owner, member, t = team
    action = _suspend(client, owner, member, t)
    other = register_and_login(client, 'nosyneil')
    r = client.post('/api/appeals', json={'decision_id': action.id, 'reason': 'me too'},
                    headers=auth_headers(other))
    assert r.status_code == 404, 'and 404, not 403 — a 403 confirms the id is real'
    assert db.session.query(Appeal).count() == 0


def test_the_decisions_list_shows_only_your_own(client, team, admin_env):
    owner, member, t = team
    _suspend(client, owner, member, t)
    assert client.get('/api/moderation/decisions',
                      headers=auth_headers(owner)).get_json() == []


def test_an_appeal_reveals_nothing_about_the_reporter_or_evidence(
        client, team, admin_env):
    owner, member, t = team
    say(client, member, t['id'], 'the reported words')
    msg = [m for m in thread(client, owner, t['id']) if m['body'] == 'the reported words'][0]
    client.post('/api/reports', json={
        'category': 'harassment', 'subject_type': 'message',
        'subject_ref': msg['message_id'], 'team_id': t['id'],
        'note': 'REPORTER PRIVATE NOTE'}, headers=auth_headers(owner))
    rep = db.session.query(Report).one()
    client.post(f'/api/admin/reports/{rep.public_id}/action',
                json={'action': 'restrict_content', 'note': 'REVIEWER INTERNAL NOTE'},
                headers=ADMIN)
    decisions = client.get('/api/moderation/decisions',
                           headers=auth_headers(member)).get_json()
    client.post('/api/appeals', json={'decision_id': decisions[0]['decision_id']},
                headers=auth_headers(member))

    raw = client.get('/api/appeals', headers=auth_headers(member)).get_data(as_text=True)
    raw += client.get('/api/moderation/decisions',
                      headers=auth_headers(member)).get_data(as_text=True)
    assert 'ownerpat' not in raw
    assert 'REPORTER PRIVATE NOTE' not in raw
    assert 'REVIEWER INTERNAL NOTE' not in raw
    assert msg['message_id'] not in raw


def test_filing_an_appeal_restores_nothing_by_itself(client, team, admin_env):
    owner, member, t = team
    _suspend(client, owner, member, t)
    decisions = client.get('/api/moderation/decisions',
                           headers=auth_headers(member)).get_json()
    client.post('/api/appeals', json={'decision_id': decisions[0]['decision_id']},
                headers=auth_headers(member))
    assert say(client, member, t['id'], 'am I back?').status_code == 403


def test_an_upheld_appeal_changes_nothing_an_overturned_one_reverses(
        client, team, admin_env):
    owner, member, t = team
    _suspend(client, owner, member, t)
    decisions = client.get('/api/moderation/decisions',
                           headers=auth_headers(member)).get_json()
    client.post('/api/appeals', json={'decision_id': decisions[0]['decision_id']},
                headers=auth_headers(member))
    ap = db.session.query(Appeal).one()

    client.post(f'/api/admin/appeals/{ap.public_id}/decide',
                json={'outcome': 'upheld', 'note': 'stands'}, headers=ADMIN)
    assert say(client, member, t['id'], 'still out').status_code == 403

    ap2 = db.session.query(Appeal).one()
    ap2.status, ap2.outcome = 'open', None
    db.session.commit()
    client.post(f'/api/admin/appeals/{ap2.public_id}/decide',
                json={'outcome': 'overturned', 'note': 'our mistake'}, headers=ADMIN)
    assert say(client, member, t['id'], 'back now').status_code == 201
    # The original action survives — overturning does not erase the record.
    assert db.session.query(ModerationAction).filter_by(action='suspend_social').count() == 1
    assert db.session.query(ModerationAction).filter_by(action='appeal_overturned').count() == 1


def test_the_appellant_sees_the_outcome(client, team, admin_env):
    owner, member, t = team
    _suspend(client, owner, member, t)
    decisions = client.get('/api/moderation/decisions',
                           headers=auth_headers(member)).get_json()
    client.post('/api/appeals', json={'decision_id': decisions[0]['decision_id']},
                headers=auth_headers(member))
    ap = db.session.query(Appeal).one()
    client.post(f'/api/admin/appeals/{ap.public_id}/decide',
                json={'outcome': 'upheld', 'note': 'we looked again and it stands'},
                headers=ADMIN)
    mine = client.get('/api/appeals', headers=auth_headers(member)).get_json()
    assert mine[0]['outcome'] == 'upheld'
    assert mine[0]['outcome_note'] == 'we looked again and it stands'


def test_appeals_are_operator_only_for_everyone_else(client, team, admin_env):
    owner, member, t = team
    _suspend(client, owner, member, t)
    decisions = client.get('/api/moderation/decisions',
                           headers=auth_headers(member)).get_json()
    client.post('/api/appeals', json={'decision_id': decisions[0]['decision_id']},
                headers=auth_headers(member))
    ap = db.session.query(Appeal).one()
    for headers in ({}, auth_headers(owner), auth_headers(member)):
        assert client.get('/api/admin/appeals', headers=headers).status_code == 403
        assert client.post(f'/api/admin/appeals/{ap.public_id}/decide',
                           json={'outcome': 'upheld'}, headers=headers).status_code == 403


def test_you_cannot_appeal_the_same_decision_twice(client, team, admin_env):
    owner, member, t = team
    _suspend(client, owner, member, t)
    decisions = client.get('/api/moderation/decisions',
                           headers=auth_headers(member)).get_json()
    did = decisions[0]['decision_id']
    assert client.post('/api/appeals', json={'decision_id': did},
                       headers=auth_headers(member)).status_code == 201
    assert client.post('/api/appeals', json={'decision_id': did},
                       headers=auth_headers(member)).status_code == 409


def test_blocking_and_urgent_reporting_survive_an_appeal(client, team, admin_env):
    owner, member, t = team
    _suspend(client, owner, member, t)
    assert client.put(f'/api/blocks/{uid(client, owner)}',
                      headers=auth_headers(member)).status_code == 204
    say(client, owner, t['id'], 'something alarming')
    msg = [m for m in thread(client, owner, t['id'])][-1]
    # Blocked, so that specific message is not reportable — but the person is.
    r = client.post('/api/reports', json={
        'category': 'child_safety', 'subject_type': 'user',
        'reported_user_id': uid(client, owner), 'team_id': t['id']},
        headers=auth_headers(member))
    assert r.status_code == 201


# --- Decision 7: repeated false reports ---------------------------------------

def test_dismissals_alone_never_restrict_anyone(client, team, admin_env):
    """A report that could not be substantiated is not a knowingly false one."""
    owner, member, t = team
    for i in range(5):
        report_message(client, owner, t['id'], f'complaint {i}', author_tok=member)
    for rep in db.session.query(Report).all():
        client.post(f'/api/admin/reports/{rep.public_id}/action',
                    json={'action': 'dismiss'}, headers=ADMIN)

    assert db.session.query(UserRestriction).filter_by(
        kind='reporting_restricted').count() == 0
    r, _ = report_message(client, owner, t['id'], 'complaint 6', author_tok=member)
    assert r.status_code == 201


def test_restricting_reporting_requires_a_written_reason(client, team, admin_env):
    owner, member, t = team
    report_message(client, owner, t['id'], 'x', author_tok=member)
    rep = db.session.query(Report).one()
    r = client.post(f'/api/admin/reports/{rep.public_id}/action',
                    json={'action': 'restrict_reporting'}, headers=ADMIN)
    assert r.status_code == 400 and r.get_json()['code'] == 'reason_required'
    assert db.session.query(UserRestriction).count() == 0


def test_a_reviewer_can_restrict_reporting_and_it_is_reversible(client, team, admin_env):
    owner, member, t = team
    # The abuser is the one filing; report THEM so the action targets them.
    client.post('/api/reports', json={
        'category': 'other', 'subject_type': 'user',
        'reported_user_id': uid(client, owner), 'team_id': t['id']},
        headers=auth_headers(member))
    rep = db.session.query(Report).order_by(Report.id.desc()).first()
    r = client.post(f'/api/admin/reports/{rep.public_id}/action',
                    json={'action': 'restrict_reporting',
                          'note': 'twelve fabricated reports, reviewed 2026-09-20'},
                    headers=ADMIN)
    assert r.status_code == 200
    restriction = db.session.query(UserRestriction).filter_by(
        kind='reporting_restricted').one()
    assert restriction.user_id == uid(client, owner)

    blocked = client.post('/api/reports', json={
        'category': 'spam', 'subject_type': 'user',
        'reported_user_id': uid(client, member), 'team_id': t['id']},
        headers=auth_headers(owner))
    assert blocked.status_code == 403
    assert blocked.get_json()['code'] == 'reporting_restricted'

    client.post(f'/api/admin/reports/{rep.public_id}/action',
                json={'action': 'lift_reporting_restriction'}, headers=ADMIN)
    assert client.post('/api/reports', json={
        'category': 'spam', 'subject_type': 'user',
        'reported_user_id': uid(client, member), 'team_id': t['id']},
        headers=auth_headers(owner)).status_code == 201


def test_a_reporting_restriction_never_blocks_child_safety_or_blocking(
        client, team, admin_env):
    """Owner decision 7, the carve-out that makes the rest defensible."""
    owner, member, t = team
    client.post('/api/reports', json={
        'category': 'other', 'subject_type': 'user',
        'reported_user_id': uid(client, owner), 'team_id': t['id']},
        headers=auth_headers(member))
    rep = db.session.query(Report).order_by(Report.id.desc()).first()
    client.post(f'/api/admin/reports/{rep.public_id}/action',
                json={'action': 'restrict_reporting', 'note': 'reviewed abuse'},
                headers=ADMIN)

    assert client.post('/api/reports', json={
        'category': 'child_safety', 'subject_type': 'user',
        'reported_user_id': uid(client, member), 'team_id': t['id']},
        headers=auth_headers(owner)).status_code == 201
    assert client.put(f'/api/blocks/{uid(client, member)}',
                      headers=auth_headers(owner)).status_code == 204


def test_a_reporting_restriction_is_appealable(client, team, admin_env):
    owner, member, t = team
    client.post('/api/reports', json={
        'category': 'other', 'subject_type': 'user',
        'reported_user_id': uid(client, owner), 'team_id': t['id']},
        headers=auth_headers(member))
    rep = db.session.query(Report).order_by(Report.id.desc()).first()
    client.post(f'/api/admin/reports/{rep.public_id}/action',
                json={'action': 'restrict_reporting', 'note': 'reviewed abuse'},
                headers=ADMIN)
    decisions = client.get('/api/moderation/decisions',
                           headers=auth_headers(owner)).get_json()
    assert any(d['action'] == 'restrict_reporting' for d in decisions)


def test_nothing_reveals_who_reported_whom(client, team, admin_env):
    owner, member, t = team
    report_message(client, owner, t['id'], 'reported', author_tok=member)
    for path in ('/api/moderation/decisions', '/api/appeals', '/api/blocks'):
        raw = client.get(path, headers=auth_headers(member)).get_data(as_text=True)
        assert 'ownerpat' not in raw


# --- Decision 2, continued: noticing the obligation ---------------------------
#
# A deadline nobody is told about is a deadline nobody meets. These check the
# GENERATION half -- that an obligation is recorded exactly once -- and are
# careful not to claim the delivery half, which has no channel yet.

def _notices(kind=None):
    q = db.session.query(ModerationNotice)
    if kind:
        q = q.filter(ModerationNotice.kind == kind)
    return q.all()


def test_a_child_safety_report_is_noticed_the_moment_it_is_filed(client, team, admin_env):
    """It does not wait to go overdue. A 24h clock spent waiting for the clock
    to run out is most of the window the owner promised, gone."""
    owner, member, t = team
    report_message(client, owner, t['id'], 'urgent', category='child_safety',
                   author_tok=member)
    made = _generate_moderation_notices()
    db.session.commit()

    assert made['urgent_filed'] == 1
    assert made['overdue'] == 0, 'it is not overdue yet, only urgent'
    rep = db.session.query(Report).one()
    assert [n.subject_ref for n in _notices('urgent_filed')] == [rep.public_id]


def test_an_ordinary_report_is_noticed_when_filed_and_again_when_overdue(
        client, team, admin_env):
    """REWRITTEN, because the behaviour it pinned was the defect.

    This test used to assert that an ordinary report produced NO notice until
    it went overdue -- so the first thing anybody heard about a 72-hour
    promise was that it had already been broken. `report_filed` exists to
    close that, and the two facts are separate: one report, noticed on
    arrival and noticed again when its deadline passes.
    """
    owner, member, t = team
    report_message(client, owner, t['id'], 'thing', author_tok=member)
    made = _generate_moderation_notices()
    db.session.commit()
    assert made['report_filed'] == 1
    assert made['urgent_filed'] == 0, 'an ordinary report is not urgent'
    assert made['overdue'] == 0, 'it is not late yet'
    assert [n.kind for n in _notices()] == ['report_filed']

    rep = db.session.query(Report).one()
    rep.due_at = datetime.utcnow() - timedelta(minutes=1)
    db.session.commit()

    made = _generate_moderation_notices()
    db.session.commit()
    assert made['overdue'] == 1
    assert made['report_filed'] == 0, 'filing is noticed once, not again'
    assert [n.subject_ref for n in _notices('overdue')] == [rep.public_id]


def test_generation_is_idempotent(client, team, admin_env):
    """The sweep runs hourly. An overdue report must be noticed once, not once
    an hour forever -- and the guarantee has to survive a restart, so it lives
    in the unique constraint rather than in the generator's memory."""
    owner, member, t = team
    report_message(client, owner, t['id'], 'urgent', category='child_safety',
                   author_tok=member)
    rep = db.session.query(Report).one()
    rep.due_at = datetime.utcnow() - timedelta(minutes=1)
    db.session.commit()

    first = _generate_moderation_notices()
    db.session.commit()
    assert first['urgent_filed'] == 1 and first['overdue'] == 1

    for _ in range(5):
        again = _generate_moderation_notices()
        db.session.commit()
        assert again == {'urgent_filed': 0, 'report_filed': 0,
                         'deadline_approaching': 0, 'overdue': 0,
                         'appeal_filed': 0}

    assert len(_notices()) == 2, 'one urgent + one overdue, however often it runs'


def test_a_closed_report_stops_generating_notices(client, team, admin_env):
    owner, member, t = team
    report_message(client, owner, t['id'], 'thing', author_tok=member)
    rep = db.session.query(Report).one()
    rep.due_at = datetime.utcnow() - timedelta(minutes=1)
    db.session.commit()

    client.post(f'/api/admin/reports/{rep.public_id}/action',
                json={'action': 'dismiss'}, headers=ADMIN)
    made = _generate_moderation_notices()
    db.session.commit()
    assert made['overdue'] == 0
    assert _notices() == []


def test_an_appeal_is_noticed_and_carries_no_content(client, team, admin_env):
    owner, member, t = team
    report_message(client, owner, t['id'], 'thing', author_tok=member)
    rep = db.session.query(Report).one()
    client.post(f'/api/admin/reports/{rep.public_id}/action',
                json={'action': 'suspend_social', 'note': 'reviewed'}, headers=ADMIN)
    action = db.session.query(ModerationAction).filter_by(
        action='suspend_social').one()

    r = client.post('/api/appeals', json={'decision_id': action.id,
                                          'reason': 'a private explanation'},
                    headers=auth_headers(member))
    assert r.status_code == 201

    made = _generate_moderation_notices()
    db.session.commit()
    assert made['appeal_filed'] == 1

    n = _notices('appeal_filed')[0]
    assert n.subject_type == 'appeal'
    # The notice is a pointer, never the person's words.
    assert 'a private explanation' not in (n.subject_ref or '')
    assert not hasattr(n, 'reason')


def test_a_notice_is_not_delivered_just_because_it_exists(client, team, admin_env):
    """The distinction the whole table exists to keep. Generating a notice is
    not telling anybody, and an undelivered row must read as 'nobody knows'."""
    owner, member, t = team
    report_message(client, owner, t['id'], 'urgent', category='child_safety',
                   author_tok=member)
    _generate_moderation_notices()
    db.session.commit()

    n = _notices('urgent_filed')[0]
    assert n.delivered_at is None
    assert n.channel is None
    assert _review_queue_counts()['undelivered_notices'] == 1


def test_the_notify_command_reports_without_marking_delivered(client, team, admin_env):
    owner, member, t = team
    report_message(client, owner, t['id'], 'urgent', category='child_safety',
                   author_tok=member)

    runner = client.application.test_cli_runner()
    out = runner.invoke(args=['moderation-notify']).output
    assert 'generated 1 urgent' in out
    assert 'not marked delivered' in out
    assert db.session.query(ModerationNotice).one().delivered_at is None

    out = runner.invoke(args=['moderation-notify', '--mark-delivered']).output
    assert 'marked 1 delivered via cli' in out
    n = db.session.query(ModerationNotice).one()
    assert n.delivered_at is not None and n.channel == 'cli'
    assert _review_queue_counts()['undelivered_notices'] == 0


# --- Decisions 4 and 6: the sweep actually runs --------------------------------
#
# `_sweep_moderation_evidence` being correct is worth nothing if nothing calls
# it. Before this the only caller was `flask moderation-prune`, which meant the
# 30-day promise held exactly as often as somebody remembered to type it.

class _StopLoop(Exception):
    """Ends the sweeper after one pass instead of sleeping for an hour."""


def test_the_retention_thread_sweeps_moderation_evidence(client, team, admin_env,
                                                         evidence_key, monkeypatch):
    """The scheduling mechanism, driven for one real iteration."""
    import app as app_module

    owner, member, t = team
    report_message(client, owner, t['id'], 'thing', author_tok=member)
    rep = db.session.query(Report).one()
    client.post(f'/api/admin/reports/{rep.public_id}/action',
                json={'action': 'dismiss'}, headers=ADMIN)
    db.session.refresh(rep)
    # Closed long enough ago that its evidence has aged out.
    rep.reviewed_at = datetime.utcnow() - timedelta(days=31)
    db.session.commit()
    assert rep.evidence_purged_at is None

    calls = []

    def fake_sleep(seconds):
        calls.append(seconds)
        if len(calls) > 1:
            raise _StopLoop()

    monkeypatch.setattr(app_module.time, 'sleep', fake_sleep)
    with pytest.raises(_StopLoop):
        app_module._retention_sweeper_loop()

    db.session.refresh(rep)
    assert rep.evidence_purged_at is not None, \
        'the scheduled sweep did not purge aged-out evidence'


def test_the_retention_thread_also_generates_notices(client, team, admin_env,
                                                     monkeypatch):
    import app as app_module

    owner, member, t = team
    report_message(client, owner, t['id'], 'urgent', category='child_safety',
                   author_tok=member)
    calls = []

    def fake_sleep(seconds):
        calls.append(seconds)
        if len(calls) > 1:
            raise _StopLoop()

    monkeypatch.setattr(app_module.time, 'sleep', fake_sleep)
    with pytest.raises(_StopLoop):
        app_module._retention_sweeper_loop()

    assert [n.kind for n in _notices()] == ['urgent_filed']


def test_one_failing_sweep_does_not_cancel_the_other(client, team, admin_env,
                                                     monkeypatch):
    """Separate try blocks, and this is why. The coach sweep failing must not
    be the reason an overdue child_safety report goes unnoticed."""
    import app as app_module

    owner, member, t = team
    report_message(client, owner, t['id'], 'urgent', category='child_safety',
                   author_tok=member)

    def boom(*a, **kw):
        raise RuntimeError('coach sweep exploded')

    monkeypatch.setattr(app_module, '_sweep_expired_coach_turns', boom)

    calls = []

    def fake_sleep(seconds):
        calls.append(seconds)
        if len(calls) > 1:
            raise _StopLoop()

    monkeypatch.setattr(app_module.time, 'sleep', fake_sleep)
    with pytest.raises(_StopLoop):
        app_module._retention_sweeper_loop()

    assert [n.kind for n in _notices()] == ['urgent_filed'], \
        'notice generation was taken down by an unrelated failure'
