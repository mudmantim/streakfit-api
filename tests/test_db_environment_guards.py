"""db_guard: which database a process may use (incident 2026-10-13).

A staging web service was created with production's DATABASE_URL and ran its
retention sweeper against production for six hours. Nothing in the app could
tell. These tests pin the rules that now make that configuration -- and its
relatives -- refuse to start, and they replay the incident configuration
exactly.

Unit tests call db_guard.resolve() with an explicit environment. The app-level
tests run `import app` in a subprocess: the binding is decided at import, and
re-importing app inside this process corrupts other tests (see conftest).
"""
import logging
import os
import subprocess
import sys

import pytest

import db_guard as G

PROD_EP = 'ep-odd-star-ajye3lag'
STAGING_EP = 'ep-fake-staging-00000000'
PROD_SVC = 'srv-d8f1hh6rnols73ak3a10'
STAGING_SVC = 'srv-fakenonproduction0000'
PASSWORD = 'Sup3rS3cretPassw0rd'


def url(ep, pooler=False, extra=''):
    return f'postgresql://neondb_owner:{PASSWORD}@{ep}{"-pooler" if pooler else ""}.c-2.us-east-2.aws.neon.tech/neondb?sslmode=require{extra}'


def refused(env, rule):
    with pytest.raises(G.DatabaseBindingError) as exc:
        G.resolve(env)
    assert str(exc.value).startswith(rule), str(exc.value)
    assert PASSWORD not in str(exc.value)
    return str(exc.value)


# -- endpoint parsing --------------------------------------------------------

@pytest.mark.parametrize('u,expected', [
    (url(PROD_EP), PROD_EP),
    (url(PROD_EP, pooler=True), PROD_EP),
    ('postgres://u:p@EP-ODD-STAR-AJYE3LAG.us-east-2.aws.neon.tech/db', PROD_EP),
    ('postgresql://u:p@localhost:5432/db', None),
    ('postgresql://u:p@ep-odd-star-ajye3lag.neon.tech.evil.example/db', None),
    ('sqlite:///x.db', None),
])
def test_neon_endpoint_parsing(u, expected):
    assert G.neon_endpoint(u) == expected


# -- the incident, replayed --------------------------------------------------

def test_the_2026_10_13_incident_configuration_is_refused():
    """Exactly what staging had: Render markers, its own service ID, no
    declared deployment, production's URL."""
    msg = refused({'RENDER': 'true', 'RENDER_SERVICE_ID': STAGING_SVC, 'DATABASE_URL': url(PROD_EP),
                   'STREAKFIT_AGE_POLICY': 'off'}, 'R2')
    assert STAGING_SVC in msg and PROD_EP in msg


def test_the_incident_is_refused_even_if_staging_declares_production():
    refused({'RENDER': 'true', 'RENDER_SERVICE_ID': STAGING_SVC, 'STREAKFIT_DEPLOYMENT': 'production',
             'DATABASE_URL': url(PROD_EP)}, 'R2')


def test_the_incident_is_refused_through_the_pooler_too():
    refused({'RENDER': 'true', 'RENDER_SERVICE_ID': STAGING_SVC, 'DATABASE_URL': url(PROD_EP, pooler=True)}, 'R2')


def test_production_database_without_a_service_id_is_refused_when_hosted():
    """A hosted process that cannot prove it is the production service (e.g. a
    step where RENDER_SERVICE_ID is absent) does not get the production DB."""
    refused({'RENDER': 'true', 'DATABASE_URL': url(PROD_EP)}, 'R2')


# -- what production and staging look like when correct ----------------------

def test_production_as_configured_today_is_accepted():
    """The live service sets no STREAKFIT_DEPLOYMENT; it must keep working."""
    b = G.resolve({'RENDER': 'true', 'RENDER_SERVICE_ID': PROD_SVC, 'DATABASE_URL': url(PROD_EP)})
    assert (b.deployment, b.endpoint, b.hosted) == ('production', PROD_EP, True)
    assert PASSWORD not in b.describe()


def test_production_with_the_inline_head_flag_only_is_hosted():
    b = G.resolve({'STREAKFIT_ENFORCE_DB_HEAD': '1', 'RENDER_SERVICE_ID': PROD_SVC, 'DATABASE_URL': url(PROD_EP)})
    assert b.hosted


def test_correct_staging_is_accepted():
    b = G.resolve({'RENDER': 'true', 'RENDER_SERVICE_ID': STAGING_SVC, 'STREAKFIT_DEPLOYMENT': 'staging',
                   'DATABASE_URL': url(STAGING_EP)})
    assert (b.deployment, b.endpoint) == ('staging', STAGING_EP)


def test_staging_that_forgets_to_declare_itself_is_refused():
    """Undeclared on Render means production; a non-production service ID
    then refuses. Staging has to say what it is."""
    refused({'RENDER': 'true', 'RENDER_SERVICE_ID': STAGING_SVC, 'DATABASE_URL': url(STAGING_EP)}, 'R4')


