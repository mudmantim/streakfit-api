import logging
import os
from logging.config import fileConfig

from flask import current_app
from sqlalchemy import create_engine
from sqlalchemy.pool import NullPool

from alembic import context

import db_guard

config = context.config
fileConfig(config.config_file_name)
logger = logging.getLogger('alembic.env')


# The migration credential (db_guard, incident 2026-10-13 follow-up B-2).
# When MIGRATION_DATABASE_URL is set, migrations connect with it and the app's
# DATABASE_URL can be a least-privilege role that cannot run DDL. It passes the
# same db_guard rules as the app's URL, and the server is re-checked (R7) on the
# connection the migration actually uses. Unset: the app's engine, as before.
MIGRATION_BINDING = (db_guard.resolve(url_var='MIGRATION_DATABASE_URL')
                     if os.environ.get('MIGRATION_DATABASE_URL') else None)
_migration_engine = (create_engine(MIGRATION_BINDING.url, poolclass=NullPool)
                     if MIGRATION_BINDING else None)


def _binding():
    if MIGRATION_BINDING is not None:
        return MIGRATION_BINDING
    import app as app_module   # already imported by the flask CLI
    return app_module.DB_BINDING


def get_engine():
    if _migration_engine is not None:
        return _migration_engine
    try:
        return current_app.extensions['migrate'].db.get_engine()
    except (TypeError, AttributeError):
        return current_app.extensions['migrate'].db.engine


def get_engine_url():
    try:
        return get_engine().url.render_as_string(hide_password=False).replace('%', '%%')
    except AttributeError:
        return str(get_engine().url).replace('%', '%%')


config.set_main_option('sqlalchemy.url', get_engine_url())
target_db = current_app.extensions['migrate'].db


def get_metadata():
    if hasattr(target_db, 'metadatas'):
        return target_db.metadatas[None]
    return target_db.metadata


def run_migrations_offline():
    url = config.get_main_option('sqlalchemy.url')
    context.configure(url=url, target_metadata=get_metadata(), literal_binds=True)
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online():
    def process_revision_directives(context, revision, directives):
        if getattr(config, 'cmd_opts', None) and config.cmd_opts.autogenerate:
            script = directives[0]
            if script.upgrade_ops.is_empty():
                directives[:] = []
                logger.info('No changes in schema detected.')

    conf_args = current_app.extensions['migrate'].configure_args
    if conf_args.get('process_revision_directives') is None:
        conf_args['process_revision_directives'] = process_revision_directives

    connectable = get_engine()
    with connectable.connect() as connection:
        binding = _binding()
        reported = db_guard.verify_server(connection, binding)
        # The check's query auto-began a transaction (SQLAlchemy 2.0). End it
        # here: if it were still open, Alembic would treat it as the caller's,
        # never commit, and every migration would log "Running upgrade" and
        # then roll back when the connection closed. Caught on PostgreSQL by
        # tests/test_db_roles_kit_postgres.py; SQLite never runs the query.
        connection.commit()
        logger.info('Migrating database: %s (server endpoint %s)',
                    binding.describe(), reported or 'not reported')
        context.configure(
            connection=connection,
            target_metadata=get_metadata(),
            **conf_args
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
