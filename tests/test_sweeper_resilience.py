"""D42: the background worker must survive its own failures, and say so honestly.

The retention/moderation worker runs `_retention_sweeper_loop` in a thread of
the gunicorn master. In d7799d6 three of its four failure handlers called
`db.session.rollback()` after their `with app.app_context():` had closed;
that raised RuntimeError and ended the thread for good, silently, while the
self-check went on reporting PASS for hours.

Every test here runs the REAL loop in a BARE thread. That matters: the other
loop tests call it on the test thread, under the app context conftest pushes,
which is exactly what hid this defect. A thread does not inherit that context
(asserted below, so a future runtime that changes this cannot quietly turn
these tests into no-ops).

The loop is stopped by swapping `appmod.time` for a shim whose interval sleep
raises a private BaseException after N passes. Nothing waits, nothing touches
the global `time` module, and the only way the thread may end is that sentinel.

Two families:
  * SURVIVAL -- an exception in any step, in any failure handler, or in any
    app-context entry or teardown never ends the thread, and the next pass
    runs every step.
  * MONITORING -- surviving a failure must not turn it into PASS. Each step's
    latest outcome is its own evidence; success in one step never vouches for
    another; a later success of the SAME step does clear it (every step is a
    full re-scan, so a later success genuinely catches up).
"""
import threading
import time as _real_time
from contextlib import contextmanager

import flask
import pytest

import app as appmod
from app import (db, NotificationChannel, NotificationRun, RetentionRun,
                 RETENTION_COACH, RETENTION_MODERATION)

STEPS = ('_sweep_expired_coach_turns', '_sweep_moderation_evidence',
         '_generate_moderation_notices', '_deliver_pending_notices')


class _Stop(BaseException):
    """Ends the loop after N passes. BaseException so no `except Exception`
    inside the loop can swallow it -- if one ever does, the test hangs on its
    join timeout and fails, rather than passing."""


class _Clock:
    """Stands in for `appmod.time`. The first-pass settle returns at once; each
    interval sleep marks the end of a pass; after `passes` it raises _Stop."""

    def __init__(self, passes):
        self.passes = passes
        self.done = 0

    def sleep(self, seconds):
        if seconds == appmod._RETENTION_THREAD_INTERVAL_S:
            self.done += 1
            if self.done >= self.passes:
                raise _Stop()

    def __getattr__(self, name):
        return getattr(_real_time, name)


class Works(NotificationChannel):
    name = 'fake'

    def send(self, subject, body, idempotency_key=None):
        return 'receipt'


@pytest.fixture()
def loop(app, monkeypatch):
    """run(passes, faults={step: set(pass_numbers)}) -> result dict.

    Pass numbers are 1-based. Each step is wrapped to count its calls per pass
    and to raise on the listed passes."""
    monkeypatch.setattr(appmod, '_notification_channel', lambda name=None: Works())
    calls = {s: [] for s in STEPS}

    def run(passes, faults=None, before=None):
        faults = faults or {}
        clock = _Clock(passes)
        monkeypatch.setattr(appmod, 'time', clock)
        for step in STEPS:
            real = getattr(appmod, step)

            def wrapped(*a, _real=real, _step=step, **kw):
                n = clock.done + 1
                calls[_step].append(n)
                if n in faults.get(_step, ()):
                    raise RuntimeError(f'injected {_step} failure on pass {n}')
                return _real(*a, **kw)
            monkeypatch.setattr(appmod, step, wrapped)
        if before:
            before(clock)

        seen = {'exc': [], 'had_ctx': None}
        monkeypatch.setattr(threading, 'excepthook',
                            lambda args: seen['exc'].append(args.exc_type))

        def target():
            seen['had_ctx'] = flask.has_app_context()
            appmod._retention_sweeper_loop()

        t = threading.Thread(target=target, name='streakfit-retention-test',
                             daemon=True)
        t.start()
        t.join(60)
        assert not t.is_alive(), 'loop did not stop: something swallowed the sentinel'
        assert seen['had_ctx'] is False, 'the bare thread must not inherit an app context'
        return {'exc': seen['exc'], 'passes': clock.done, 'calls': calls}

    return run


