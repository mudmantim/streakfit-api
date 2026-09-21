"""Migration-chain integrity — the guardrail for schema/migration drift.

A database built entirely from the Alembic chain (`flask db upgrade` against an
empty DB) MUST match the application's model schema. This test exists because
that guarantee silently failed for a long time: the baseline shipped as a no-op
and nothing in the chain ever created the `user` or `challenge` tables, so a
fresh database could not be built from migrations at all — and CI never noticed
because the rest of the suite builds its test DB with `create_all()`, bypassing
Alembic entirely.

Part of the StreakFit Verification Standard (see CLAUDE.md): no change that
touches the schema is complete until this test passes.

Server-side defaults are intentionally NOT compared: migrations add
`server_default`s to backfill existing rows when adding NOT NULL columns, while
the models rely on Python-side defaults. That difference is expected and
harmless. Structure — tables, columns (name/type/nullable), primary keys,
foreign keys, unique constraints, and indexes — is what must match.
"""
import json
import os
import sys
import tempfile
import subprocess

from sqlalchemy import create_engine, inspect

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _introspect(url):
    insp = inspect(create_engine(url))
    schema = {}
    for table in insp.get_table_names():
        if table == 'alembic_version':
            continue
        schema[table] = {
            'columns': {c['name']: (str(c['type']), bool(c['nullable']))
                        for c in insp.get_columns(table)},
            'pk': tuple(insp.get_pk_constraint(table).get('constrained_columns') or []),
            'fks': {(tuple(f['constrained_columns']), f['referred_table'],
                     tuple(f['referred_columns'])) for f in insp.get_foreign_keys(table)},
            'unique': {frozenset(u['column_names'])
                       for u in insp.get_unique_constraints(table)},
            'indexes': {(frozenset(i['column_names']), bool(i.get('unique')))
                        for i in insp.get_indexes(table)},
        }
    return schema


def _build_from_migrations():
    """Empty DB -> `flask db upgrade` -> path. Fails loudly if the chain can't
    bootstrap a fresh database."""
    path = tempfile.mktemp(suffix='.db')
    env = {**os.environ,
           'DATABASE_URL': f'sqlite:///{path}',
           'FLASK_APP': 'app',
           'SECRET_KEY': 'test', 'JWT_SECRET_KEY': 'test'}
    env.pop('STREAKFIT_ENFORCE_DB_HEAD', None)  # the boot guard must not fire mid-upgrade
    result = subprocess.run(
        [sys.executable, '-m', 'flask', 'db', 'upgrade'],
        cwd=REPO, env=env, capture_output=True, text=True)
    assert result.returncode == 0, (
        "`flask db upgrade` could not build a database from empty — the migration "
        "chain cannot bootstrap a fresh DB.\n\nSTDERR:\n" + result.stderr[-3000:])
    return path


def _build_from_models():
    from app import db
    path = tempfile.mktemp(suffix='.db')
    db.metadata.create_all(create_engine(f'sqlite:///{path}'))
    return path


def test_migration_chain_builds_from_empty_and_matches_models():
    migrated = _introspect(f'sqlite:///{_build_from_migrations()}')
    model = _introspect(f'sqlite:///{_build_from_models()}')

    assert set(migrated) == set(model), (
        "Tables built from migrations differ from the models — "
        f"migrated-only={set(migrated) - set(model)}, "
        f"model-only={set(model) - set(migrated)}")

    mismatches = []
    for table in sorted(migrated):
        for facet in ('columns', 'pk', 'fks', 'unique', 'indexes'):
            if migrated[table][facet] != model[table][facet]:
                mismatches.append(
                    f"  {table}.{facet}:\n"
                    f"    from migrations = {migrated[table][facet]}\n"
                    f"    from models     = {model[table][facet]}")
    assert not mismatches, (
        "Schema built from the migration chain does not match the models "
        "(the migrations and the models have drifted apart):\n"
        + "\n".join(mismatches))


def test_self_check_reports_schema_currency_only_when_it_really_knows():
    """The schema checks must PASS on a migrated database and never claim
    currency on one that cannot prove it.

    Mudman Command's roll-up turns UNKNOWN into "investigate", so this is
    load-bearing for qualification. It is also easy to misread: a database
    built with `create_all()` — which every other test here uses — carries no
    Alembic stamp, so "not at head" is the CORRECT answer there, not a defect.
    Both halves are pinned so nobody "fixes" the honest half.

    The helper is `_read_migration_state`, the DEPLOYED one, kept in the merge
    because it separates two facts this test needs kept apart: `state` says
    whether the revision could be READ at all, and `atHead` carries the
    comparison. The retired `_read_migration_state` collapsed them into `state`,
    so an unreachable database and a database one migration behind were
    reported identically.
    """
    migrated = _build_from_migrations()
    probe = (
        "import json, sys; sys.path.insert(0, %r);\n"
        "from app import app, _read_migration_state\n"
        "with app.app_context(): print(json.dumps(_read_migration_state()))\n" % str(REPO)
    )
    env = {**os.environ, 'DATABASE_URL': f'sqlite:///{migrated}',
           'SECRET_KEY': 'test', 'JWT_SECRET_KEY': 'test'}
    env.pop('STREAKFIT_ENFORCE_DB_HEAD', None)
    out = subprocess.run([sys.executable, '-c', probe],
                         cwd=REPO, env=env, capture_output=True, text=True)
    assert out.returncode == 0, out.stderr[-2000:]
    state = json.loads(out.stdout.strip().splitlines()[-1])
    assert state['state'] == 'ok', (
        "a database built by `flask db upgrade` reported its schema currency as "
        f"{state['state']!r} — qualification would read that as 'investigate'")
    assert state['atHead'] is True, (
        "a database built by `flask db upgrade` is at head by construction; "
        f"reported atHead={state['atHead']!r}")
    assert state['latest'], "stamped revision not reported"
    assert state['appliedCount'] and state['appliedCount'] > 0

    # And the honest half: an unstamped database must NOT claim to be at head.
    unstamped = tempfile.mktemp(suffix='.db')
    env2 = {**env, 'DATABASE_URL': f'sqlite:///{unstamped}'}
    probe2 = (
        "import json, sys; sys.path.insert(0, %r);\n"
        "from app import app, db, _read_migration_state\n"
        "with app.app_context():\n"
        "    db.create_all()\n"
        "    print(json.dumps(_read_migration_state()))\n" % str(REPO)
    )
    out2 = subprocess.run([sys.executable, '-c', probe2],
                          cwd=REPO, env=env2, capture_output=True, text=True)
    assert out2.returncode == 0, out2.stderr[-2000:]
    unstamped_state = json.loads(out2.stdout.strip().splitlines()[-1])
    # NOT AT HEAD is the assertion, not "unreadable". `create_all()` leaves a
    # perfectly readable database with no `alembic_version` row: the revision
    # reads as None, which is a fact we established rather than one we failed
    # to look up. What must never happen is it claiming currency.
    assert unstamped_state['atHead'] is not True, (
        "a create_all() database claimed its schema was at head; it has no "
        "Alembic stamp and cannot know that")
    assert not unstamped_state['latest'], (
        f"an unstamped database reported a revision: {unstamped_state['latest']!r}")