# -- production on the wrong database ----------------------------------------

def test_production_on_a_staging_database_is_refused():
    refused({'RENDER': 'true', 'RENDER_SERVICE_ID': PROD_SVC, 'DATABASE_URL': url(STAGING_EP)}, 'R4')


def test_production_recovery_endpoint_must_be_named_exactly():
    env = {'RENDER': 'true', 'RENDER_SERVICE_ID': PROD_SVC, 'DATABASE_URL': url('ep-new-recovery-123')}
    refused(env, 'R4')
    refused({**env, G.RECOVERY_ENDPOINT_VAR: 'ep-something-else'}, 'R4')
    assert G.resolve({**env, G.RECOVERY_ENDPOINT_VAR: 'ep-new-recovery-123'}).endpoint == 'ep-new-recovery-123'


def test_the_recovery_override_cannot_open_production_to_staging():
    refused({'RENDER': 'true', 'RENDER_SERVICE_ID': STAGING_SVC, G.RECOVERY_ENDPOINT_VAR: PROD_EP,
             'DATABASE_URL': url(PROD_EP)}, 'R2')


# -- declared non-production -------------------------------------------------

@pytest.mark.parametrize('deployment', ['staging', 'development'])
def test_declared_non_production_never_uses_the_production_database(deployment):
    refused({'STREAKFIT_DEPLOYMENT': deployment, 'DATABASE_URL': url(PROD_EP),
             G.LOCAL_PRODUCTION_OVERRIDE_VAR: '1'}, 'R3')


def test_an_unknown_deployment_name_is_refused():
    refused({'STREAKFIT_DEPLOYMENT': 'prod', 'DATABASE_URL': url(STAGING_EP)}, f'{G.DEPLOYMENT_VAR}=')


# -- local machines and CI ----------------------------------------------------

def test_local_development_without_a_url_still_gets_sqlite():
    b = G.resolve({})
    assert (b.url, b.deployment, b.hosted) == (G.SQLITE_FALLBACK, 'development', False)


def test_a_local_postgres_is_fine():
    assert G.resolve({'DATABASE_URL': 'postgresql://u:p@localhost/db'}).endpoint is None


def test_a_laptop_on_the_production_database_must_say_so():
    refused({'DATABASE_URL': url(PROD_EP)}, 'R5')
    b = G.resolve({'DATABASE_URL': url(PROD_EP), G.LOCAL_PRODUCTION_OVERRIDE_VAR: '1'})
    assert b.endpoint == PROD_EP


# -- R1: no silent SQLite on a hosted service ----------------------------------

@pytest.mark.parametrize('marker', [{'RENDER': 'true'}, {'RENDER_SERVICE_ID': PROD_SVC},
                                    {'STREAKFIT_ENFORCE_DB_HEAD': '1'}])
def test_a_hosted_service_without_a_url_refuses_the_sqlite_fallback(marker):
    refused(marker, 'R1')
    refused({**marker, 'DATABASE_URL': '   '}, 'R1')


def test_an_explicit_sqlite_url_is_left_alone_even_when_hosted():
    assert G.resolve({'RENDER': 'true', 'DATABASE_URL': 'sqlite:///x.db'}).url == 'sqlite:///x.db'


# -- R6: rerouting -------------------------------------------------------------

@pytest.mark.parametrize('extra', ['&host=ep-odd-star-ajye3lag.c-2.us-east-2.aws.neon.tech',
                                   '&hostaddr=10.0.0.1', '&service=prod'])
def test_query_parameters_that_override_the_host_are_refused(extra):
    refused({'STREAKFIT_DEPLOYMENT': 'staging', 'DATABASE_URL': url(STAGING_EP, extra=extra)}, 'R6')


@pytest.mark.parametrize('var', ['PGHOSTADDR', 'PGSERVICE', 'PGSERVICEFILE'])
def test_libpq_environment_overrides_are_refused(var):
    refused({'DATABASE_URL': url(STAGING_EP), 'STREAKFIT_DEPLOYMENT': 'staging', var: 'x'}, 'R6')


def test_the_migration_url_is_checked_by_the_same_rules():
    env = {'RENDER': 'true', 'RENDER_SERVICE_ID': STAGING_SVC, 'STREAKFIT_DEPLOYMENT': 'staging',
           'DATABASE_URL': url(STAGING_EP), 'MIGRATION_DATABASE_URL': url(PROD_EP)}
    assert G.resolve(env).endpoint == STAGING_EP
    with pytest.raises(G.DatabaseBindingError, match='^R2'):
        G.resolve(env, url_var='MIGRATION_DATABASE_URL')


# -- R7: the server's own account ---------------------------------------------

