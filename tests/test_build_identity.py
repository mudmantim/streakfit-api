"""GET /api/build-identity -- Build Identity Contract v1.0.0.

The endpoint exists so that nothing this dashboard reports is evidence about an
unidentified build. These tests hold the two properties that give it its value:

  1. It answers the contract exactly, including when it does not know something.
     An honest null is a passing answer here; an invented value is the defect.
  2. It answers AT ALL when the database is gone. A build that cannot see its
     database is the single case where identity matters most, so a 500 there
     would remove the endpoint precisely when it is needed.

The fixtures build the test database with `create_all()`, which never stamps
`alembic_version` -- so the default state in this suite is "readable, nothing
applied". That is a real state (an unmigrated database), and it is asserted as
such rather than worked around.
"""
import json

import pytest

import app as app_module


REQUIRED_FIELDS = {
    "schemaVersion", "application", "version", "gitSha", "gitBranch", "buildDate",
    "environment", "deploymentId", "instanceId", "migration", "storageProvider",
    "featureFlags", "apiVersion", "verificationFrameworkVersion", "healthTimestamp",
}

# The values Mudman Command rejects the whole response for when any is missing
# or empty (runner.ts -> checkBuildIdentity).
CONSUMER_REQUIRED = ("application", "environment", "schemaVersion")


@pytest.fixture()
def identity(client):
    resp = client.get('/api/build-identity')
    assert resp.status_code == 200
    return resp.get_json()


# ── The contract ────────────────────────────────────────────────────────────

def test_returns_200_json(client):
    resp = client.get('/api/build-identity')
    assert resp.status_code == 200
    assert resp.content_type.startswith('application/json')


def test_carries_every_contract_field(identity):
    assert REQUIRED_FIELDS.issubset(identity.keys())


def test_the_fields_the_consumer_gates_on_are_non_empty(identity):
    """Mudman Command FAILs the check outright if any of these is falsy."""
    for field in CONSUMER_REQUIRED:
        assert identity[field], f"{field} must be non-empty for the dashboard to accept the response"


def test_application_matches_the_dashboard_registry_id(identity):
    """Must equal the `id` of StreakFit's entry in Mudman Command's registry."""
    assert identity["application"] == "streakfit"


def test_declares_the_contract_and_framework_versions(identity):
    assert identity["schemaVersion"] == "1.0.0"
    assert identity["verificationFrameworkVersion"] == "1.0.0"
    assert identity["apiVersion"] == "1"


def test_environment_is_one_of_the_contract_values(identity):
    assert identity["environment"] in ("production", "staging", "development")


def test_health_timestamp_is_iso_utc(identity):
    from datetime import datetime
    assert identity["healthTimestamp"].endswith("Z")
    datetime.fromisoformat(identity["healthTimestamp"].rstrip("Z"))


def test_feature_flags_are_booleans_only(identity):
    """Booleans, never configuration values -- a flag's *setting* is a leak."""
    assert identity["featureFlags"]
    for name, value in identity["featureFlags"].items():
        assert isinstance(value, bool), f"featureFlags.{name} must be a boolean, got {type(value)}"


def test_storage_provider_is_not_guessed(identity):
    """The managed Postgres provider is not confirmed, so it is not named."""
    assert identity["storageProvider"] == "unknown"


# ── Commit identity: known and unknown ──────────────────────────────────────

def test_reports_the_commit_when_render_supplies_it(client, monkeypatch):
    monkeypatch.setenv('RENDER_GIT_COMMIT', 'abc123def4567890')
    body = client.get('/api/build-identity').get_json()
    # _get_commit_sha() truncates to 12, which is the existing helper's behaviour.
    assert body["gitSha"] == 'abc123def456'
    assert body["version"] == 'abc123def456'


def test_reports_the_branch_when_render_supplies_it(client, monkeypatch):
    monkeypatch.setenv('RENDER_GIT_BRANCH', 'main')
    assert client.get('/api/build-identity').get_json()["gitBranch"] == 'main'


def test_unknown_commit_is_null_and_version_says_unknown(client, monkeypatch):
    """An honest null. A fabricated SHA would make every later result a lie."""
    monkeypatch.setattr(app_module, '_get_commit_sha', lambda: None)
    body = client.get('/api/build-identity').get_json()
    assert body["gitSha"] is None
    assert body["version"] == "unknown"


def test_unknown_branch_is_null(client, monkeypatch):
    monkeypatch.delenv('RENDER_GIT_BRANCH', raising=False)
    assert client.get('/api/build-identity').get_json()["gitBranch"] is None


# ── Migration state ─────────────────────────────────────────────────────────

def test_migration_state_is_readable_here(identity):
    """create_all() never stamps alembic_version: readable, nothing applied."""
    migration = identity["migration"]
    assert migration["state"] == "ok"
    assert migration["latest"] is None
    assert migration["appliedCount"] == 0
    assert migration["atHead"] is False


def test_migration_reports_at_head_when_the_database_is_current(client, monkeypatch):
    """The comparison itself: stamped revision == the chain's head."""
    from alembic.config import Config
    from alembic.script import ScriptDirectory
    from alembic.runtime.migration import MigrationContext
    import os

    cfg = Config()
    cfg.set_main_option('script_location', os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'migrations'))
    head = ScriptDirectory.from_config(cfg).get_current_head()

    monkeypatch.setattr(MigrationContext, 'get_current_revision', lambda self: head)

    migration = client.get('/api/build-identity').get_json()["migration"]
    assert migration["state"] == "ok"
    assert migration["latest"] == head
    assert migration["atHead"] is True
    assert migration["appliedCount"] == len(list(
        ScriptDirectory.from_config(cfg).iterate_revisions(head, 'base')))


