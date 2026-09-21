"""Delivery is a claim about the outside world, and this file polices it.

Every defect the operational-readiness audit reproduced has a test here named
after it (R1, R4-R7), alongside the original redaction and honesty checks.

No real message is sent anywhere. The channels are fakes, which is the whole
reason the interface exists separately from any provider.
"""
from datetime import datetime, timedelta

import pytest


import app as appmod
from app import (db, ModerationNotice, NotificationRun, Report, User,
                 NotificationError, NotificationChannel, ConsoleChannel,
                 SOURCE_THREAD, SOURCE_MANUAL,
                 _deliver_pending_notices, _notice_message, _notice_is_due,
                 _notification_channel, _generate_moderation_notices, _notices_for_delivery,
                 _claim_notice, _delivery_capability, _persistently_failing_notices,
                 _NOTIFY_LEASE, _NOTIFY_PERSISTENT_AFTER,
                 _NOTIFY_URGENT_MAX_INTERVAL_M)


class Works(NotificationChannel):
    """Accepts everything and hands back a receipt, like a real provider."""
    name = 'fake'

    def __init__(self):
        self.sent = []
        self.keys = []

    def send(self, subject, body, idempotency_key=None):
        self.sent.append((subject, body))
        self.keys.append(idempotency_key)
        return f"receipt-{len(self.sent)}"


class Fails(NotificationChannel):
    name = 'fake'

    def __init__(self):
        self.attempts = 0

    def send(self, subject, body, idempotency_key=None):
        self.attempts += 1
        raise NotificationError('provider said no')


class Silent(NotificationChannel):
    """Succeeds without saying what it did — no exception, no receipt."""
    name = 'fake'

    def send(self, subject, body, idempotency_key=None):
        return None


def a_notice(kind='urgent_filed', ref='r' * 32, minutes_ago=0, **kw):
    n = ModerationNotice(
        subject_type='report', subject_ref=ref, kind=kind,
        created_at=datetime.utcnow() - timedelta(minutes=minutes_ago), **kw)
    db.session.add(n)
    db.session.commit()
    return n


def checks(client):
    payload = client.get('/api/verification/self').get_json()
    return {c['id']: c for c in payload['checks']}


def state(client, name):
    return checks(client)[name]['status']


# --- R1: delivery happens without anybody typing a command --------------------

def test_R1_the_unattended_worker_delivers(client, monkeypatch):
    """The audit's most serious finding: _deliver_pending_notices had exactly
    one call site, the CLI. The worker generated notices hourly and delivered
    none of them, so the whole system waited on a human at a terminal."""
    a_notice(kind='urgent_filed')
    channel = Works()
    monkeypatch.setattr(appmod, '_notification_channel', lambda name=None: channel)

    class StopLoop(Exception):
        pass

    calls = {'n': 0}

    def fake_sleep(_s):
        calls['n'] += 1
        if calls['n'] > 1:
            raise StopLoop()

    monkeypatch.setattr(appmod.time, 'sleep', fake_sleep)
    with pytest.raises(StopLoop):
        appmod._retention_sweeper_loop()

    assert len(channel.sent) == 1, "the unattended worker delivered nothing"
    assert db.session.query(ModerationNotice).one().delivered_at is not None


def test_R1_the_worker_records_its_pass_as_unattended(client, monkeypatch):
    monkeypatch.setattr(appmod, '_notification_channel', lambda name=None: Works())

    class StopLoop(Exception):
        pass

    calls = {'n': 0}

    def fake_sleep(_s):
        calls['n'] += 1
        if calls['n'] > 1:
            raise StopLoop()

    monkeypatch.setattr(appmod.time, 'sleep', fake_sleep)
    with pytest.raises(StopLoop):
        appmod._retention_sweeper_loop()

    run = db.session.query(NotificationRun).order_by(
        NotificationRun.id.desc()).first()
    assert run is not None and run.source == SOURCE_THREAD


def test_a_pass_that_sent_nothing_is_still_recorded(client):
    """An idle worker and an absent one must not look the same."""
    _deliver_pending_notices(Works(), source=SOURCE_THREAD)
    run = db.session.query(NotificationRun).one()
    assert run.attempted == 0 and run.delivered == 0


def test_a_pass_with_no_channel_is_recorded_as_having_none(client, monkeypatch):
    monkeypatch.delenv('STREAKFIT_NOTIFY_CHANNEL', raising=False)
    _deliver_pending_notices(source=SOURCE_THREAD)
    run = db.session.query(NotificationRun).one()
    assert run.channel is None, "a channel-less pass claimed a channel"


