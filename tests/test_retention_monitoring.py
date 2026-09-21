"""Can monitoring tell a working sweeper from a dead one?

Every test here is adversarial in the same direction: it tries to make the
checks say PASS when the promise is not being kept. That is the only failure
mode worth testing for, because monitoring that under-reports gets noticed the
first time somebody looks, and monitoring that over-reports never does.

The specific trap this file exists for: before `RetentionRun.kind`, both
promises shared one table, so a moderation sweep would have satisfied a check
asking about conversations. A green board caused by the wrong process running
is worse than a blank one.
"""
from datetime import datetime, timedelta

import pytest


import app as appmod
from app import (db, RetentionRun, Report, PhotoEvidence,
                 RETENTION_COACH, RETENTION_MODERATION,
                 RETENTION_STALE_AFTER_HOURS,
                 _last_retention_run, _record_retention_run,
                 _record_retention_failure, _sweep_moderation_evidence,
                 _moderation_sweep_detail)


def checks(client):
    """{name: check} from the self-verification endpoint."""
    payload = client.get('/api/verification/self').get_json()
    return {c['id']: c for c in payload['checks']}


def state(client, name):
    return checks(client)[name]['status']


def a_run(kind, hours_ago=0, outcome='ok', source='thread', deleted=0,
          detail=None, error_type=None):
    db.session.add(RetentionRun(
        ran_at=datetime.utcnow() - timedelta(hours=hours_ago),
        deleted=deleted, source=source, kind=kind, outcome=outcome,
        detail=detail, error_type=error_type))
    db.session.commit()


# --- the separation between the two promises ---------------------------------

def test_with_nothing_recorded_neither_promise_claims_to_be_running(client):
    assert state(client, 'retention.recent') == 'UNKNOWN'
    assert state(client, 'retention.moderation') == 'UNKNOWN'


def test_a_moderation_sweep_does_not_make_conversation_retention_look_healthy(client):
    """The regression this column was added for.

    A single unfiltered `ORDER BY ran_at DESC` would return this row and report
    that conversations are being swept. They are not.
    """
    a_run(RETENTION_MODERATION, hours_ago=0)

    assert state(client, 'retention.moderation') == 'PASS'
    assert state(client, 'retention.recent') == 'UNKNOWN', \
        "a moderation sweep was read as evidence for the coach promise"


def test_a_coach_sweep_does_not_make_moderation_retention_look_healthy(client):
    """And symmetrically, which is the direction that leaks private content."""
    a_run(RETENTION_COACH, hours_ago=0)

    assert state(client, 'retention.recent') == 'PASS'
    assert state(client, 'retention.moderation') == 'UNKNOWN', \
        "a coach sweep was read as evidence that reported photos are deleted"


def test_one_promise_going_stale_does_not_drag_the_other_down(client):
    a_run(RETENTION_COACH, hours_ago=0)
    a_run(RETENTION_MODERATION, hours_ago=RETENTION_STALE_AFTER_HOURS + 1)

    assert state(client, 'retention.recent') == 'PASS'
    assert state(client, 'retention.moderation') == 'FAIL'


def test_the_lookup_helper_never_crosses_kinds(client):
    a_run(RETENTION_COACH, hours_ago=1)
    a_run(RETENTION_MODERATION, hours_ago=5)

    assert _last_retention_run(RETENTION_COACH).kind == RETENTION_COACH
    assert _last_retention_run(RETENTION_MODERATION).kind == RETENTION_MODERATION


# --- R3: a hand-typed sweep is not a scheduler --------------------------------

def test_R3_a_manual_sweep_never_passes_the_retention_check(client):
    """Reproduced: `flask moderation-prune` typed by hand recorded itself as
    'cron', so the board read PASS for 48 hours with no scheduler anywhere."""
    a_run(RETENTION_MODERATION, hours_ago=0, source=appmod.SOURCE_MANUAL)

    check = checks(client)['retention.moderation']
    assert check['status'] == 'UNKNOWN', \
        "somebody typing a command was read as unattended retention"
    assert 'not that anything sweeps on its own' in check['observed']


def test_R3_the_command_labels_itself_by_how_it_was_invoked(client):
    """The label comes from the invocation, not from the command's own wishes."""
    runner = appmod.app.test_cli_runner()

    runner.invoke(args=['moderation-prune'])
    assert _last_retention_run(RETENTION_MODERATION).source == appmod.SOURCE_MANUAL
    assert _last_retention_run(RETENTION_MODERATION, unattended_only=True) is None

    runner.invoke(args=['moderation-prune', '--scheduled'])
    assert _last_retention_run(RETENTION_MODERATION).source == appmod.SOURCE_CRON
    assert _last_retention_run(RETENTION_MODERATION, unattended_only=True) is not None