def test_a_behind_head_database_is_reported_not_hidden(client, monkeypatch):
    from alembic.runtime.migration import MigrationContext
    monkeypatch.setattr(MigrationContext, 'get_current_revision',
                        lambda self: 'a3f8b1c2d4e5')   # the baseline revision
    migration = client.get('/api/build-identity').get_json()["migration"]
    assert migration["state"] == "ok"
    assert migration["latest"] == 'a3f8b1c2d4e5'
    assert migration["atHead"] is False


def test_database_unavailable_still_returns_an_identity(client, monkeypatch):
    """The rule this endpoint exists for: identify the build even with no database."""
    from alembic.runtime.migration import MigrationContext

    def boom(*_args, **_kwargs):
        raise RuntimeError('could not connect to server')

    monkeypatch.setattr(MigrationContext, 'configure', boom)

    resp = client.get('/api/build-identity')
    assert resp.status_code == 200, "a dead database must not take the endpoint down"
    body = resp.get_json()
    assert body["migration"] == {
        "latest": None, "appliedCount": None, "state": "unknown", "atHead": None,
    }
    # The identity itself is unaffected -- that is the whole point.
    assert body["application"] == "streakfit"
    assert body["schemaVersion"] == "1.0.0"


def test_unreadable_migration_directory_is_unknown_not_an_error(client, monkeypatch):
    import alembic.config

    def boom(*_args, **_kwargs):
        raise RuntimeError('no such directory')

    monkeypatch.setattr(alembic.config, 'Config', boom)

    resp = client.get('/api/build-identity')
    assert resp.status_code == 200
    assert resp.get_json()["migration"]["state"] == "unknown"


# ── Read-only ───────────────────────────────────────────────────────────────

def test_endpoint_issues_no_writes(client, app):
    """No INSERT/UPDATE/DELETE/DDL reaches the database. The contract says this
    is safe to poll every few seconds, which is only true if it writes nothing."""
    from sqlalchemy import event

    engine = app_module.db.engine
    statements = []

    def record(_conn, _cursor, statement, *_args):
        statements.append(statement)

    event.listen(engine, 'before_cursor_execute', record)
    try:
        assert client.get('/api/build-identity').status_code == 200
    finally:
        event.remove(engine, 'before_cursor_execute', record)

    forbidden = ('INSERT', 'UPDATE', 'DELETE', 'CREATE', 'DROP', 'ALTER', 'TRUNCATE')
    for statement in statements:
        assert not statement.lstrip().upper().startswith(forbidden), f"wrote: {statement}"


def test_endpoint_does_not_stamp_the_database(client, app):
    """Reading migration state must never create alembic_version as a side effect."""
    from sqlalchemy import inspect

    client.get('/api/build-identity')
    assert 'alembic_version' not in inspect(app_module.db.engine).get_table_names()


# ── Nothing sensitive in the payload ────────────────────────────────────────

def test_no_secret_value_appears_anywhere_in_the_payload(client, monkeypatch):
    """Every secret this app takes, set to a recognisable value, must be absent."""
    secrets = {
        'SECRET_KEY': 'sentinel-flask-secret',
        'JWT_SECRET_KEY': 'sentinel-jwt-secret',
        'ANTHROPIC_API_KEY': 'sentinel-anthropic-key',
        'ADMIN_SECRET': 'sentinel-admin-secret',
        'DATABASE_URL': 'postgresql://sentinel_user:sentinel_pw@sentinel-host.example/sentinel_db',
    }
    for name, value in secrets.items():
        monkeypatch.setenv(name, value)

    raw = json.dumps(client.get('/api/build-identity').get_json())
    for name, value in secrets.items():
        assert value not in raw, f"{name}'s value leaked into the payload"
    for fragment in ('sentinel_user', 'sentinel_pw', 'sentinel-host.example', 'sentinel_db'):
        assert fragment not in raw, f"connection-string fragment {fragment!r} leaked"


def test_internal_infrastructure_identifiers_are_withheld(client, monkeypatch):
    """The contract keeps the keys; this deployment does not fill them."""
    monkeypatch.setenv('RENDER_SERVICE_ID', 'srv-sentinel123')
    monkeypatch.setenv('RENDER_INSTANCE_ID', 'srv-sentinel123-xyz')

    body = client.get('/api/build-identity').get_json()
    assert body["deploymentId"] is None
    assert body["instanceId"] is None
    assert 'srv-sentinel123' not in json.dumps(body)


def test_no_filesystem_path_leaks(client):
    raw = json.dumps(client.get('/api/build-identity').get_json())
    assert '/home/' not in raw
    assert '/opt/render' not in raw
    assert 'migrations' not in raw


def test_payload_carries_no_user_data(client, app):
    """A registered user's identifiers must not appear. Nothing here reads users,
    and this is the test that keeps it that way."""
    client.post('/api/register', json={'username': 'sentineluser', 'password': 'WalkTest123!'})
    raw = json.dumps(client.get('/api/build-identity').get_json())
    assert 'sentineluser' not in raw


def test_requires_no_credentials(client):
    """Unauthenticated by contract: Mudman Command probes it with no headers."""
    resp = client.get('/api/build-identity')
    assert resp.status_code == 200
    assert resp.status_code not in (401, 403)