# --- R2: monitoring separates capability, worker and outcome ------------------

def test_R2_an_empty_queue_is_not_a_pass_without_a_provider(client, monkeypatch):
    """Reproduced defect: empty database, no provider, no worker -> PASS."""
    monkeypatch.delenv('STREAKFIT_NOTIFY_CHANNEL', raising=False)
    assert db.session.query(ModerationNotice).count() == 0

    c = checks(client)['moderation.notices_delivered']
    assert c['status'] == 'UNKNOWN', "an empty queue was reported as health"
    assert 'not evidence' in c['observed']


def test_R2_configuration_and_execution_are_separate_checks(client, monkeypatch):
    monkeypatch.delenv('STREAKFIT_NOTIFY_CHANNEL', raising=False)
    ids = set(checks(client))
    assert {'moderation.delivery_configured', 'moderation.delivery_worker',
            'moderation.notices_delivered'} <= ids


def test_R2_a_configured_channel_with_no_worker_still_fails(client, monkeypatch):
    """The state that reads healthiest and is not: a provider set up
    perfectly, and nothing ever calling it."""
    monkeypatch.setattr(appmod, '_notification_channel', lambda name=None: Works())
    # "No worker" is this test's PRECONDITION, so it is stated rather than
    # inherited. _WORKER_STARTED_AT is a process-wide global, and any earlier
    # test that really starts a sweeper leaves it set -- which turns the
    # assertion below from FAIL into UNKNOWN for the next two minutes.
    monkeypatch.setattr(appmod, '_WORKER_STARTED_AT', None)

    assert state(client, 'moderation.delivery_configured') == 'PASS'
    assert state(client, 'moderation.delivery_worker') == 'FAIL'
    assert state(client, 'moderation.notices_delivered') == 'UNKNOWN'


def test_R2_a_worker_with_no_channel_fails_configuration(client, monkeypatch):
    monkeypatch.delenv('STREAKFIT_NOTIFY_CHANNEL', raising=False)
    _deliver_pending_notices(source=SOURCE_THREAD)

    assert state(client, 'moderation.delivery_worker') == 'PASS'
    assert state(client, 'moderation.delivery_configured') == 'FAIL'
    assert state(client, 'moderation.notices_delivered') == 'UNKNOWN'


def test_R2_a_manual_pass_never_satisfies_the_worker_check(client, monkeypatch):
    monkeypatch.setattr(appmod, '_WORKER_STARTED_AT', None)   # see above
    _deliver_pending_notices(Works(), source=SOURCE_MANUAL)
    assert state(client, 'moderation.delivery_worker') == 'FAIL'


def test_R2_a_channel_that_cannot_certify_is_not_capability(client, monkeypatch):
    """`console` resolves to a channel and delivers nothing -- it raises by
    design. Counting it as configuration put the same false green one layer
    further in: configured, worker alive, empty queue, no alert possible."""
    monkeypatch.setenv('STREAKFIT_NOTIFY_CHANNEL', 'console')
    _deliver_pending_notices(source=SOURCE_THREAD)

    configured, worker_fresh, why = _delivery_capability()
    assert configured is False
    assert worker_fresh is True
    assert 'cannot certify delivery' in why

    c = checks(client)
    assert c['moderation.delivery_configured']['status'] == 'FAIL'
    assert c['moderation.notices_delivered']['status'] == 'UNKNOWN', \
        "a channel that always raises was read as working delivery"


def test_R2_console_can_never_mark_anything_delivered(client, monkeypatch):
    monkeypatch.setenv('STREAKFIT_NOTIFY_CHANNEL', 'console')
    n = a_notice()
    _deliver_pending_notices(source=SOURCE_THREAD)

    assert n.delivered_at is None
    assert n.attempts == 1


def test_R2_everything_working_finally_passes(client, monkeypatch):
    monkeypatch.setattr(appmod, '_notification_channel', lambda name=None: Works())
    _deliver_pending_notices(Works(), source=SOURCE_THREAD)

    assert state(client, 'moderation.delivery_configured') == 'PASS'
    assert state(client, 'moderation.delivery_worker') == 'PASS'
    assert state(client, 'moderation.notices_delivered') == 'PASS'


def test_R2_a_stuck_urgent_notice_still_fails_regardless(client, monkeypatch):
    monkeypatch.setattr(appmod, '_notification_channel', lambda name=None: Works())
    _deliver_pending_notices(Works(), source=SOURCE_THREAD)
    a_notice(kind='urgent_filed', minutes_ago=120)

    assert state(client, 'moderation.notices_delivered') == 'FAIL'