def survived(r, passes):
    """The thread ended ONLY because the sentinel ended it, after `passes`."""
    return r['exc'] == [_Stop] and r['passes'] == passes


def ran_every_step_on(r, pass_no):
    return all(pass_no in r['calls'][s] for s in STEPS)


def checks(client):
    return {c['id']: c for c in client.get('/api/verification/self').get_json()['checks']}


def rows(kind):
    return db.session.execute(
        db.select(RetentionRun).where(RetentionRun.kind == kind)
        .order_by(RetentionRun.id)).scalars().all()


def delivery_rows():
    return db.session.execute(
        db.select(NotificationRun).order_by(NotificationRun.id)).scalars().all()


# ── SURVIVAL ────────────────────────────────────────────────────────────────

def test_a_clean_loop_runs_every_step_every_pass(loop):
    r = loop(3)
    assert survived(r, 3)
    assert all(ran_every_step_on(r, n) for n in (1, 2, 3))


@pytest.mark.parametrize('step', STEPS)
def test_a_failure_in_any_step_never_ends_the_thread(loop, step):
    r = loop(3, faults={step: {1}})
    assert survived(r, 3), r['exc']
    assert ran_every_step_on(r, 2) and ran_every_step_on(r, 3)


@pytest.mark.parametrize('step', STEPS)
def test_a_step_failing_every_pass_never_ends_the_thread(loop, step):
    r = loop(4, faults={step: {1, 2, 3, 4}})
    assert survived(r, 4), r['exc']
    assert all(ran_every_step_on(r, n) for n in (1, 2, 3, 4))


def test_every_step_failing_at_once_never_ends_the_thread(loop):
    r = loop(2, faults={s: {1} for s in STEPS})
    assert survived(r, 2), r['exc']
    assert ran_every_step_on(r, 2)


def test_failure_recording_that_itself_fails_never_ends_the_thread(loop, monkeypatch):
    def boom(*a, **kw):
        raise RuntimeError('recording failed too')
    monkeypatch.setattr(appmod, '_record_retention_failure', boom)
    if hasattr(appmod, '_record_notification_failure'):
        monkeypatch.setattr(appmod, '_record_notification_failure', boom)
    r = loop(2, faults={s: {1} for s in STEPS})
    assert survived(r, 2), r['exc']
    assert ran_every_step_on(r, 2)


def test_logging_that_raises_inside_a_handler_never_ends_the_thread(loop, monkeypatch):
    def boom(*a, **kw):
        raise RuntimeError('logging broke')
    for level in ('warning', 'error', 'info'):
        monkeypatch.setattr(appmod.app.logger, level, boom)
    r = loop(2, faults={s: {1} for s in STEPS})
    assert survived(r, 2), r['exc']
    assert ran_every_step_on(r, 2)


@pytest.mark.parametrize('phase', ['enter', 'exit'])
@pytest.mark.parametrize('nth', range(1, 9))
def test_an_app_context_failing_anywhere_never_ends_the_thread(loop, monkeypatch, phase, nth):
    """Every app_context() the loop opens -- steps AND failure handlers -- is
    made to fail in turn, on entry or on teardown, during pass 1 (with every
    step also failing, so the failure handlers' contexts are exercised)."""
    real_ctx = appmod.app.app_context
    count = {'n': 0}

    @contextmanager
    def faulty():
        count['n'] += 1
        mine = count['n']
        if phase == 'enter' and mine == nth:
            raise RuntimeError(f'context {mine} failed on entry')
        with real_ctx():
            yield
        if phase == 'exit' and mine == nth:
            raise RuntimeError(f'context {mine} failed on teardown')

    monkeypatch.setattr(appmod.app, 'app_context', faulty)
    r = loop(2, faults={s: {1} for s in STEPS})
    assert survived(r, 2), r['exc']
    assert ran_every_step_on(r, 2)


# ── MONITORING ──────────────────────────────────────────────────────────────

def test_a_coach_failure_is_recorded_like_a_moderation_failure(loop, client):
    loop(1, faults={'_sweep_expired_coach_turns': {1}})
    assert rows(RETENTION_COACH)[-1].outcome == 'failed'
    assert rows(RETENTION_COACH)[-1].error_type == 'RuntimeError'
    assert checks(client)['retention.recent']['status'] == 'FAIL'


