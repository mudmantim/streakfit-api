"""Running without anybody there: the worker, the cron, and the two together.

Two questions this file exists for, both found by audit rather than by use:

  1. A worker that has not reported YET and a worker that has STOPPED look
     identical in the notification_run table. They call for opposite
     responses -- one resolves itself in seconds, the other means nobody is
     being told anything until a person intervenes.

  2. The approved production shape runs BOTH an in-process worker and an
     independent Render cron. Two things delivering the same queue is a
     duplicate-send machine unless the claim is durable, so it is asserted
     here against the real delivery loop rather than argued from the design.
"""
import inspect
from datetime import datetime, timedelta

import pytest

import app as appmod
from app import (db, ModerationNotice, NotificationRun, NotificationChannel,
                 SOURCE_CRON, SOURCE_THREAD, SOURCE_MANUAL,
                 _deliver_pending_notices, _last_notification_run,
                 _RETENTION_FIRST_PASS_SETTLE_S, _WORKER_FIRST_PASS_GRACE_S,
                 DELIVERY_STALE_AFTER_HOURS)


class Works(NotificationChannel):
    name = 'fake'

    def __init__(self):
        self.sent = []

    def send(self, subject, body, idempotency_key=None):
        self.sent.append((subject, idempotency_key))
        return f"receipt-{len(self.sent)}"


def a_notice(kind='urgent_filed', ref='n' * 32, **kw):
    n = ModerationNotice(subject_type='report', subject_ref=ref, kind=kind,
                         created_at=datetime.utcnow(), **kw)
    db.session.add(n)
    db.session.commit()
    return n


def worker_check(client):
    payload = client.get('/api/verification/self').get_json()
    return {c['id']: c for c in payload['checks']}['moderation.delivery_worker']


@pytest.fixture(autouse=True)
def _no_worker_by_default(monkeypatch):
    """Most tests want a process with no sweeper, which is the default."""
    monkeypatch.setattr(appmod, '_WORKER_STARTED_AT', None)


# ── A starting worker is not a stopped worker ───────────────────────────────

def test_with_no_worker_and_no_pass_it_fails(client):
    """Nothing running and nothing recorded. The honest answer is FAIL."""
    check = worker_check(client)
    assert check['status'] == 'FAIL'
    assert 'never' in check['observed']


def test_a_worker_that_just_started_reports_unknown_not_fail(client, monkeypatch):
    """The blind window. A deploy must not read as a dead worker.

    Reproduced before the fix: the loop slept a full interval BEFORE its first
    pass, so every restart bought an hour of FAIL that was indistinguishable
    from a worker that had died.
    """
    monkeypatch.setattr(appmod, '_WORKER_STARTED_AT', datetime.utcnow())

    check = worker_check(client)
    assert check['status'] == 'UNKNOWN', check
    assert 'has not completed yet' in check['observed']
    assert 'resolves on its own' in (check['failureReason'] or '')


def test_a_worker_that_started_long_ago_and_never_reported_fails(client, monkeypatch):
    """The grace period is a grace period, not an amnesty."""
    long_ago = datetime.utcnow() - timedelta(
        seconds=_RETENTION_FIRST_PASS_SETTLE_S + _WORKER_FIRST_PASS_GRACE_S + 60)
    monkeypatch.setattr(appmod, '_WORKER_STARTED_AT', long_ago)

    check = worker_check(client)
    assert check['status'] == 'FAIL'
    assert 'no unattended delivery pass has ever run' in check['observed']


def test_the_grace_window_is_much_shorter_than_the_staleness_window(client):
    """A dead worker must be reported long before a human would notice it."""
    assert (_RETENTION_FIRST_PASS_SETTLE_S + _WORKER_FIRST_PASS_GRACE_S) < \
        DELIVERY_STALE_AFTER_HOURS * 3600 / 4


def test_a_restart_does_not_hide_a_worker_that_had_already_stopped(client, monkeypatch):
    """A stale run plus a brand-new worker reads as UNKNOWN, not PASS.

    The new worker has not proven anything yet; claiming PASS here would let a
    restart launder an outage into health.
    """
    db.session.add(NotificationRun(
        ran_at=datetime.utcnow() - timedelta(hours=DELIVERY_STALE_AFTER_HOURS + 5),
        source=SOURCE_THREAD, outcome='ok', attempted=0, delivered=0, failed=0))
    db.session.commit()
    monkeypatch.setattr(appmod, '_WORKER_STARTED_AT', datetime.utcnow())

    check = worker_check(client)
    assert check['status'] == 'UNKNOWN'
    assert 'has not reported yet' in check['observed']


def test_a_fresh_pass_passes_whatever_the_worker_clock_says(client, monkeypatch):
    monkeypatch.setattr(appmod, '_WORKER_STARTED_AT', datetime.utcnow())
    db.session.add(NotificationRun(
        ran_at=datetime.utcnow(), source=SOURCE_THREAD, outcome='ok',
        attempted=0, delivered=0, failed=0))
    db.session.commit()

    assert worker_check(client)['status'] == 'PASS'


def test_a_manual_run_never_satisfies_the_check_even_while_starting(client, monkeypatch):
    monkeypatch.setattr(appmod, '_WORKER_STARTED_AT', datetime.utcnow())
    db.session.add(NotificationRun(
        ran_at=datetime.utcnow(), source=SOURCE_MANUAL, outcome='ok',
        attempted=0, delivered=0, failed=0))
    db.session.commit()

    assert worker_check(client)['status'] == 'UNKNOWN'   # still nothing unattended