# --- R4: an urgent alert is never starved by an older backlog -----------------

def test_R4_a_new_urgent_alert_is_attempted_despite_an_older_backlog(client):
    """Reproduced: 50 older notices in backoff filled the batch window and the
    newest urgent notice was never even attempted."""
    recent = datetime.utcnow()
    for i in range(60):
        a_notice(kind='overdue', ref=f"old{i:029d}", minutes_ago=500 + i,
                 attempts=1, last_attempt_at=recent)
    urgent = a_notice(kind='urgent_filed', ref='u' * 32)

    channel = Works()
    _deliver_pending_notices(channel, now=datetime.utcnow(), limit=50)

    assert urgent.delivered_at is not None, \
        "the urgent alert was starved by an older backlog"


def test_R4_urgent_notices_are_ordered_ahead_of_ordinary_ones(client):
    a_notice(kind='overdue', ref='o' * 32, minutes_ago=900)
    a_notice(kind='urgent_filed', ref='u' * 32, minutes_ago=1)

    batch = _notices_for_delivery(datetime.utcnow(), limit=10)
    assert batch[0].kind == 'urgent_filed'


def test_R4_eligibility_is_applied_before_the_batch_limit(client):
    """A batch of one, with an ineligible notice ahead of an eligible one."""
    a_notice(kind='overdue', ref='b' * 32, minutes_ago=900,
             attempts=1, last_attempt_at=datetime.utcnow())
    ok = a_notice(kind='overdue', ref='c' * 32, minutes_ago=800)

    batch = _notices_for_delivery(datetime.utcnow(), limit=1)
    assert len(batch) == 1 and batch[0].id == ok.id


# --- R5: nothing urgent is ever permanently abandoned -------------------------

def test_R5_an_urgent_notice_is_never_exhausted(client):
    """Reproduced: abandoned after 6 attempts / 5.35h, never retried again,
    even once the provider recovered."""
    n = a_notice(kind='urgent_filed')
    now = datetime.utcnow()
    for _ in range(12):
        now += timedelta(hours=6)
        _deliver_pending_notices(Fails(), now=now)

    assert n.attempts == 12
    assert _notice_is_due(n, now + timedelta(hours=1)), \
        "an urgent child-safety alert was permanently abandoned"


def test_R5_a_recovered_provider_delivers_without_manual_intervention(client):
    n = a_notice(kind='urgent_filed')
    now = datetime.utcnow()
    for _ in range(12):
        now += timedelta(hours=6)
        _deliver_pending_notices(Fails(), now=now)
    assert n.delivered_at is None

    channel = Works()
    _deliver_pending_notices(channel, now=now + timedelta(hours=1))

    assert n.delivered_at is not None, \
        "a recovered provider needed a database edit to drain the backlog"
    assert len(channel.sent) == 1


def test_R5_urgent_retry_intervals_stay_bounded(client):
    n = a_notice(kind='urgent_filed', attempts=99,
                 last_attempt_at=datetime.utcnow())
    assert appmod._notice_backoff_minutes(n) == _NOTIFY_URGENT_MAX_INTERVAL_M


def test_R5_persistent_failures_are_visible_to_monitoring(client, monkeypatch):
    # A channel IS configured and a worker IS running -- the notice keeps
    # failing anyway, which is precisely the state that used to be silent.
    monkeypatch.setattr(appmod, '_notification_channel', lambda name=None: Works())
    n = a_notice(kind='overdue', attempts=_NOTIFY_PERSISTENT_AFTER - 1)
    _deliver_pending_notices(Fails(), source=SOURCE_THREAD)

    assert n.delivered_at is None
    assert n.attempts == _NOTIFY_PERSISTENT_AFTER

    assert n in _persistently_failing_notices()
    assert 'failing persistently' in checks(client)['moderation.notices_delivered']['observed']


# --- R6/R7: claims, concurrency and transaction isolation ---------------------

def test_R7_a_second_worker_cannot_claim_a_claimed_notice(client):
    n = a_notice()
    now = datetime.utcnow()

    assert _claim_notice(n.id, now) is True
    assert _claim_notice(n.id, now) is False, \
        "two workers both claimed the same notice"


def test_R7_two_passes_over_the_same_notice_send_once(client):
    """The reproduced duplicate: both workers read the row, both sent it."""
    a_notice()
    channel = Works()
    now = datetime.utcnow()

    _deliver_pending_notices(channel, now=now)
    _deliver_pending_notices(channel, now=now)

    assert len(channel.sent) == 1


