"""Delivery is a claim about the outside world, and this file polices it.

`delivered_at` means a person can be told something reached them. Everything
here attacks that claim from a different angle: a channel that fails, a channel
that lies by returning nothing, a channel that is not configured at all, and a
message that quietly carries more than it should.

No real message is sent anywhere. The channels here are fakes, which is the
point of the interface existing separately from any provider.
"""
from datetime import datetime, timedelta

import pytest

from conftest import register_and_login, auth_headers

import app as appmod
from app import (db, ModerationNotice, Report, User, Appeal,
                 ModerationAction, NotificationError, NotificationChannel,
                 ConsoleChannel, _deliver_pending_notices, _notice_message,
                 _notice_is_due, _notification_channel,
                 _undelivered_urgent_notices, _generate_moderation_notices,
                 _NOTIFY_MAX_ATTEMPTS)


class Works(NotificationChannel):
    """Accepts everything and hands back a receipt, like a real provider."""
    name = 'fake'

    def __init__(self):
        self.sent = []

    def send(self, subject, body):
        self.sent.append((subject, body))
        return f"receipt-{len(self.sent)}"


class Fails(NotificationChannel):
    """Refuses everything, the way a provider outage does."""
    name = 'fake'

    def __init__(self):
        self.attempts = 0

    def send(self, subject, body):
        self.attempts += 1
        raise NotificationError('provider said no')


class Silent(NotificationChannel):
    """Succeeds without saying what it did. The most dangerous shape: no
    exception, no receipt, nothing that proves anything arrived."""
    name = 'fake'

    def send(self, subject, body):
        return None


def a_notice(kind='urgent_filed', ref='r' * 32, minutes_ago=0):
    n = ModerationNotice(
        subject_type='report', subject_ref=ref, kind=kind,
        created_at=datetime.utcnow() - timedelta(minutes=minutes_ago))
    db.session.add(n)
    db.session.commit()
    return n


def checks(client):
    payload = client.get('/api/verification/self').get_json()
    return {c['id']: c for c in payload['checks']}


# --- delivery is only ever claimed on proof -----------------------------------

def test_a_successful_send_records_the_receipt_and_the_channel(client):
    a_notice()
    channel = Works()

    counts = _deliver_pending_notices(channel)

    n = db.session.query(ModerationNotice).one()
    assert counts['delivered'] == 1
    assert n.delivered_at is not None
    assert n.channel == 'fake'
    assert n.receipt == 'receipt-1'
    assert n.last_error is None


def test_a_failed_send_is_never_recorded_as_delivered(client):
    a_notice()
    channel = Fails()

    counts = _deliver_pending_notices(channel)

    n = db.session.query(ModerationNotice).one()
    assert counts['failed'] == 1 and counts['delivered'] == 0
    assert n.delivered_at is None, "a failed send was recorded as delivered"
    assert n.channel is None and n.receipt is None
    assert n.last_error == 'NotificationError'
    assert n.attempts == 1


def test_a_channel_that_returns_no_receipt_is_treated_as_a_failure(client):
    """Silence is not proof. A provider that returns nothing has not told us
    the message arrived, and guessing in the optimistic direction here is what
    turns an unmet obligation into an apparently met one."""
    a_notice()

    counts = _deliver_pending_notices(Silent())

    n = db.session.query(ModerationNotice).one()
    assert counts['delivered'] == 0 and counts['failed'] == 1
    assert n.delivered_at is None
    assert n.last_error == 'NotificationError'


def test_with_no_channel_configured_nothing_is_delivered_or_claimed(client,
                                                                    monkeypatch):
    monkeypatch.delenv('STREAKFIT_NOTIFY_CHANNEL', raising=False)
    a_notice()

    counts = _deliver_pending_notices()

    n = db.session.query(ModerationNotice).one()
    assert counts == {'delivered': 0, 'failed': 0, 'skipped': 0}
    assert n.delivered_at is None
    assert n.attempts == 0, "an unconfigured channel consumed a retry"


