"""Every obligation says itself once, and urgent still goes first.

Two kinds were missing, and their absence had the same shape: an obligation
that existed in the data and was never spoken aloud.

  * an ORDINARY report was silent until it went overdue, so the first thing
    anybody heard about a 72-hour promise was that it had been broken;
  * a deadline APPROACHING was never mentioned at all, which is the one
    moment when saying so can still change the outcome.

Adding them is also the moment the flat urgent-or-not ordering stopped being
safe: ordinary notices are created AT FILING, so they are the oldest rows in
the tail, and a busy day of them would have queued ahead of a deadline that
was already broken. The tiers are tested here for that reason.
"""
from datetime import datetime, timedelta

import pytest

import app as appmod
from app import (db, Report, ModerationNotice, NotificationChannel,
                 REVIEW_WINDOW_HOURS, REVIEW_WINDOW_DEFAULT_HOURS,
                 REVIEW_APPROACHING_HOURS, REVIEW_APPROACHING_DEFAULT_HOURS,
                 _generate_moderation_notices, _notices_for_delivery,
                 _deliver_pending_notices, _notice_message, _review_due_at,
                 SOURCE_THREAD)
import uuid


class Works(NotificationChannel):
    name = 'fake'

    def __init__(self):
        self.sent = []

    def send(self, subject, body, idempotency_key=None):
        self.sent.append((subject, body))
        return f"r{len(self.sent)}"


class Fails(NotificationChannel):
    name = 'fake'

    def send(self, subject, body, idempotency_key=None):
        raise appmod.NotificationError('nope')


def a_report(category='harassment', filed_hours_ago=0, status='pending'):
    created = datetime.utcnow() - timedelta(hours=filed_hours_ago)
    r = Report(public_id=uuid.uuid4().hex, reporter_user_id=1,
               subject_type='user', category=category, status=status,
               created_at=created, due_at=_review_due_at(category, created))
    db.session.add(r)
    db.session.commit()
    return r


def kinds_for(ref):
    return {n.kind for n in db.session.execute(
        db.select(ModerationNotice).where(
            ModerationNotice.subject_ref == ref)).scalars().all()}


# ── An ordinary report is noticed when it is FILED ──────────────────────────

def test_an_ordinary_report_is_noticed_at_filing(client):
    r = a_report('harassment')
    _generate_moderation_notices()
    assert 'report_filed' in kinds_for(r.public_id)


def test_a_child_safety_report_gets_the_urgent_kind_not_the_ordinary_one(client):
    r = a_report('child_safety')
    _generate_moderation_notices()

    kinds = kinds_for(r.public_id)
    assert 'urgent_filed' in kinds
    assert 'report_filed' not in kinds, "a child-safety report was also filed as ordinary"


def test_filing_is_noticed_exactly_once_however_often_the_sweep_runs(client):
    r = a_report('harassment')
    for _ in range(5):
        _generate_moderation_notices()

    rows = db.session.execute(db.select(ModerationNotice).where(
        ModerationNotice.subject_ref == r.public_id,
        ModerationNotice.kind == 'report_filed')).scalars().all()
    assert len(rows) == 1


def test_a_closed_report_is_not_noticed(client):
    r = a_report('harassment', status='actioned')
    _generate_moderation_notices()
    assert kinds_for(r.public_id) == set()


# ── A deadline that is coming ───────────────────────────────────────────────

def test_a_fresh_report_is_not_yet_approaching(client):
    r = a_report('harassment', filed_hours_ago=0)
    _generate_moderation_notices()
    assert 'deadline_approaching' not in kinds_for(r.public_id)


def test_a_report_inside_the_lead_time_is_noticed_as_approaching(client):
    """72h window, 18h lead -> approaching once 54h have passed."""
    lead = REVIEW_APPROACHING_DEFAULT_HOURS
    window = REVIEW_WINDOW_DEFAULT_HOURS
    r = a_report('harassment', filed_hours_ago=window - lead + 1)
    _generate_moderation_notices()

    assert 'deadline_approaching' in kinds_for(r.public_id)
    assert 'overdue' not in kinds_for(r.public_id), "not late yet"