def test_R7_a_stale_claim_is_recoverable_after_a_worker_crash(client):
    """A worker killed mid-send must not strand an urgent alert forever."""
    n = a_notice(kind='urgent_filed')
    crashed_at = datetime.utcnow() - _NOTIFY_LEASE - timedelta(minutes=1)
    n.claimed_at = crashed_at
    n.claimed_by = 'dead-worker'
    db.session.commit()

    channel = Works()
    _deliver_pending_notices(channel, now=datetime.utcnow())

    assert n.delivered_at is not None, "a dead worker's claim stranded the notice"


def test_R7_a_fresh_claim_is_respected(client):
    n = a_notice()
    n.claimed_at = datetime.utcnow()
    n.claimed_by = 'another-worker'
    db.session.commit()

    channel = Works()
    _deliver_pending_notices(channel, now=datetime.utcnow())

    assert len(channel.sent) == 0
    assert n.delivered_at is None


def test_R7_a_claim_alone_never_counts_as_delivery(client):
    n = a_notice()
    _claim_notice(n.id, datetime.utcnow())

    assert n.claimed_at is not None
    assert n.delivered_at is None, "a claim was mistaken for a delivery"


def test_R7_the_idempotency_key_is_minted_before_the_send(client):
    a_notice()
    channel = Works()
    _deliver_pending_notices(channel)

    assert channel.keys[0], "the provider was given no idempotency key"


def test_R7_a_retry_reuses_the_same_idempotency_key(client):
    """The key is what lets a provider collapse the duplicate created by a
    crash between acceptance and recording."""
    n = a_notice(kind='urgent_filed')
    _deliver_pending_notices(Fails(), now=datetime.utcnow())
    first_key = n.provider_key
    assert first_key

    channel = Works()
    _deliver_pending_notices(channel,
                             now=datetime.utcnow() + timedelta(minutes=30))
    assert channel.keys[0] == first_key


def test_R6_one_failing_record_does_not_erase_another_notices_delivery(client,
                                                                      monkeypatch):
    """Reproduced: one batch commit meant three sends and zero records."""
    a_notice(kind='urgent_filed', ref='a' * 32)
    a_notice(kind='urgent_filed', ref='b' * 32)

    channel = Works()
    real_commit = db.session.commit
    calls = {'n': 0}

    def flaky(*a, **k):
        calls['n'] += 1
        # Let claims through; break exactly one delivery record.
        if calls['n'] == 4:
            raise RuntimeError('database went away')
        return real_commit(*a, **k)

    monkeypatch.setattr(db.session, 'commit', flaky)
    _deliver_pending_notices(channel, now=datetime.utcnow())
    monkeypatch.undo()
    db.session.rollback()

    delivered = db.session.query(ModerationNotice).filter(
        ModerationNotice.delivered_at.isnot(None)).count()
    assert delivered >= 1, \
        "one failed commit erased every delivery record in the batch"


def test_R6_a_send_that_cannot_be_recorded_is_not_counted_as_delivered(client,
                                                                      monkeypatch):
    """Honesty under the worst interleaving: the provider took it and we
    could not write that down, so we must not claim it."""
    n = a_notice(kind='urgent_filed')
    channel = Works()
    real_commit = db.session.commit
    calls = {'n': 0}

    def flaky(*a, **k):
        calls['n'] += 1
        if calls['n'] >= 2:          # claim commits; the record does not
            raise RuntimeError('database went away')
        return real_commit(*a, **k)

    monkeypatch.setattr(db.session, 'commit', flaky)
    counts = _deliver_pending_notices(channel, now=datetime.utcnow(),
                                      record_run=False)
    monkeypatch.undo()
    db.session.rollback()

    assert len(channel.sent) == 1
    assert counts['delivered'] == 0, "an unrecorded send was counted delivered"
    assert db.session.get(ModerationNotice, n.id).delivered_at is None


def test_R6_a_message_that_cannot_be_composed_does_not_abort_the_batch(client,
                                                                      monkeypatch):
    good = a_notice(kind='urgent_filed', ref='g' * 32)
    bad = a_notice(kind='overdue', ref='z' * 32, minutes_ago=900)

    real = appmod._notice_message

    def picky(notice, base_url=None):
        if notice.subject_ref.startswith('z'):
            raise ValueError('malformed')
        return real(notice, base_url)

    monkeypatch.setattr(appmod, '_notice_message', picky)
    channel = Works()
    _deliver_pending_notices(channel, now=datetime.utcnow())

    assert good.delivered_at is not None
    assert bad.last_error == 'MessageError'