def test_R3_a_manual_run_does_not_hide_a_dead_scheduler(client):
    """Manual sweeps every hour must not paper over a scheduler that stopped."""
    a_run(RETENTION_MODERATION, hours_ago=RETENTION_STALE_AFTER_HOURS + 6,
          source=appmod.SOURCE_CRON)
    a_run(RETENTION_MODERATION, hours_ago=0, source=appmod.SOURCE_MANUAL)

    assert state(client, 'retention.moderation') == 'FAIL', \
        "a fresh manual sweep masked an unattended sweeper that had stopped"


def test_R3_an_unattended_sweep_is_what_passes(client):
    a_run(RETENTION_MODERATION, hours_ago=0, source=appmod.SOURCE_CRON)
    assert state(client, 'retention.moderation') == 'PASS'

    a_run(RETENTION_COACH, hours_ago=0, source=appmod.SOURCE_THREAD)
    assert state(client, 'retention.recent') == 'PASS'


def test_R3_the_helper_filters_manual_runs_out(client):
    a_run(RETENTION_COACH, hours_ago=2, source=appmod.SOURCE_THREAD)
    a_run(RETENTION_COACH, hours_ago=0, source=appmod.SOURCE_MANUAL)

    assert _last_retention_run(RETENTION_COACH).source == appmod.SOURCE_MANUAL
    unattended = _last_retention_run(RETENTION_COACH, unattended_only=True)
    assert unattended.source == appmod.SOURCE_THREAD


def test_R3_a_request_piggybacked_sweep_is_not_unattended_either(client):
    """`source='request'` means a user happened to arrive, not that anything
    runs on its own. An idle service stops sweeping and nobody is told."""
    a_run(RETENTION_COACH, hours_ago=0, source=appmod.SOURCE_REQUEST)

    assert _last_retention_run(RETENTION_COACH, unattended_only=True) is None
    assert state(client, 'retention.recent') == 'UNKNOWN'


# --- a sweeper that stopped ---------------------------------------------------

def test_a_sweeper_that_stopped_is_detected(client):
    """The whole point. Yesterday's success must not vouch for today."""
    a_run(RETENTION_MODERATION, hours_ago=RETENTION_STALE_AFTER_HOURS + 6)
    check = checks(client)['retention.moderation']

    assert check['status'] == 'FAIL'
    assert 'not being deleted' in check['failureReason']


def test_a_sweeper_just_inside_the_window_still_passes(client):
    a_run(RETENTION_MODERATION, hours_ago=RETENTION_STALE_AFTER_HOURS - 1)
    assert state(client, 'retention.moderation') == 'PASS'


def test_a_sweep_that_deleted_nothing_still_counts_as_running(client):
    """'It ran and there was nothing expired' is the common case and the one
    monitoring needs most often. A row-count check would call this silence."""
    a_run(RETENTION_MODERATION, hours_ago=0, deleted=0,
          detail='text=0 photos=0 held=0')
    assert state(client, 'retention.moderation') == 'PASS'


# --- a sweeper that is failing ------------------------------------------------

def test_a_failing_sweep_is_recorded_and_fails_the_check(client):
    _record_retention_failure(RETENTION_MODERATION, 'thread',
                              RuntimeError('boom'))
    row = _last_retention_run(RETENTION_MODERATION)

    assert row.outcome == 'failed'
    assert row.error_type == 'RuntimeError'
    assert state(client, 'retention.moderation') == 'FAIL'


def test_a_recent_failure_outranks_an_older_success(client):
    """A sweep that worked at 09:00 and has crashed every hour since is broken,
    and a check that reads only the newest row must see that."""
    a_run(RETENTION_MODERATION, hours_ago=2, outcome='ok')
    _record_retention_failure(RETENTION_MODERATION, 'thread', ValueError('x'))

    assert state(client, 'retention.moderation') == 'FAIL'


def test_a_failure_record_never_carries_the_exception_text(client):
    """A database error can quote a row back in its message, and this table is
    served over HTTP."""
    secret = 'olivia.hill@example.com said something private'
    _record_retention_failure(RETENTION_MODERATION, 'thread',
                              RuntimeError(secret))

    row = _last_retention_run(RETENTION_MODERATION)
    assert row.error_type == 'RuntimeError'
    blob = f"{row.error_type} {row.detail} {row.source}"
    assert 'olivia' not in blob and 'private' not in blob

    payload = client.get('/api/verification/self').get_json()
    assert 'olivia' not in str(payload)


def test_the_coach_check_also_reports_a_failure_rather_than_staleness(client):
    _record_retention_failure(RETENTION_COACH, 'thread', RuntimeError('boom'))
    assert state(client, 'retention.recent') == 'FAIL'


# --- a sweep that fails partway through ---------------------------------------

