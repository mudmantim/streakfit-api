"""Expired team photos are erased, not merely hidden (privacy proposal Gap 2).

A team photo expires PHOTO_RETENTION_DAYS after it is shared. Before this the
photo stopped being SERVED (410) but its bytes and caption stayed in the
database for ever, were still served as text from /messages, and still counted
against the team's 150 MB quota -- so "Older photos free up space as they
expire" was false. The retention sweeper now erases them and records a
retention_run of kind 'team_photo', which /api/verification/self checks.
"""
import datetime
import uuid

import pytest

import app as A
from app import db, RetentionRun, RETENTION_TEAM_PHOTO
from conftest import auth_headers, register_and_login

from test_team_photos import VALID_JPEG, upload


def _now():
    return datetime.datetime.utcnow()


@pytest.fixture()
def fam(client):
    parent = register_and_login(client, 'exp_parent')
    team = client.post('/api/teams', json={'name': 'Exp Family'},
                       headers=auth_headers(parent)).get_json()['team']
    pid = client.get('/api/me', headers=auth_headers(parent)).get_json()['id']
    return {'tok': parent, 'uid': pid, 'tid': team['id']}


def _shared(client, fam, caption='beach day', age_days=31):
    """Upload through the real route, then age it."""
    msg = upload(client, fam['tok'], fam['tid'], caption=caption).get_json()
    photo = A.TeamPhoto.query.filter_by(public_id=msg['photo']['public_id']).one()
    when = _now() - datetime.timedelta(days=age_days)
    photo.created_at = when
    photo.expires_at = when + datetime.timedelta(days=A.PHOTO_RETENTION_DAYS)
    db.session.query(A.TeamMessage).filter_by(photo_id=photo.id).update(
        {A.TeamMessage.created_at: when})
    db.session.query(A.TeamMembership).filter_by(team_id=fam['tid']).update(
        {A.TeamMembership.joined_at: when - datetime.timedelta(days=1)})
    db.session.commit()
    return photo.id


def _runs():
    db.session.expire_all()
    return RetentionRun.query.filter_by(kind=RETENTION_TEAM_PHOTO).order_by(RetentionRun.id).all()


def _checks(client):
    return {c['id']: c for c in client.get('/api/verification/self').get_json()['checks']}


def test_an_expired_photo_loses_its_bytes_and_caption_everywhere(client, fam):
    pid = _shared(client, fam)
    A._sweeper_team_photo_step()

    db.session.expire_all()
    photo = db.session.get(A.TeamPhoto, pid)
    assert photo.image_data is None and photo.byte_size == 0 and photo.caption is None
    # The thread keeps its shape and still says EXPIRED, not "removed".
    assert photo.deleted_at is None
    msg = A.TeamMessage.query.filter_by(photo_id=pid).one()
    assert msg.body == ''
    thread = client.get(f"/api/teams/{fam['tid']}/messages",
                        headers=auth_headers(fam['tok'])).get_json()
    blob = str(thread)
    assert 'beach day' not in blob
    card = next(m['photo'] for m in (thread['messages'] if isinstance(thread, dict) else thread)
                if m.get('photo'))
    assert card['available'] is False and card['removed'] is False
    assert client.get(card['url'], headers=auth_headers(fam['tok'])).status_code in (404, 410)
    # History keeps the fact that a photo was shared.
    assert A.TeamMoment.query.filter_by(team_id=fam['tid'], moment_type='photo_shared').count() == 1

    run = _runs()[-1]
    assert run.source == 'thread' and run.outcome == 'ok' and run.deleted == 1
    assert run.detail == 'erased=1 held=0'


def test_a_live_photo_is_untouched(client, fam):
    pid = _shared(client, fam, age_days=1)
    A._sweeper_team_photo_step()
    db.session.expire_all()
    photo = db.session.get(A.TeamPhoto, pid)
    assert photo.image_data == VALID_JPEG and photo.caption == 'beach day'
    assert photo.byte_size == len(VALID_JPEG)


def test_the_boundary_is_the_expiry_instant(client, fam):
    pid = _shared(client, fam, age_days=1)
    photo = db.session.get(A.TeamPhoto, pid)
    now = _now()
    photo.expires_at = now + datetime.timedelta(seconds=5)
    db.session.commit()
    assert A._sweep_expired_team_photos(now=now)['erased'] == 0
    assert A._sweep_expired_team_photos(now=now + datetime.timedelta(seconds=5))['erased'] == 1