def test_the_console_channel_refuses_to_certify_delivery(client):
    """Printing to a terminal nobody is watching is not telling somebody."""
    a_notice()

    counts = _deliver_pending_notices(ConsoleChannel())

    n = db.session.query(ModerationNotice).one()
    assert counts['delivered'] == 0
    assert n.delivered_at is None


def test_an_already_delivered_notice_is_never_sent_twice(client):
    a_notice()
    channel = Works()

    _deliver_pending_notices(channel)
    _deliver_pending_notices(channel)
    _deliver_pending_notices(channel)

    assert len(channel.sent) == 1, "a delivered notice was sent again"


# --- retries, without a flood -------------------------------------------------

def test_a_transient_failure_is_retried_rather_than_lost(client):
    n = a_notice()
    _deliver_pending_notices(Fails())
    assert n.attempts == 1 and n.delivered_at is None

    # The provider comes back. Backoff after one attempt is one minute.
    later = datetime.utcnow() + timedelta(minutes=2)
    channel = Works()
    _deliver_pending_notices(channel, now=later)

    assert n.delivered_at is not None
    assert n.receipt == 'receipt-1'


def test_backoff_stops_a_bad_ten_minutes_becoming_a_hundred_sends(client):
    n = a_notice()
    channel = Fails()
    now = datetime.utcnow()

    # Ten passes in the same minute.
    for _ in range(10):
        _deliver_pending_notices(channel, now=now)

    assert channel.attempts == 1, \
        f"backoff did not hold; {channel.attempts} sends in one minute"
    assert n.attempts == 1


def test_attempts_are_eventually_exhausted_rather_than_retried_forever(client):
    n = a_notice()
    channel = Fails()
    now = datetime.utcnow()

    for i in range(_NOTIFY_MAX_ATTEMPTS + 3):
        now = now + timedelta(hours=6)      # always past the longest backoff
        _deliver_pending_notices(channel, now=now)

    assert n.attempts == _NOTIFY_MAX_ATTEMPTS
    assert not _notice_is_due(n, now + timedelta(days=1))


def test_an_exhausted_notice_is_still_undelivered_not_quietly_closed(client):
    """Giving up on sending must never look like having sent."""
    n = a_notice()
    now = datetime.utcnow()
    for _ in range(_NOTIFY_MAX_ATTEMPTS + 2):
        now = now + timedelta(hours=6)
        _deliver_pending_notices(Fails(), now=now)

    assert n.delivered_at is None
    assert n in _undelivered_urgent_notices(now=now)


# --- generation stays idempotent ----------------------------------------------

def test_generation_is_still_idempotent_alongside_delivery(client):
    """The unique constraint is the older guarantee and must not have moved."""
    rep = Report(public_id='u' * 32, reporter_user_id=1,
                 category='child_safety', subject_type='message',
                 status='pending', created_at=datetime.utcnow())
    db.session.add(rep)
    db.session.commit()

    first = _generate_moderation_notices()
    db.session.commit()
    second = _generate_moderation_notices()
    db.session.commit()

    assert first['urgent_filed'] == 1
    assert second['urgent_filed'] == 0
    assert db.session.query(ModerationNotice).count() == 1


def test_delivering_a_notice_does_not_let_it_be_generated_again(client):
    rep = Report(public_id='v' * 32, reporter_user_id=1,
                 category='child_safety', subject_type='message',
                 status='pending', created_at=datetime.utcnow())
    db.session.add(rep)
    db.session.commit()
    _generate_moderation_notices()
    db.session.commit()

    _deliver_pending_notices(Works())
    _generate_moderation_notices()
    db.session.commit()

    assert db.session.query(ModerationNotice).count() == 1


# --- what a message may carry -------------------------------------------------

def test_a_message_carries_a_pointer_and_never_the_case(client):
    n = a_notice(ref='abc123def456abc123def456abc12345')
    subject, body = _notice_message(n, base_url='https://example.test')

    assert 'child-safety' in subject
    assert 'abc123def456abc123def456abc12345' in body
    assert 'https://example.test/admin' in body


