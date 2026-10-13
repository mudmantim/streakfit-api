"""PW-1 on PostgreSQL: the token-version migration, its rollback, and races.

Opt-in like the other PostgreSQL modules: STREAKFIT_TEST_POSTGRES_URL names a
disposable server. This module creates its own throwaway databases next to
whatever that URL names, and drops them afterwards.

Matrix rows T11, T12 and T15 of docs/architecture/password-implementation-checklist.md,
plus the rollback rehearsal from docs/architecture/pw1-implementation-plan.md §6.
"""
import json
import os
import subprocess
import sys
import time
import uuid

import pytest
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.engine import make_url

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PG_URL = os.environ.get('STREAKFIT_TEST_POSTGRES_URL')
pytestmark = pytest.mark.skipif(not PG_URL, reason='STREAKFIT_TEST_POSTGRES_URL not set')

PREVIOUS_HEAD = 'd4f1b6e2a9c3'
THIS_HEAD = 'e8b3c1f0a7d2'


@pytest.fixture
def scratch_db():
    server = make_url(PG_URL)
    name = f'sf_pw1_{uuid.uuid4().hex[:10]}'
    admin = create_engine(server.set(database='postgres'), isolation_level='AUTOCOMMIT')
    with admin.connect() as c:
        c.execute(text(f'CREATE DATABASE "{name}"'))
    url = server.set(database=name).render_as_string(hide_password=False)
    try:
        yield url
    finally:
        with admin.connect() as c:
            c.execute(text(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))
        admin.dispose()


def _env(url):
    env = {**os.environ, 'DATABASE_URL': url, 'FLASK_APP': 'app',
           'SECRET_KEY': 'test', 'JWT_SECRET_KEY': 'test', 'PYTHONPATH': REPO}
    env.pop('STREAKFIT_ENFORCE_DB_HEAD', None)
    env.pop('STREAKFIT_RETENTION_SWEEPER', None)
    return env


def _flask(url, *args, timeout=300):
    return subprocess.run([sys.executable, '-m', 'flask', 'db', *args],
                          cwd=REPO, env=_env(url), capture_output=True, text=True,
                          timeout=timeout)


def _user_columns(url):
    eng = create_engine(url)
    try:
        return {c['name']: c for c in inspect(eng).get_columns('user')}
    finally:
        eng.dispose()


def _revision(url):
    eng = create_engine(url)
    try:
        with eng.connect() as c:
            return c.execute(text('SELECT version_num FROM alembic_version')).scalar()
    finally:
        eng.dispose()


def _seed_users(url, n=3):
    """Rows as the PREVIOUS build writes them: no token_version column named."""
    eng = create_engine(url)
    try:
        with eng.begin() as c:
            for i in range(n):
                c.execute(text(
                    'INSERT INTO "user" (username, password_hash, skill_level, display_mode, '
                    'rickie_mode, xp_total, acorns_total, acorns_spent, is_plus) '
                    "VALUES (:u, :h, 'beginner', 'game', 'full', 0, 0, 0, false)"),
                    {'u': f'pw1_existing_{i}', 'h': f'pbkdf2:sha256:600000$salt{i}$hash{i}'})
        with eng.connect() as c:
            return c.execute(text('SELECT id, username, password_hash FROM "user" ORDER BY id')).all()
    finally:
        eng.dispose()


def _users(url):
    eng = create_engine(url)
    try:
        with eng.connect() as c:
            return c.execute(text('SELECT id, username, password_hash FROM "user" ORDER BY id')).all()
    finally:
        eng.dispose()


def test_upgrade_keeps_existing_accounts_signed_in_and_downgrade_keeps_them(scratch_db):
    """T15 on PostgreSQL, with real rows in the table both ways."""
    assert _flask(scratch_db, 'upgrade', PREVIOUS_HEAD).returncode == 0
    before = _seed_users(scratch_db)
    assert 'token_version' not in _user_columns(scratch_db)

    up = _flask(scratch_db, 'upgrade')
    assert up.returncode == 0, up.stderr[-3000:]
    assert _revision(scratch_db) == THIS_HEAD
    cols = _user_columns(scratch_db)
    assert str(cols['token_version']['type']) == 'INTEGER'
    assert cols['token_version']['nullable'] is False
    assert '0' in str(cols['token_version']['default'])
    assert cols['password_changed_at']['nullable'] is True
    eng = create_engine(scratch_db)
    try:
        with eng.connect() as c:
            versions = c.execute(text('SELECT token_version, password_changed_at FROM "user"')).all()
    finally:
        eng.dispose()
    assert versions == [(0, None)] * len(before)
    assert _users(scratch_db) == before

    down = _flask(scratch_db, 'downgrade', PREVIOUS_HEAD)
    assert down.returncode == 0, down.stderr[-3000:]
    assert _revision(scratch_db) == PREVIOUS_HEAD
    cols = _user_columns(scratch_db)
    assert 'token_version' not in cols and 'password_changed_at' not in cols
    assert _users(scratch_db) == before          # no account, name or hash lost

    again = _flask(scratch_db, 'upgrade')
    assert again.returncode == 0, again.stderr[-3000:]
    assert _users(scratch_db) == before