def test_a_notice_generation_failure_is_not_masked_by_a_good_delivery_pass(loop, client):
    r = loop(1, faults={'_generate_moderation_notices': {1}})
    assert survived(r, 1)
    assert delivery_rows()[-1].outcome == 'ok'          # delivery really did run
    c = checks(client)
    assert c['moderation.delivery_worker']['status'] == 'PASS'   # the worker is alive...
    assert c['moderation.notice_generation']['status'] == 'FAIL'  # ...and that is not enough
    assert c['moderation.notices_delivered']['status'] != 'PASS'


def test_a_healthy_pass_passes_every_worker_check(loop, client):
    loop(1)
    c = checks(client)
    for check_id in ('retention.recent', 'retention.moderation',
                     'moderation.notice_generation', 'moderation.delivery_worker',
                     'moderation.notices_delivered'):
        assert c[check_id]['status'] == 'PASS', (check_id, c[check_id])


def test_a_delivery_failure_is_recorded_and_fails_the_worker_check(loop, client):
    loop(1, faults={'_deliver_pending_notices': {1}})
    last = delivery_rows()[-1]
    assert last.outcome == 'failed' and last.error_type == 'RuntimeError'
    c = checks(client)
    assert c['moderation.delivery_worker']['status'] == 'FAIL'
    assert c['moderation.notices_delivered']['status'] != 'PASS'


@pytest.mark.parametrize('step,check_id', [
    ('_sweep_expired_coach_turns', 'retention.recent'),
    ('_sweep_moderation_evidence', 'retention.moderation'),
    ('_generate_moderation_notices', 'moderation.notice_generation'),
    ('_deliver_pending_notices', 'moderation.delivery_worker'),
])
def test_repeated_failures_stay_failed_however_long_the_thread_lives(loop, client, step, check_id):
    r = loop(4, faults={step: {1, 2, 3, 4}})
    assert survived(r, 4)
    assert checks(client)[check_id]['status'] == 'FAIL'


@pytest.mark.parametrize('step,check_id', [
    ('_sweep_expired_coach_turns', 'retention.recent'),
    ('_sweep_moderation_evidence', 'retention.moderation'),
    ('_generate_moderation_notices', 'moderation.notice_generation'),
    ('_deliver_pending_notices', 'moderation.delivery_worker'),
])
def test_a_later_success_of_the_same_step_clears_it(loop, client, step, check_id):
    r = loop(3, faults={step: {1, 2}})
    assert survived(r, 3)
    assert checks(client)[check_id]['status'] == 'PASS'


@pytest.mark.parametrize('failing,other_checks', [
    ('_sweep_expired_coach_turns', ['retention.moderation', 'moderation.notice_generation',
                                    'moderation.delivery_worker']),
    ('_sweep_moderation_evidence', ['retention.recent', 'moderation.notice_generation',
                                    'moderation.delivery_worker']),
    ('_generate_moderation_notices', ['retention.recent', 'retention.moderation',
                                      'moderation.delivery_worker']),
    ('_deliver_pending_notices', ['retention.recent', 'retention.moderation',
                                  'moderation.notice_generation']),
])
def test_success_elsewhere_never_erases_an_unresolved_failure(loop, client, failing, other_checks):
    """The failing step fails on its LAST pass, after earlier successes, while
    every other step succeeds on every pass, including after it. Its own check
    must say FAIL; the others' successes must neither hide it nor be dragged
    down by it."""
    r = loop(3, faults={failing: {3}})
    assert survived(r, 3)
    c = checks(client)
    own = {'_sweep_expired_coach_turns': 'retention.recent',
           '_sweep_moderation_evidence': 'retention.moderation',
           '_generate_moderation_notices': 'moderation.notice_generation',
           '_deliver_pending_notices': 'moderation.delivery_worker'}[failing]
    assert c[own]['status'] == 'FAIL', c[own]
    for other in other_checks:
        assert c[other]['status'] == 'PASS', (other, c[other])