def test_an_idle_sweep_is_still_recorded(app):
    A._sweeper_team_photo_step()
    run = _runs()[-1]
    assert run.deleted == 0 and run.outcome == 'ok' and run.detail == 'erased=0 held=0'


def test_a_second_sweep_finds_nothing_left_to_erase(client, fam):
    _shared(client, fam)
    A._sweeper_team_photo_step()
    A._sweeper_team_photo_step()
    assert [r.deleted for r in _runs()] == [1, 0]


def test_a_photo_under_legal_hold_is_skipped(client, fam):
    pid = _shared(client, fam)
    photo = db.session.get(A.TeamPhoto, pid)
    db.session.add(A.Report(public_id=uuid.uuid4().hex, team_id=fam['tid'],
                            category='harassment', subject_type='photo',
                            subject_ref=photo.public_id, status='closed',
                            legal_hold=True, legal_hold_reason='test'))
    db.session.commit()
    A._sweeper_team_photo_step()
    db.session.expire_all()
    assert db.session.get(A.TeamPhoto, pid).image_data == VALID_JPEG
    assert _runs()[-1].detail == 'erased=0 held=1'


def test_an_expired_photo_stops_counting_against_the_quota_before_any_sweep(client, fam):
    big = A.TeamPhoto(public_id='e' * 32, team_id=fam['tid'], sender_user_id=fam['uid'],
                      byte_size=A.PHOTO_TEAM_QUOTA_BYTES, image_data=b'x',
                      created_at=_now() - datetime.timedelta(days=31),
                      expires_at=_now() - datetime.timedelta(days=1))
    db.session.add(big)
    db.session.commit()
    assert upload(client, fam['tok'], fam['tid']).status_code == 201


def test_a_live_photo_still_counts_against_the_quota(client, fam):
    big = A.TeamPhoto(public_id='l' * 32, team_id=fam['tid'], sender_user_id=fam['uid'],
                      byte_size=A.PHOTO_TEAM_QUOTA_BYTES, image_data=b'x',
                      expires_at=_now() + datetime.timedelta(days=1))
    db.session.add(big)
    db.session.commit()
    assert upload(client, fam['tok'], fam['tid']).status_code == 507


def test_the_check_passes_after_an_unattended_sweep(client, fam):
    assert _checks(client)['retention.team_photos']['status'] == 'UNKNOWN'
    A._sweeper_team_photo_step()
    assert _checks(client)['retention.team_photos']['status'] == 'PASS'


def test_a_manual_run_does_not_satisfy_the_check(app, client):
    out = app.test_cli_runner().invoke(args=['team-photo-prune'])
    assert out.exit_code == 0, out.output
    assert 'erased 0 expired team photo(s)' in out.output
    assert _runs()[-1].source == 'manual'
    assert _checks(client)['retention.team_photos']['status'] == 'UNKNOWN'
    out = app.test_cli_runner().invoke(args=['team-photo-prune', '--scheduled'])
    assert out.exit_code == 0 and _runs()[-1].source == 'cron'
    assert _checks(client)['retention.team_photos']['status'] == 'PASS'


def test_a_failed_sweep_is_recorded_and_fails_only_its_own_check(app, client, monkeypatch):
    def boom(now=None):
        raise RuntimeError('injected secret text')
    monkeypatch.setattr(A, '_sweep_expired_team_photos', boom)
    ok = A._run_sweeper_step('team photo sweep', A._sweeper_team_photo_step,
                             lambda exc: A._record_retention_failure(
                                 RETENTION_TEAM_PHOTO, A.SOURCE_THREAD, exc))
    assert ok is False
    run = _runs()[-1]
    assert run.outcome == 'failed' and run.error_type == 'RuntimeError'
    c = _checks(client)
    assert c['retention.team_photos']['status'] == 'FAIL'
    assert 'injected' not in str(c)
    # Another promise's record is not touched by this failure.
    assert RetentionRun.query.filter(RetentionRun.kind != RETENTION_TEAM_PHOTO).count() == 0


def test_the_team_photo_kind_fits_the_column_and_is_its_own_promise():
    assert len(RETENTION_TEAM_PHOTO) <= RetentionRun.kind.property.columns[0].type.length
    assert RETENTION_TEAM_PHOTO in A.RETENTION_KINDS
    assert len(set(A.RETENTION_KINDS)) == len(A.RETENTION_KINDS)