def test_a_message_never_names_anybody_or_quotes_anything(client):
    """The table carries no content by design; the message must not put it
    back. Email is the least controlled surface in the system."""
    reporter = User(username='olivia.hill@example.com', password_hash='x')
    accused = User(username='someone.else@example.com', password_hash='x')
    db.session.add_all([reporter, accused])
    db.session.commit()
    rep = Report(public_id='w' * 32, reporter_user_id=reporter.id,
                 reported_user_id=accused.id, category='child_safety',
                 subject_type='message', subject_ref='m' * 32,
                 status='pending', note='he said something horrible to my kid',
                 created_at=datetime.utcnow())
    db.session.add(rep)
    db.session.commit()
    _generate_moderation_notices()
    db.session.commit()

    n = db.session.query(ModerationNotice).one()
    subject, body = _notice_message(n, base_url='https://example.test')
    blob = (subject + body).lower()

    for forbidden in ('olivia', 'someone.else', 'example.com',
                      'horrible', 'said something'):
        assert forbidden not in blob, f"the message leaked {forbidden!r}"


def test_a_message_never_carries_the_admin_secret(client, monkeypatch):
    monkeypatch.setenv('ADMIN_SECRET', 'super-secret-value')
    n = a_notice()
    subject, body = _notice_message(n, base_url='https://example.test')

    assert 'super-secret-value' not in subject + body


def test_every_notice_kind_has_a_fixed_subject_line(client):
    """Interpolating a report's own words into a subject is how content escapes
    a system that was careful everywhere else."""
    for kind in ('urgent_filed', 'overdue', 'appeal_filed'):
        n = ModerationNotice(subject_type='report', subject_ref='x' * 32,
                             kind=kind, created_at=datetime.utcnow())
        subject, _body = _notice_message(n)
        assert subject.startswith('StreakFit:')


# --- undelivered urgent work is visible ---------------------------------------

def test_an_undelivered_urgent_notice_fails_the_check(client):
    a_notice(kind='urgent_filed', minutes_ago=120)
    check = checks(client)['moderation.notices_delivered']

    assert check['status'] == 'FAIL'
    assert '24-hour clock' in check['failureReason']


def test_a_freshly_generated_notice_is_given_its_grace_period(client):
    """A notice seconds old is a retry in progress, not a broken channel."""
    a_notice(kind='urgent_filed', minutes_ago=0)
    assert checks(client)['moderation.notices_delivered']['status'] == 'PASS'


def test_a_delivered_urgent_notice_clears_the_check(client):
    a_notice(kind='urgent_filed', minutes_ago=120)
    _deliver_pending_notices(Works())

    assert checks(client)['moderation.notices_delivered']['status'] == 'PASS'


def test_an_undelivered_NON_urgent_notice_does_not_raise_the_alarm(client):
    """72-hour work is real but it is not the 24-hour clock, and a check that
    fires on everything gets ignored."""
    a_notice(kind='overdue', minutes_ago=600)
    assert checks(client)['moderation.notices_delivered']['status'] == 'PASS'


def test_the_check_reports_no_report_identifier(client):
    a_notice(kind='urgent_filed', ref='secretref00000000000000000000000',
             minutes_ago=120)
    payload = str(client.get('/api/verification/self').get_json())

    assert 'secretref' not in payload


# --- the channel registry -----------------------------------------------------

def test_an_unknown_channel_name_yields_no_channel(client, monkeypatch):
    """A typo in configuration must not silently become a working channel, and
    must not become a crash either."""
    monkeypatch.setenv('STREAKFIT_NOTIFY_CHANNEL', 'sendgrid-typo')
    assert _notification_channel() is None


def test_the_default_is_no_delivery_at_all(client, monkeypatch):
    monkeypatch.delenv('STREAKFIT_NOTIFY_CHANNEL', raising=False)
    assert _notification_channel() is None
