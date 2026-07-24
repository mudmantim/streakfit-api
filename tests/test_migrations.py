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
