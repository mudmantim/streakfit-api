"""When the alerts stop, how would anybody find out?

The uncomfortable property of an email alerting system is that the thing
which would tell you it is broken is the thing that is broken. So the
question these tests answer is not "does delivery retry" -- it does, and that
is covered elsewhere -- but "is the outage VISIBLE somewhere that does not
depend on email having worked".

The answer is `GET /api/verification/self`. It is unauthenticated, it is
served by the app itself, and it reports delivery as three separate facts. An
outage of any shape has to show up there, or nobody finds out until they
happen to open the queue.

Three shapes are tested: the provider refusing everything, the provider
refusing because a quota ran out, and the worker simply stopping.
"""
from datetime import datetime, timedelta

import pytest

import app as appmod
from app import (db, ModerationNotice, NotificationRun, NotificationChannel,
                 NotificationError, _deliver_pending_notices,
                 _persistently_failing_notices, _NOTIFY_PERSISTENT_AFTER,
                 DELIVERY_STALE_AFTER_HOURS, SOURCE_THREAD)
import uuid


def a_notice(kind='urgent_filed', minutes_ago=0):
    n = ModerationNotice(subject_type='report', subject_ref=uuid.uuid4().hex,
                         kind=kind,
                         created_at=datetime.utcnow() - timedelta(minutes=minutes_ago))
    db.session.add(n)
    db.session.commit()
    return n


def checks(client):
    return {c['id']: c for c in
            client.get('/api/verification/self').get_json()['checks']}


def resend_with(monkeypatch, status=None, boom=None):
    """A configured Resend channel whose provider misbehaves."""
    monkeypatch.setenv("STREAKFIT_NOTIFY_CHANNEL", "resend")
    monkeypatch.setenv("RESEND_API_KEY", "re_fake")
    monkeypatch.setenv("STREAKFIT_NOTIFY_FROM", "from@example.invalid")
    monkeypatch.setenv("STREAKFIT_NOTIFY_TO", "owner@example.invalid")
    calls = []

    def fake_post(url, payload, headers, timeout=10):
        calls.append(payload)
        if boom:
            raise boom
        return status, None

    monkeypatch.setattr(appmod, "_notify_http_post", fake_post)
    return calls


# ── 1. The provider is down ─────────────────────────────────────────────────

def test_a_provider_outage_never_marks_anything_delivered(client, monkeypatch):
    calls = resend_with(monkeypatch, boom=OSError("connection refused"))
    n = a_notice()

    for i in range(3):
        _deliver_pending_notices(source=SOURCE_THREAD,
                                 now=datetime.utcnow() + timedelta(hours=i))

    assert calls, "the channel never even tried"
    assert n.delivered_at is None
    assert n.receipt is None
    assert n.attempts >= 2


def test_a_provider_outage_is_visible_without_the_provider(client, monkeypatch):
    """The whole point. No email is needed to learn that email is broken."""
    resend_with(monkeypatch, boom=OSError("connection refused"))
    a_notice(kind='urgent_filed', minutes_ago=120)     # past the grace period

    _deliver_pending_notices(source=SOURCE_THREAD)

    c = checks(client)
    assert c['moderation.notices_delivered']['status'] == 'FAIL'
    assert 'undelivered' in c['moderation.notices_delivered']['observed']
    # And it says which report, by public id, without saying anything about it.
    assert c['moderation.notices_delivered']['failureReason']


def test_repeated_failure_is_surfaced_as_persistent(client, monkeypatch):
    resend_with(monkeypatch, status=500)
    n = a_notice(kind='overdue')
    now = datetime.utcnow()

    for i in range(_NOTIFY_PERSISTENT_AFTER + 1):
        _deliver_pending_notices(source=SOURCE_THREAD,
                                 now=now + timedelta(hours=6 * i))

    assert n in _persistently_failing_notices()
    assert 'failing persistently' in checks(client)['moderation.notices_delivered']['observed']


def test_the_worker_still_reports_that_it_ran_during_an_outage(client, monkeypatch):
    """A failing pass is still a pass. Conflating "the worker is dead" with
    "the provider is dead" would send somebody to fix the wrong thing."""
    resend_with(monkeypatch, status=503)
    monkeypatch.setattr(appmod, '_WORKER_STARTED_AT', datetime.utcnow())
    a_notice()

    _deliver_pending_notices(source=SOURCE_THREAD)

    c = checks(client)
    assert c['moderation.delivery_worker']['status'] == 'PASS', \
        "a provider outage was reported as a dead worker"
    assert c['moderation.delivery_configured']['status'] == 'PASS', \
        "the channel IS configured; it is the provider that is failing"


