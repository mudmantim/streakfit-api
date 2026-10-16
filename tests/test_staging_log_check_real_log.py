"""(v3, 2026-10-16) The checker against the REAL log of the first RC-B rehearsal deploy.

tests/fixtures/staging_rehearsal_real/2026-10-16_step5_direct.log is a byte-for-byte
copy of the owner's raw Render download for deploy dep-db8vk1qvcj2c73bcptn0
(streakfit-staging-rcb, commit 8c68735, staging endpoint, direct string), sha256
856aace4cc8b1d09e9df8d7433408f9956363b6e22438d62f81614f83534aa8e. It contains no
credential (the checker's NO-CREDENTIAL passes on it, and this test asserts so).

v2 failed it on RUN-HEAD-FLAG because the runtime probe runs before the inline
STREAKFIT_ENFORCE_DB_HEAD=1 on gunicorn. These tests pin that the correctly
configured deploy passes, and that the same real log fails whenever the serving
process's enforced head check is missing, failed, or names the wrong revision,
or a retention sweeper ran.
"""
import hashlib
import importlib.util
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]
REAL = ROOT / 'tests' / 'fixtures' / 'staging_rehearsal_real' / '2026-10-16_step5_direct.log'
REAL_SHA256 = '856aace4cc8b1d09e9df8d7433408f9956363b6e22438d62f81614f83534aa8e'

_spec = importlib.util.spec_from_file_location('staging_log_check_real', ROOT / 'scripts' / 'staging_log_check.py')
chk = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(chk)

ARGS = SimpleNamespace(endpoint='ep-young-sound-b4qsoqe8', head='d4f1b6e2a9c3', service='srv-db8bna2j9qps73ccc3e0',
                       commit='8c687350d70d837264ec8ffc87940c50255c21b9', branch='rc/safety-b')
HEAD = 'd4f1b6e2a9c3'


def real():
    return REAL.read_text(encoding='utf-8').splitlines()


def fails(lines):
    return set(chk.evaluate('deploy', lines, ARGS).failed_ids())


def test_the_fixture_is_the_unmodified_real_log():
    assert hashlib.sha256(REAL.read_bytes()).hexdigest() == REAL_SHA256


def test_the_real_probe_shows_the_inline_flags_as_missing():
    """Why v2 failed: the probe cannot see what the Start Command sets on gunicorn."""
    lines = real()
    assert any(ln.endswith('STREAKFIT_ENFORCE_DB_HEAD: MISSING') for ln in lines)
    assert any(ln.endswith(f'Database migration check passed (at head {HEAD}).') for ln in lines)


def test_the_real_step5_log_passes_every_finding():
    rep = chk.evaluate('deploy', real(), ARGS)
    assert rep.ok, rep.failed_ids()
    ids = {r[0] for r in rep.rows}
    assert {'RUN-HEAD-FLAG', 'RUN-NO-SWEEPER', 'HEAD-ONE', 'HEAD-REV', 'NO-CREDENTIAL', 'PRE-SID',
            'MIG-SERVER', 'ID-STAGING', 'CHECKOUT'} <= ids


def _drop_head(lines):
    return [ln for ln in lines if 'Database migration check passed' not in ln]


def _replace_head(lines, new):
    return [new if 'Database migration check passed' in ln else ln for ln in lines]


TS = '2026-10-16T10:20:05.789000000Z '
REAL_MUTATIONS = {
    'head check absent (probe MISSING alone is not proof)': (_drop_head, {'RUN-HEAD-FLAG', 'HEAD-ONE'}),
    'head check names the wrong revision': (
        lambda g: [ln.replace(f'at head {HEAD}', 'at head c7d2a9e41b30') for ln in g], {'RUN-HEAD-FLAG', 'HEAD-REV'}),
    'head check without a revision': (
        lambda g: _replace_head(g, TS + '[2026-10-16 10:20:05,789] INFO in app: Database migration check passed'),
        {'RUN-HEAD-FLAG', 'HEAD-REV'}),
    'head check failed (database behind head)': (
        lambda g: _replace_head(g, TS + "[2026-10-16 10:20:05,789] CRITICAL in app: Refusing to start: database is "
                                "at Alembic revision 'c7d2a9e41b30' but the code expects head 'd4f1b6e2a9c3'. "
                                "Run `flask db upgrade`."), {'RUN-HEAD-FLAG', 'HEAD-ONE'}),
    'head check failed (state unverifiable)': (
        lambda g: _replace_head(g, TS + '[2026-10-16 10:20:05,789] CRITICAL in app: Refusing to start: could not '
                                'verify database migration state (database unreachable?): OperationalError'),
        {'RUN-HEAD-FLAG', 'HEAD-ONE'}),
    'head check present but a second process refused': (
        lambda g: g + [TS + "CRITICAL in app: Refusing to start: database is at Alembic revision 'c7d2a9e41b30' "
                       "but the code expects head 'd4f1b6e2a9c3'. Run `flask db upgrade`."], {'RUN-HEAD-FLAG'}),
    'probe shows the head flag with a wrong value': (
        lambda g: [ln.replace('STREAKFIT_ENFORCE_DB_HEAD: MISSING', 'STREAKFIT_ENFORCE_DB_HEAD=0') for ln in g],
        {'RUN-HEAD-FLAG'}),
    'retention sweeper started (inline flag, probe MISSING)': (
        lambda g: g + [TS + '[2026-10-16 10:20:05,800] INFO in app: event=retention_sweeper_started interval_s=3600'],
        {'RUN-NO-SWEEPER'}),
}


@pytest.mark.parametrize('name', REAL_MUTATIONS)
def test_the_real_log_fails_when_the_enforced_head_check_did_not_succeed(name):
    mutate, expected = REAL_MUTATIONS[name]
    got = fails(mutate(real()))
    assert expected <= got, (name, sorted(got))