def test_child_safety_gets_its_own_shorter_lead(client):
    """24h window, 6h lead -> approaching at 18h, not at 18h for everyone."""
    lead = REVIEW_APPROACHING_HOURS['child_safety']
    window = REVIEW_WINDOW_HOURS['child_safety']
    r = a_report('child_safety', filed_hours_ago=window - lead + 1)
    _generate_moderation_notices()
    assert 'deadline_approaching' in kinds_for(r.public_id)

    # An hour before the lead begins, nothing is said.
    r2 = a_report('child_safety', filed_hours_ago=window - lead - 1)
    _generate_moderation_notices()
    assert 'deadline_approaching' not in kinds_for(r2.public_id)


def test_an_overdue_report_is_overdue_not_approaching(client):
    """A deadline already missed is not a deadline coming up."""
    r = a_report('harassment', filed_hours_ago=REVIEW_WINDOW_DEFAULT_HOURS + 1)
    _generate_moderation_notices()

    kinds = kinds_for(r.public_id)
    assert 'overdue' in kinds
    assert 'deadline_approaching' not in kinds


def test_a_report_can_be_noticed_filed_then_approaching_then_overdue(client):
    """Three separate facts about one report, each said once."""
    r = a_report('harassment', filed_hours_ago=0)
    _generate_moderation_notices()
    assert kinds_for(r.public_id) == {'report_filed'}

    # `due_at` is fixed at filing and never recomputed -- that is deliberate,
    # so a report that is escalated or re-categorised keeps the deadline it
    # was born with. Advancing the clock therefore means moving `due_at`, not
    # `created_at`; moving `created_at` changes nothing, which is the point.
    r.due_at = datetime.utcnow() + timedelta(
        hours=REVIEW_APPROACHING_DEFAULT_HOURS - 1)
    db.session.commit()
    _generate_moderation_notices()
    assert kinds_for(r.public_id) == {'report_filed', 'deadline_approaching'}

    r.due_at = datetime.utcnow() - timedelta(hours=1)
    db.session.commit()
    _generate_moderation_notices()
    assert kinds_for(r.public_id) == {'report_filed', 'deadline_approaching', 'overdue'}


def test_approaching_is_noticed_once_not_every_sweep(client):
    r = a_report('harassment',
                 filed_hours_ago=REVIEW_WINDOW_DEFAULT_HOURS
                 - REVIEW_APPROACHING_DEFAULT_HOURS + 1)
    for _ in range(4):
        _generate_moderation_notices()

    rows = db.session.execute(db.select(ModerationNotice).where(
        ModerationNotice.subject_ref == r.public_id,
        ModerationNotice.kind == 'deadline_approaching')).scalars().all()
    assert len(rows) == 1


# ── Urgent still goes first ─────────────────────────────────────────────────

def _notice(kind, minutes_ago, ref=None):
    n = ModerationNotice(subject_type='report', subject_ref=ref or uuid.uuid4().hex,
                         kind=kind,
                         created_at=datetime.utcnow() - timedelta(minutes=minutes_ago))
    db.session.add(n)
    db.session.commit()
    return n


def test_urgent_outranks_everything_however_old_the_rest_are(client):
    """The ordinary kinds are created AT FILING, so they are always older."""
    for k in ('report_filed', 'deadline_approaching', 'overdue', 'appeal_filed'):
        _notice(k, minutes_ago=600)
    urgent = _notice('urgent_filed', minutes_ago=0)

    batch = _notices_for_delivery(datetime.utcnow(), 50)
    assert batch[0].id == urgent.id, [n.kind for n in batch]


def test_a_flood_of_newly_filed_reports_cannot_bury_an_overdue_one(client):
    """The starvation the tiers exist for, at batch size 1."""
    overdue = _notice('overdue', minutes_ago=5)
    for _ in range(40):
        _notice('report_filed', minutes_ago=600)      # older, and many

    batch = _notices_for_delivery(datetime.utcnow(), 1)
    assert len(batch) == 1
    assert batch[0].id == overdue.id, batch[0].kind


def test_an_approaching_deadline_outranks_an_ordinary_filing(client):
    filed = _notice('report_filed', minutes_ago=600)
    approaching = _notice('deadline_approaching', minutes_ago=1)

    batch = _notices_for_delivery(datetime.utcnow(), 50)
    assert [n.id for n in batch][:2] == [approaching.id, filed.id]