# ── 2. The free tier's daily cap ────────────────────────────────────────────

def test_a_429_is_a_failure_and_never_a_delivery(client, monkeypatch):
    """Resend's free tier stops at 100 messages a day. Hitting that ceiling is
    a delivery failure like any other, and must not be recorded as a send."""
    resend_with(monkeypatch, status=429)
    n = a_notice()

    _deliver_pending_notices(source=SOURCE_THREAD)

    assert n.delivered_at is None
    assert n.attempts == 1
    assert n.last_error


def test_a_quota_outage_keeps_retrying_and_recovers_when_it_clears(client, monkeypatch):
    """The cap resets daily; the notice must survive until then."""
    monkeypatch.setenv("STREAKFIT_NOTIFY_CHANNEL", "resend")
    monkeypatch.setenv("RESEND_API_KEY", "re_fake")
    monkeypatch.setenv("STREAKFIT_NOTIFY_FROM", "f@example.invalid")
    monkeypatch.setenv("STREAKFIT_NOTIFY_TO", "t@example.invalid")
    state = {'status': 429}

    def fake_post(url, payload, headers, timeout=10):
        return state['status'], ({"id": "ok"} if state['status'] == 200 else None)

    monkeypatch.setattr(appmod, "_notify_http_post", fake_post)
    n = a_notice()
    now = datetime.utcnow()

    for i in range(6):                      # a day of refusals
        _deliver_pending_notices(source=SOURCE_THREAD,
                                 now=now + timedelta(hours=4 * i))
    assert n.delivered_at is None

    state['status'] = 200                   # the cap resets
    _deliver_pending_notices(source=SOURCE_THREAD,
                             now=now + timedelta(hours=30))

    assert n.delivered_at is not None, "a recovered quota needed manual intervention"
    assert n.receipt == 'ok'


# ── 3. The worker stops ─────────────────────────────────────────────────────

def test_a_stopped_worker_is_reported_even_though_nothing_failed(client, monkeypatch):
    """No failures, no stuck queue -- and still broken. A worker that stopped
    delivers nothing, so there is nothing to fail."""
    monkeypatch.setattr(appmod, '_WORKER_STARTED_AT', None)
    db.session.add(NotificationRun(
        ran_at=datetime.utcnow() - timedelta(hours=DELIVERY_STALE_AFTER_HOURS + 2),
        source=SOURCE_THREAD, outcome='ok', attempted=0, delivered=0, failed=0))
    db.session.commit()

    c = checks(client)
    assert c['moderation.delivery_worker']['status'] == 'FAIL'
    assert 'ago' in c['moderation.delivery_worker']['observed']


def test_a_stopped_worker_also_stops_the_outcome_check_claiming_health(client, monkeypatch):
    """An empty queue plus a dead worker is not health -- nothing could have
    emptied it."""
    monkeypatch.setattr(appmod, '_WORKER_STARTED_AT', None)
    monkeypatch.delenv('STREAKFIT_NOTIFY_CHANNEL', raising=False)

    c = checks(client)
    assert c['moderation.notices_delivered']['status'] == 'UNKNOWN'
    assert 'not evidence' in c['moderation.notices_delivered']['observed']


# ── The three facts stay separable ──────────────────────────────────────────

@pytest.mark.parametrize("scenario,expect", [
    ("all_broken", ('FAIL', 'FAIL')),
    ("provider_only", ('PASS', 'PASS')),
])
def test_configuration_and_execution_never_collapse_into_one_answer(
        client, monkeypatch, scenario, expect):
    """Which of the three is broken determines who fixes what."""
    if scenario == "all_broken":
        monkeypatch.delenv('STREAKFIT_NOTIFY_CHANNEL', raising=False)
        monkeypatch.setattr(appmod, '_WORKER_STARTED_AT', None)
    else:
        resend_with(monkeypatch, status=500)
        monkeypatch.setattr(appmod, '_WORKER_STARTED_AT', datetime.utcnow())
        _deliver_pending_notices(source=SOURCE_THREAD)

    c = checks(client)
    assert (c['moderation.delivery_configured']['status'],
            c['moderation.delivery_worker']['status']) == expect


def test_the_outage_surface_needs_no_credential(client, monkeypatch):
    """If finding out required the admin secret, finding out would require
    being at a computer with it. This endpoint does not."""
    monkeypatch.delenv('ADMIN_SECRET', raising=False)
    resp = client.get('/api/verification/self')

    assert resp.status_code == 200
    ids = {c['id'] for c in resp.get_json()['checks']}
    assert {'moderation.delivery_configured', 'moderation.delivery_worker',
            'moderation.notices_delivered'} <= ids