def test_the_previous_build_can_still_write_users_at_the_new_head(scratch_db):
    """During the deploy the old build keeps serving against the new schema; a
    registration that names no token_version must get the server default."""
    assert _flask(scratch_db, 'upgrade').returncode == 0
    rows = _seed_users(scratch_db, n=1)
    eng = create_engine(scratch_db)
    try:
        with eng.connect() as c:
            assert c.execute(text('SELECT token_version FROM "user" WHERE id = :i'),
                             {'i': rows[0][0]}).scalar() == 0
    finally:
        eng.dispose()


def test_a_held_row_lock_makes_the_rollback_fail_fast_and_then_it_succeeds(scratch_db):
    """The rollback rehearsal. A request holding a user row (every login, every
    mission write) blocks the ALTER; without a lock timeout the downgrade -- and
    every request queued behind its ACCESS EXCLUSIVE request -- would wait
    without limit. With it, the downgrade stops in ~5 s and can simply be
    retried once the lock is gone."""
    assert _flask(scratch_db, 'upgrade').returncode == 0
    _seed_users(scratch_db, n=1)
    eng = create_engine(scratch_db)
    holder = eng.connect()
    tx = holder.begin()
    holder.execute(text('SELECT id FROM "user" LIMIT 1 FOR NO KEY UPDATE'))
    try:
        started = time.monotonic()
        blocked = _flask(scratch_db, 'downgrade', PREVIOUS_HEAD, timeout=120)
        elapsed = time.monotonic() - started
        assert blocked.returncode != 0
        assert 'lock timeout' in blocked.stderr.lower()
        assert elapsed < 60
        assert _revision(scratch_db) == THIS_HEAD          # nothing half-done
        assert 'token_version' in _user_columns(scratch_db)
    finally:
        tx.rollback()
        holder.close()
        eng.dispose()
    retried = _flask(scratch_db, 'downgrade', PREVIOUS_HEAD)
    assert retried.returncode == 0, retried.stderr[-3000:]
    assert _revision(scratch_db) == PREVIOUS_HEAD


def test_the_upgrade_also_fails_fast_behind_a_held_row_lock(scratch_db):
    assert _flask(scratch_db, 'upgrade', PREVIOUS_HEAD).returncode == 0
    _seed_users(scratch_db, n=1)
    eng = create_engine(scratch_db)
    holder = eng.connect()
    tx = holder.begin()
    holder.execute(text('SELECT id FROM "user" LIMIT 1 FOR NO KEY UPDATE'))
    try:
        blocked = _flask(scratch_db, 'upgrade', timeout=120)
        assert blocked.returncode != 0 and 'lock timeout' in blocked.stderr.lower()
        assert _revision(scratch_db) == PREVIOUS_HEAD
    finally:
        tx.rollback()
        holder.close()
        eng.dispose()


def test_the_head_check_refuses_a_build_that_does_not_know_the_revision(scratch_db):
    """Why rollback is downgrade-FIRST: the previous build's boot check compares
    the database's revision with its own head and exits on any difference.
    Reproduced with this build's check against a database stamped one ahead."""
    assert _flask(scratch_db, 'upgrade').returncode == 0
    eng = create_engine(scratch_db)
    try:
        with eng.begin() as c:
            c.execute(text("UPDATE alembic_version SET version_num = 'ffffffffffff'"))
    finally:
        eng.dispose()
    env = {**_env(scratch_db), 'STREAKFIT_ENFORCE_DB_HEAD': '1'}
    boot = subprocess.run([sys.executable, '-c', 'import app'], cwd=REPO, env=env,
                          capture_output=True, text=True, timeout=120)
    assert boot.returncode == 1
    assert 'Refusing to start' in boot.stderr


@pytest.fixture
def race_results(scratch_db):
    up = _flask(scratch_db, 'upgrade')
    assert up.returncode == 0, up.stderr[-3000:]
    run = subprocess.run([sys.executable, os.path.join('tests', 'password_change_race.py')],
                         cwd=REPO, env=_env(scratch_db), capture_output=True, text=True,
                         timeout=600)
    assert run.returncode == 0, run.stderr[-3000:]
    return json.loads(run.stdout.strip().splitlines()[-1])


def test_races(race_results):
    """T11 (change vs deletion), T12 (two changes), and other devices mid-change."""
    failed = {k: v['runs'] for k, v in race_results.items() if not v['ok']}
    assert set(race_results) == {'P1', 'P2', 'P3'}
    assert not failed, failed