class FakeConn:
    class dialect:
        name = 'postgresql'

    def __init__(self, reported):
        self.reported = reported

    def exec_driver_sql(self, sql):
        assert "neon.endpoint_id" in sql
        reported = self.reported

        class R:
            def scalar(self):
                return reported
        return R()


def test_server_reporting_the_named_endpoint_passes():
    b = G.resolve({'STREAKFIT_DEPLOYMENT': 'staging', 'DATABASE_URL': url(STAGING_EP)})
    assert G.verify_server(FakeConn(STAGING_EP), b) == STAGING_EP


def test_server_disagreeing_with_the_url_is_refused():
    b = G.resolve({'STREAKFIT_DEPLOYMENT': 'staging', 'DATABASE_URL': url(STAGING_EP)})
    with pytest.raises(G.DatabaseBindingError, match='^R7'):
        G.verify_server(FakeConn('ep-somewhere-else'), b)


def test_a_non_neon_url_that_lands_on_production_is_refused():
    """A custom domain or proxy in front of Neon: the URL names no endpoint,
    the server does -- and the policy runs on what the server says."""
    b = G.resolve({'STREAKFIT_DEPLOYMENT': 'staging', 'DATABASE_URL': 'postgresql://u:p@db.example.com/x'})
    with pytest.raises(G.DatabaseBindingError, match='^R3'):
        G.verify_server(FakeConn(PROD_EP), b)


def test_a_server_that_reports_nothing_is_not_held_against_it():
    b = G.resolve({'DATABASE_URL': 'postgresql://u:p@localhost/db'})
    assert G.verify_server(FakeConn(None), b) is None


def test_sqlite_connections_are_not_asked():
    class Lite:
        class dialect:
            name = 'sqlite'
    assert G.verify_server(Lite(), G.resolve({})) is None


# -- secret redaction ------------------------------------------------------------

def test_redact_masks_url_credentials_conninfo_bearer_and_known_values():
    text = (f'connect {url(PROD_EP)} redis://default:redispw123@host:6379 '
            f"host=x password='{PASSWORD}' Authorization: Bearer abcdefghijklmnop and raw sentinel-secret-1")
    out = G.redact(text, ('sentinel-secret-1',))
    for secret in (PASSWORD, 'redispw123', 'abcdefghijklmnop', 'sentinel-secret-1'):
        assert secret not in out
    assert 'neondb_owner:***@ep-odd-star-ajye3lag' in out     # who and where survive; only the secret goes


def test_secret_values_come_from_the_environment():
    values = G._secret_values({'SECRET_KEY': 'k' * 20, 'ADMIN_SECRET': 'short',
                               'MIGRATION_DATABASE_URL': 'postgresql://u:pw%40long123@h/db'})
    assert 'k' * 20 in values and 'short' not in values
    assert 'pw@long123' in values and 'pw%40long123' in values


def test_log_redaction_covers_every_logger(monkeypatch, caplog):
    monkeypatch.setattr(G, '_installed', False)
    previous = logging.getLogRecordFactory()
    try:
        G.install_log_redaction({'ADMIN_SECRET': 'admin-sentinel-value'})
        with caplog.at_level(logging.INFO):
            logging.getLogger('some.library').info('dsn=%s', url(STAGING_EP))
            logging.getLogger('another').warning('leaked admin-sentinel-value here')
        assert PASSWORD not in caplog.text and 'admin-sentinel-value' not in caplog.text
        assert 'neondb_owner:***@' in caplog.text
    finally:
        logging.setLogRecordFactory(previous)
        G._installed = False


# -- the app, end to end (subprocess: binding happens at import) ---------------

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BASE_ENV_DROP = ('RENDER', 'RENDER_SERVICE_ID', 'STREAKFIT_ENFORCE_DB_HEAD', 'STREAKFIT_RETENTION_SWEEPER',
                 'STREAKFIT_DEPLOYMENT', 'DATABASE_URL', 'MIGRATION_DATABASE_URL', G.LOCAL_PRODUCTION_OVERRIDE_VAR)


def run_import(extra_env, code='import app; print("IMPORTED", app.DB_BINDING.describe())'):
    env = {k: v for k, v in os.environ.items() if k not in BASE_ENV_DROP}
    env.update({'SECRET_KEY': 'x' * 32, 'JWT_SECRET_KEY': 'y' * 32, 'PYTHONDONTWRITEBYTECODE': '1'})
    env.update(extra_env)
    return subprocess.run([sys.executable, '-c', code], cwd=REPO, env=env,
                          capture_output=True, text=True, timeout=120)


def test_app_refuses_to_import_with_the_incident_configuration():
    p = run_import({'RENDER': 'true', 'RENDER_SERVICE_ID': STAGING_SVC, 'DATABASE_URL': url(PROD_EP)})
    assert p.returncode != 0
    assert 'Refusing to start: R2' in p.stderr
    assert PASSWORD not in p.stdout + p.stderr
    assert 'IMPORTED' not in p.stdout