def test_a_failure_record_never_carries_the_exception_text(loop, client):
    loop(1, faults={s: {1} for s in STEPS})
    for row in rows(RETENTION_COACH) + rows(RETENTION_MODERATION):
        assert 'injected' not in (row.error_type or '') + (row.detail or '')
    for row in delivery_rows():
        assert 'injected' not in (row.error_type or '')
    assert 'injected' not in str(client.get('/api/verification/self').get_json())


# ── MONITORING: interleavings (D42 adversarial review, MAJOR 1 / MINOR 2-3) ──
#
# The resilience tests above never let anything else write between two
# unattended failures. Real life does: an operator sees FAIL and runs the
# command by hand; a user's coach request piggy-backs a sweep.

def _run(kind, source, outcome, minutes_ago=0):
    from datetime import datetime, timedelta
    db.session.add(RetentionRun(
        ran_at=datetime.utcnow() - timedelta(minutes=minutes_ago), deleted=0,
        source=source, kind=kind, outcome=outcome,
        error_type=None if outcome == 'ok' else 'OperationalError'))
    db.session.commit()


@pytest.mark.parametrize('kind,check_id,other_source', [
    (appmod.RUN_NOTICE_GENERATION, 'moderation.notice_generation', 'manual'),
    (RETENTION_COACH, 'retention.recent', 'manual'),
    (RETENTION_COACH, 'retention.recent', 'request'),
    (RETENTION_MODERATION, 'retention.moderation', 'manual'),
])
def test_an_attended_success_never_clears_an_unattended_failure(client, monkeypatch, kind, check_id, other_source):
    monkeypatch.setattr(appmod, '_WORKER_STARTED_AT', None)
    _run(kind, 'thread', 'ok', minutes_ago=120)
    _run(kind, 'thread', 'failed', minutes_ago=10)
    _run(kind, other_source, 'ok', minutes_ago=1)       # "it's red, I ran it by hand"
    c = checks(client)[check_id]
    assert c['status'] == 'FAIL', c
    assert 'does not clear' in c['observed']


def test_a_hand_run_of_the_real_command_does_not_turn_a_generation_failure_green(client, monkeypatch):
    monkeypatch.setattr(appmod, '_WORKER_STARTED_AT', None)
    monkeypatch.setattr(appmod, '_notification_channel', lambda name=None: Works())
    _run(appmod.RUN_NOTICE_GENERATION, 'thread', 'failed', minutes_ago=10)
    result = appmod.app.test_cli_runner().invoke(args=['moderation-notify'])
    assert result.exit_code == 0, result.output
    c = checks(client)
    assert c['moderation.notice_generation']['status'] == 'FAIL'
    assert c['moderation.notices_delivered']['status'] != 'PASS'


def test_the_next_unattended_success_does_clear_it(client, monkeypatch):
    monkeypatch.setattr(appmod, '_WORKER_STARTED_AT', None)
    _run(appmod.RUN_NOTICE_GENERATION, 'thread', 'failed', minutes_ago=70)
    _run(appmod.RUN_NOTICE_GENERATION, 'manual', 'ok', minutes_ago=30)
    _run(appmod.RUN_NOTICE_GENERATION, 'thread', 'ok', minutes_ago=5)
    assert checks(client)['moderation.notice_generation']['status'] == 'PASS'


def test_a_failed_delivery_pass_is_fail_even_while_the_worker_is_starting(client, monkeypatch):
    from datetime import datetime
    monkeypatch.setattr(appmod, '_notification_channel', lambda name=None: Works())
    monkeypatch.setattr(appmod, '_WORKER_STARTED_AT', datetime.utcnow())
    with appmod.app.app_context():
        appmod._record_notification_failure('thread', RuntimeError('x'))
    assert checks(client)['moderation.delivery_worker']['status'] == 'FAIL'


def test_the_generation_check_survives_a_failure_in_the_delivery_checks(client, monkeypatch):
    def boom(*a, **kw):
        raise RuntimeError('capability unreadable')
    monkeypatch.setattr(appmod, '_delivery_capability', boom)
    _run(appmod.RUN_NOTICE_GENERATION, 'thread', 'ok', minutes_ago=5)
    c = checks(client)
    assert c['moderation.notice_generation']['status'] == 'PASS'
