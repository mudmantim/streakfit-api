"""A verification run orphaned by a restart is reported as interrupted (K6).

The admin "Verify Application" button writes a VerificationRun row as
'running' and finishes it from a daemon thread. A deploy or crash mid-run left
the row 'running' forever: Project Status stuck on yellow and History showing a
run that would never end. Past VERIFICATION_RUN_MAX_AGE, a row no thread in
this process owns now reads 'interrupted'. The stored row is never rewritten.
"""
from datetime import datetime, timedelta

import pytest

import app as A
from app import VerificationRun, db

ADMIN = {'X-Admin-Secret': 's3cret-value'}


@pytest.fixture(autouse=True)
def admin_env(monkeypatch):
    monkeypatch.setenv('ADMIN_SECRET', 's3cret-value')
    monkeypatch.setitem(A._verification_state, 'running', False)
    monkeypatch.setitem(A._verification_state, 'run_id', None)


def _run(age, status='running'):
    row = VerificationRun(suite_version=1, status=status, total=0, passed=0, failed=0,
                          started_at=datetime.utcnow() - age)
    db.session.add(row)
    db.session.commit()
    return row


def _statuses(client):
    status = client.get('/api/admin/verify/status', headers=ADMIN).get_json()['status']
    project = client.get('/api/admin/project-status', headers=ADMIN).get_json()
    history = client.get('/api/admin/verify/history', headers=ADMIN).get_json()['runs'][0]['status']
    return status, project['last_verification']['status'], history, project['overall_health']


def test_an_orphaned_run_reads_interrupted_everywhere(client):
    row = _run(timedelta(hours=3))
    status, last, history, _overall = _statuses(client)
    assert status == last == history == 'interrupted'
    db.session.refresh(row)
    assert row.status == 'running'          # display-time only: history is not rewritten


def test_an_interrupted_run_keeps_project_status_yellow_not_green(client):
    _run(timedelta(hours=3))
    assert _statuses(client)[3] == 'yellow'


def test_a_young_run_is_still_running(client):
    _run(timedelta(minutes=5))
    assert _statuses(client)[:3] == ('running', 'running', 'running')


def test_a_long_run_this_process_owns_is_still_running(client, monkeypatch):
    row = _run(timedelta(hours=3))
    monkeypatch.setitem(A._verification_state, 'running', True)
    monkeypatch.setitem(A._verification_state, 'run_id', row.id)
    assert client.get('/api/admin/verify/status', headers=ADMIN).get_json()['status'] == 'running'


@pytest.mark.parametrize('final', ['passed', 'failed', 'error'])
def test_finished_runs_are_reported_as_they_finished(client, final):
    _run(timedelta(days=2), status=final)
    assert _statuses(client)[:3] == (final, final, final)