def test_app_refuses_to_import_hosted_without_a_database_url():
    p = run_import({'RENDER': 'true'})
    assert p.returncode != 0 and 'R1' in p.stderr


def test_app_imports_normally_for_local_development(tmp_path):
    p = run_import({'DATABASE_URL': f'sqlite:///{tmp_path}/x.db'})
    assert p.returncode == 0, p.stderr[-1500:]
    assert 'IMPORTED deployment=development' in p.stdout


def test_hosted_sweeper_does_not_start_without_a_verified_database(tmp_path):
    """RENDER set, sweeper requested, but no boot check (ENFORCE_DB_HEAD off):
    nothing has proved which database this is, so nothing is deleted."""
    p = run_import({'RENDER': 'true', 'DATABASE_URL': f'sqlite:///{tmp_path}/x.db',
                    'STREAKFIT_RETENTION_SWEEPER': '1'},
                   code='import app; print("THREAD", app._WORKER_STARTED_AT)')
    assert p.returncode == 0, p.stderr[-1500:]
    assert 'retention_sweeper_refused' in p.stderr
    assert 'THREAD None' in p.stdout


def test_migrations_use_the_migration_url_when_set(tmp_path):
    """MIGRATION_DATABASE_URL takes the DDL; DATABASE_URL is left untouched."""
    app_db, mig_db = tmp_path / 'app.db', tmp_path / 'mig.db'
    p = run_import({'DATABASE_URL': f'sqlite:///{app_db}', 'MIGRATION_DATABASE_URL': f'sqlite:///{mig_db}',
                    'FLASK_APP': 'app'},
                   code='import sys; from flask.cli import main; sys.argv=["flask","db","upgrade"]; main()')
    assert p.returncode == 0, p.stderr[-2000:]
    import sqlite3
    tables = {r[0] for r in sqlite3.connect(mig_db).execute("select name from sqlite_master where type='table'")}
    assert 'alembic_version' in tables and 'user' in tables
    app_tables = set()
    if app_db.exists():
        app_tables = {r[0] for r in sqlite3.connect(app_db).execute("select name from sqlite_master")}
    assert 'alembic_version' not in app_tables


def test_migrations_refuse_a_migration_url_that_breaks_the_rules(tmp_path):
    p = run_import({'DATABASE_URL': f'sqlite:///{tmp_path}/app.db', 'STREAKFIT_DEPLOYMENT': 'staging',
                    'MIGRATION_DATABASE_URL': url(PROD_EP), 'FLASK_APP': 'app'},
                   code='import sys; from flask.cli import main; sys.argv=["flask","db","upgrade"]; main()')
    assert p.returncode != 0
    assert 'R3' in p.stdout + p.stderr
    assert PASSWORD not in p.stdout + p.stderr


def test_a_non_production_service_cannot_claim_production_through_the_recovery_override():
    """Only the service-ID half of R4 stops this: the recovery override makes
    the endpoint half pass."""
    msg = refused({'RENDER': 'true', 'RENDER_SERVICE_ID': STAGING_SVC, 'STREAKFIT_DEPLOYMENT': 'production',
                   G.RECOVERY_ENDPOINT_VAR: 'ep-new-recovery-123', 'DATABASE_URL': url('ep-new-recovery-123')}, 'R4')
    assert 'not a production service' in msg


# -- the boot check consults the server before anything else -------------------

def _at_head(monkeypatch):
    from alembic.runtime import migration
    from alembic.script import ScriptDirectory
    monkeypatch.setattr(migration.MigrationContext, 'get_current_revision', lambda self: 'h')
    monkeypatch.setattr(ScriptDirectory, 'get_current_head', lambda self: 'h')


def test_boot_check_refuses_when_the_server_identity_check_fails(monkeypatch, caplog):
    import app as A

    def wrong_database(conn, binding):
        raise G.DatabaseBindingError('R7: the URL names endpoint ep-a but the server reports ep-b')
    _at_head(monkeypatch)
    monkeypatch.setattr(A._db_guard, 'verify_server', wrong_database)
    monkeypatch.setattr(A, '_DB_IDENTITY_VERIFIED', False)
    with caplog.at_level(logging.CRITICAL), pytest.raises(SystemExit):
        A._assert_db_at_head()
    assert 'Refusing to start: R7' in caplog.text
    assert A._DB_IDENTITY_VERIFIED is False


def test_boot_check_marks_the_identity_verified_on_success(monkeypatch):
    import app as A
    _at_head(monkeypatch)
    monkeypatch.setattr(A, '_DB_IDENTITY_VERIFIED', False)
    A._assert_db_at_head()
    assert A._DB_IDENTITY_VERIFIED is True