def test_a_sweep_that_fails_partway_records_no_success(client, monkeypatch):
    """The success row is added in the SAME transaction as the deletions, so a
    failure after some deletions rolls back both. What must never survive is a
    row claiming success for work that was undone.
    """
    _record_retention_run(RETENTION_MODERATION, 'thread', deleted=3,
                          detail='text=3 photos=0 held=0')
    # ...and then the sweep blows up before the caller commits.
    db.session.rollback()

    assert _last_retention_run(RETENTION_MODERATION) is None, \
        "a success record outlived the transaction whose work it described"


def test_the_thread_records_a_failure_when_the_sweep_raises(client, monkeypatch):
    """Fault injection on the real loop body, not a stand-in."""
    def explode(*a, **k):
        raise RuntimeError('sweep exploded')

    monkeypatch.setattr(appmod, '_sweep_moderation_evidence', explode)

    class StopLoop(Exception):
        pass

    calls = {'n': 0}

    def fake_sleep(_seconds):
        calls['n'] += 1
        if calls['n'] > 1:
            raise StopLoop()

    monkeypatch.setattr(appmod.time, 'sleep', fake_sleep)
    with pytest.raises(StopLoop):
        appmod._retention_sweeper_loop()

    row = _last_retention_run(RETENTION_MODERATION)
    assert row is not None, "a sweep that raised left no trace at all"
    assert row.outcome == 'failed'
    assert row.error_type == 'RuntimeError'


def test_the_thread_records_a_success_with_counts_when_the_sweep_works(client, monkeypatch):
    class StopLoop(Exception):
        pass

    calls = {'n': 0}

    def fake_sleep(_seconds):
        calls['n'] += 1
        if calls['n'] > 1:
            raise StopLoop()

    monkeypatch.setattr(appmod.time, 'sleep', fake_sleep)
    with pytest.raises(StopLoop):
        appmod._retention_sweeper_loop()

    row = _last_retention_run(RETENTION_MODERATION)
    assert row is not None and row.outcome == 'ok'
    assert row.source == 'thread'
    assert row.detail == 'text=0 photos=0 held=0'


def test_one_promise_failing_still_records_the_other(client, monkeypatch):
    """The two sweeps sit in separate try blocks precisely so this holds."""
    def explode(*a, **k):
        raise RuntimeError('moderation is broken')

    monkeypatch.setattr(appmod, '_sweep_moderation_evidence', explode)

    class StopLoop(Exception):
        pass

    calls = {'n': 0}

    def fake_sleep(_seconds):
        calls['n'] += 1
        if calls['n'] > 1:
            raise StopLoop()

    monkeypatch.setattr(appmod.time, 'sleep', fake_sleep)
    with pytest.raises(StopLoop):
        appmod._retention_sweeper_loop()

    assert _last_retention_run(RETENTION_COACH) is not None, \
        "a broken moderation sweep stopped the coach promise being recorded"
    assert _last_retention_run(RETENTION_MODERATION).outcome == 'failed'


# --- what the record is allowed to contain ------------------------------------

def test_the_detail_field_is_counts_and_nothing_else(client):
    detail = _moderation_sweep_detail(
        {'text_evidence_purged': 2, 'photo_evidence_purged': 1,
         'held_by_legal_hold': 3})
    assert detail == 'text=2 photos=1 held=3'


def test_a_real_sweep_records_real_counts(client, monkeypatch):
    """End to end through the CLI path, against rows that genuinely expired."""
    rep = Report(public_id='r' * 32, reporter_user_id=1, category='harassment',
                 subject_type='photo', status='pending',
                 created_at=datetime.utcnow() - timedelta(days=60))
    db.session.add(rep)
    db.session.commit()
    db.session.add(PhotoEvidence(
        report_id=rep.id, photo_public_id='p' * 32, ciphertext=b'sealed',
        byte_size=6, content_type='image/jpeg',
        captured_at=datetime.utcnow() - timedelta(days=40),
        expires_at=datetime.utcnow() - timedelta(days=10)))
    db.session.commit()

    result = _sweep_moderation_evidence()
    _record_retention_run(RETENTION_MODERATION, 'cron',
                          deleted=result['text_evidence_purged']
                          + result['photo_evidence_purged'],
                          detail=_moderation_sweep_detail(result))
    db.session.commit()

    row = _last_retention_run(RETENTION_MODERATION)
    assert row.deleted == 1
    assert row.detail == 'text=0 photos=1 held=0'
    assert state(client, 'retention.moderation') == 'PASS'


def test_the_endpoint_exposes_no_report_identifiers(client):
    """The check is served publicly enough to matter; it must carry counts."""
    rep = Report(public_id='secretreportid1234567890abcdef12',
                 reporter_user_id=1, category='child_safety',
                 subject_type='photo', status='pending')
    db.session.add(rep)
    db.session.commit()
    a_run(RETENTION_MODERATION, hours_ago=0, deleted=1,
          detail='text=0 photos=1 held=0')

    payload = str(client.get('/api/verification/self').get_json())
    assert 'secretreportid' not in payload