# --- the original guarantees, still holding -----------------------------------

def test_a_successful_send_records_the_receipt_and_the_channel(client):
    a_notice()
    _deliver_pending_notices(Works())
    n = db.session.query(ModerationNotice).one()

    assert n.delivered_at is not None
    assert n.channel == 'fake' and n.receipt == 'receipt-1'
    assert n.last_error is None
    assert n.claimed_at is None, "the claim was not released"


def test_a_failed_send_is_never_recorded_as_delivered(client):
    a_notice()
    _deliver_pending_notices(Fails())
    n = db.session.query(ModerationNotice).one()

    assert n.delivered_at is None and n.channel is None
    assert n.last_error == 'NotificationError' and n.attempts == 1
    assert n.claimed_at is None, "a failed send held its claim"


def test_a_channel_that_returns_no_receipt_is_treated_as_a_failure(client):
    a_notice()
    counts = _deliver_pending_notices(Silent())
    n = db.session.query(ModerationNotice).one()

    assert counts['delivered'] == 0 and n.delivered_at is None
    assert n.last_error == 'NotificationError'


def test_with_no_channel_configured_nothing_is_delivered_or_claimed(client,
                                                                    monkeypatch):
    monkeypatch.delenv('STREAKFIT_NOTIFY_CHANNEL', raising=False)
    a_notice()
    counts = _deliver_pending_notices()
    n = db.session.query(ModerationNotice).one()

    assert counts['delivered'] == 0 and n.delivered_at is None
    assert n.attempts == 0 and n.claimed_at is None


def test_the_console_channel_refuses_to_certify_delivery(client):
    a_notice()
    _deliver_pending_notices(ConsoleChannel())
    assert db.session.query(ModerationNotice).one().delivered_at is None


def test_an_already_delivered_notice_is_never_sent_twice(client):
    a_notice()
    channel = Works()
    for _ in range(3):
        _deliver_pending_notices(channel,
                                 now=datetime.utcnow() + timedelta(hours=1))
    assert len(channel.sent) == 1


def test_backoff_stops_a_bad_ten_minutes_becoming_a_hundred_sends(client):
    n = a_notice()
    channel = Fails()
    now = datetime.utcnow()
    for _ in range(10):
        _deliver_pending_notices(channel, now=now)

    assert channel.attempts == 1, f"backoff did not hold ({channel.attempts})"
    assert n.attempts == 1


def test_generation_is_still_idempotent_alongside_delivery(client):
    rep = Report(public_id='u' * 32, reporter_user_id=1,
                 category='child_safety', subject_type='message',
                 status='pending', created_at=datetime.utcnow())
    db.session.add(rep)
    db.session.commit()

    first = _generate_moderation_notices()
    db.session.commit()
    _deliver_pending_notices(Works())
    second = _generate_moderation_notices()
    db.session.commit()

    assert first['urgent_filed'] == 1 and second['urgent_filed'] == 0
    assert db.session.query(ModerationNotice).count() == 1


# --- what a message may carry -------------------------------------------------

def test_a_message_carries_a_pointer_and_never_the_case(client):
    n = a_notice(ref='abc123def456abc123def456abc12345')
    subject, body = _notice_message(n, base_url='https://example.test')

    assert 'child-safety' in subject
    assert 'abc123def456abc123def456abc12345' in body
    assert 'https://example.test/admin' in body


def test_a_message_never_names_anybody_or_quotes_anything(client):
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


def test_the_delivery_checks_expose_no_report_identifier(client):
    a_notice(kind='urgent_filed', ref='secretref00000000000000000000000',
             minutes_ago=120)
    payload = str(client.get('/api/verification/self').get_json())
    assert 'secretref' not in payload


def test_a_notification_run_carries_counts_and_nothing_else(client):
    a_notice(ref='leakyref0000000000000000000000ab')
    _deliver_pending_notices(Works(), source=SOURCE_THREAD)
    run = db.session.query(NotificationRun).one()

    assert 'leakyref' not in f"{run.source}{run.channel}{run.error_type}"


# --- the channel registry -----------------------------------------------------

def test_an_unknown_channel_name_yields_no_channel(client, monkeypatch):
    monkeypatch.setenv('STREAKFIT_NOTIFY_CHANNEL', 'sendgrid-typo')
    assert _notification_channel() is None


def test_the_default_is_no_delivery_at_all(client, monkeypatch):
    monkeypatch.delenv('STREAKFIT_NOTIFY_CHANNEL', raising=False)
    assert _notification_channel() is None