# ── The loop works first and sleeps afterwards ──────────────────────────────

def test_the_first_pass_is_not_gated_behind_a_full_interval():
    """Structural, because the behavioural version would take an hour.

    The defect was the ORDER of two statements: sleeping an interval before
    the first pass rather than after it.
    """
    src = inspect.getsource(appmod._retention_sweeper_loop)
    loop = src.index('while True:')

    assert src.index('_RETENTION_FIRST_PASS_SETTLE_S') < loop, \
        "the settle delay must come before the loop"
    body = src[loop:]
    assert body.rindex('time.sleep') > body.index('_deliver_pending_notices'), \
        "the interval sleep must come AFTER the delivery pass, not before it"
    assert '_RETENTION_THREAD_INTERVAL_S' in body, \
        "the loop must sleep the configured interval between passes"


# ── A cron and an in-process worker, together ───────────────────────────────

def test_a_cron_pass_and_a_worker_pass_send_a_notice_once(client):
    """The approved production shape: both are running, both see the queue."""
    n = a_notice()
    channel = Works()
    now = datetime.utcnow()

    _deliver_pending_notices(channel, now=now, source=SOURCE_THREAD)
    _deliver_pending_notices(channel, now=now, source=SOURCE_CRON)

    assert len(channel.sent) == 1, f"sent {len(channel.sent)} times"
    assert n.delivered_at is not None
    assert n.attempts == 1


def test_the_second_runner_finds_nothing_to_do_rather_than_redoing_it(client):
    """A delivered notice is not a candidate, so the second pass is a no-op.

    Asserted exactly rather than loosely: every counter zero is the claim.
    An earlier version of this test allowed "contended or skipped, either is
    honest", which would have passed just as happily if the second pass had
    re-sent and mis-counted it.
    """
    a_notice()
    channel = Works()
    now = datetime.utcnow()

    first = _deliver_pending_notices(channel, now=now, source=SOURCE_THREAD)
    second = _deliver_pending_notices(channel, now=now, source=SOURCE_CRON)

    assert first == {'delivered': 1, 'failed': 0, 'skipped': 0, 'contended': 0}
    assert second == {'delivered': 0, 'failed': 0, 'skipped': 0, 'contended': 0}
    assert len(channel.sent) == 1


def test_two_runners_that_both_scanned_first_still_send_once(client):
    """The actual race: both see the candidate before either has claimed it."""
    n = a_notice()
    now = datetime.utcnow()

    # Both scans happen before either claim -- the state a cron firing at the
    # same moment as the worker's pass actually produces.
    a_candidates = appmod._notices_for_delivery(now, 50)
    b_candidates = appmod._notices_for_delivery(now, 50)
    assert [c.id for c in a_candidates] == [c.id for c in b_candidates] == [n.id]

    assert appmod._claim_notice(n.id, now) is True, "first claim should win"
    assert appmod._claim_notice(n.id, now) is False, "second claim must lose"


def test_a_notice_held_under_an_unexpired_lease_is_left_alone(client):
    """A cron firing mid-send must not take the notice off the worker."""
    n = a_notice()
    now = datetime.utcnow()
    n.claimed_at, n.claimed_by = now, 'the-other-worker'
    db.session.commit()

    channel = Works()
    _deliver_pending_notices(channel, now=now + timedelta(minutes=1),
                             source=SOURCE_CRON)

    assert channel.sent == []
    assert n.delivered_at is None


def test_both_runners_record_their_own_pass(client):
    """Monitoring reads the most recent UNATTENDED run; cron and thread both
    qualify, and each must leave its own row."""
    channel = Works()
    now = datetime.utcnow()
    _deliver_pending_notices(channel, now=now, source=SOURCE_THREAD)
    _deliver_pending_notices(channel, now=now + timedelta(seconds=1),
                             source=SOURCE_CRON)

    rows = db.session.execute(db.select(NotificationRun)).scalars().all()
    assert {r.source for r in rows} == {SOURCE_THREAD, SOURCE_CRON}
    assert _last_notification_run(unattended_only=True).source == SOURCE_CRON


def test_a_crashed_worker_does_not_strand_a_notice_forever(client):
    """The cron picks up what a dead worker claimed, once the lease expires."""
    n = a_notice()
    now = datetime.utcnow()
    n.claimed_at, n.claimed_by = now, 'worker-that-died'
    db.session.commit()

    channel = Works()
    _deliver_pending_notices(channel, now=now + appmod._NOTIFY_LEASE + timedelta(seconds=1),
                             source=SOURCE_CRON)

    assert len(channel.sent) == 1
    assert n.delivered_at is not None


def test_many_interleaved_passes_still_send_once(client):
    """Ten alternating passes over one notice, as a blunt instrument."""
    a_notice()
    channel = Works()
    now = datetime.utcnow()
    for i in range(10):
        _deliver_pending_notices(
            channel, now=now + timedelta(seconds=i),
            source=SOURCE_THREAD if i % 2 == 0 else SOURCE_CRON)

    assert len(channel.sent) == 1


def test_the_settle_delay_never_outlasts_the_interval():
    """A short interval must mean a short first wait.

    The first version used a fixed 20-second settle regardless of interval, so
    a sweeper configured to cycle every 0.2s still waited 20s for its first
    pass. tests/test_coach_memory.py caught it by timing out.
    """
    src = inspect.getsource(appmod._retention_sweeper_loop)
    first_sleep = src[:src.index('while True:')]
    assert 'min(' in first_sleep, "the settle must be bounded by the interval"
    assert '_RETENTION_THREAD_INTERVAL_S' in first_sleep