def test_the_full_priority_order_holds(client):
    for i, k in enumerate(['appeal_filed', 'report_filed',
                           'deadline_approaching', 'overdue', 'urgent_filed']):
        _notice(k, minutes_ago=i)
    batch = _notices_for_delivery(datetime.utcnow(), 50)
    assert [n.kind for n in batch][:4] == [
        'urgent_filed', 'overdue', 'deadline_approaching']  + ['report_filed']


# ── The new kinds behave like the old ones once queued ──────────────────────

@pytest.mark.parametrize("kind", ['report_filed', 'deadline_approaching'])
def test_a_new_kind_is_delivered_and_recorded(client, kind):
    n = _notice(kind, minutes_ago=0)
    channel = Works()
    _deliver_pending_notices(channel, source=SOURCE_THREAD)

    assert len(channel.sent) == 1
    assert n.delivered_at is not None
    assert n.receipt == 'r1'


@pytest.mark.parametrize("kind", ['report_filed', 'deadline_approaching'])
def test_a_failed_new_kind_retries_and_is_never_marked_delivered(client, kind):
    n = _notice(kind, minutes_ago=0)
    now = datetime.utcnow()
    for i in range(4):
        _deliver_pending_notices(Fails(), now=now + timedelta(hours=6 * i),
                                 source=SOURCE_THREAD)

    assert n.delivered_at is None
    assert n.attempts >= 2, n.attempts
    assert appmod._notice_is_due(n, now + timedelta(hours=48)), \
        "an ordinary notice was abandoned"


@pytest.mark.parametrize("kind", ['report_filed', 'deadline_approaching'])
def test_the_new_kinds_have_their_own_subject_line(client, kind):
    n = _notice(kind, minutes_ago=0)
    subject, _ = _notice_message(n)

    assert subject != 'StreakFit: moderation notice', "fell through to the default"
    assert subject in appmod._NOTICE_SUBJECTS.values()


# ── Privacy, for the new kinds too ──────────────────────────────────────────

@pytest.mark.parametrize("category", ['harassment', 'spam', 'child_safety'])
def test_a_new_kind_carries_a_pointer_and_never_the_case(client, category, monkeypatch):
    monkeypatch.setenv('STREAKFIT_PUBLIC_URL', 'https://streakfit.example')
    monkeypatch.setenv('ADMIN_SECRET', 'the-admin-secret-value')
    r = a_report(category, filed_hours_ago=REVIEW_WINDOW_DEFAULT_HOURS - 1)
    r.note = "he said something about my child at 14 Elm Street"
    db.session.commit()
    _generate_moderation_notices()

    for n in db.session.execute(db.select(ModerationNotice).where(
            ModerationNotice.subject_ref == r.public_id)).scalars().all():
        subject, body = _notice_message(n)
        blob = subject + body
        assert 'Elm Street' not in blob
        assert 'the-admin-secret-value' not in blob
        assert category not in blob, "the notice disclosed the report category"
        assert r.public_id in body, "the pointer is missing"


def test_the_recipient_is_still_configuration_for_the_new_kinds(client, monkeypatch):
    """A new kind must not open a new path to a new recipient."""
    monkeypatch.setenv("STREAKFIT_NOTIFY_CHANNEL", "resend")
    monkeypatch.setenv("RESEND_API_KEY", "re_fake")
    monkeypatch.setenv("STREAKFIT_NOTIFY_FROM", "from@example.invalid")
    monkeypatch.setenv("STREAKFIT_NOTIFY_TO", "owner@example.invalid")

    calls = []

    def fake_post(url, payload, headers, timeout=10):
        calls.append(payload)
        return 200, {"id": "x"}

    monkeypatch.setattr(appmod, "_notify_http_post", fake_post)
    _notice('report_filed', minutes_ago=0)
    _notice('deadline_approaching', minutes_ago=0)
    _deliver_pending_notices(source=SOURCE_THREAD)

    assert len(calls) == 2
    for payload in calls:
        assert payload['to'] == ["owner@example.invalid"]
        assert 'html' not in payload
