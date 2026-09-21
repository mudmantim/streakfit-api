"""GET /api/verification/self -- the checks only StreakFit can run on itself.

What these tests defend is not "the endpoint returns 200". It is that the
endpoint cannot *lie* in the three ways this contract was written to prevent:

  * a check that could not run reporting PASS,
  * a FAIL and an UNKNOWN being treated as the same thing,
  * one bad subsystem hiding behind three good ones in the roll-up.

So every check has a negative control here -- a test that drives it to FAIL --
because the contract says a check nobody has seen fail is OBSERVED at best, and
all but one of these claim VERIFIED.

Note the suite's default state: conftest builds the database with `create_all()`,
which never stamps `alembic_version`. So `db.schema-current` legitimately FAILs
in this suite and the rolled-up status is FAIL unless a test says otherwise.
That is a real state (an unmigrated database), and it is asserted rather than
papered over.
"""
import json

import pytest
from sqlalchemy.exc import OperationalError

import app as app_module


CHECK_IDS = ['db.reachable', 'db.schema-current', 'assets.present', 'coach.configured']

# Exactly the vocabularies Mudman Command accepts. Anything else is coerced to
# UNKNOWN on its side, so a typo here would silently drop a check from the verdict.
VALID_STATUSES = {'PASS', 'FAIL', 'UNKNOWN'}
VALID_LEVELS = {'VERIFIED', 'OBSERVED', 'ASSUMED', 'UNKNOWN'}


def _get(client):
    resp = client.get('/api/verification/self')
    assert resp.status_code == 200
    return resp.get_json()


def _check(body, check_id):
    found = [c for c in body["checks"] if c["id"] == check_id]
    assert len(found) == 1, f"expected exactly one {check_id}"
    return found[0]


@pytest.fixture()
def at_head(monkeypatch):
    """Pin the database's reported revision to the chain head."""
    import os
    from alembic.config import Config
    from alembic.script import ScriptDirectory
    from alembic.runtime.migration import MigrationContext

    cfg = Config()
    cfg.set_main_option('script_location', os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'migrations'))
    head = ScriptDirectory.from_config(cfg).get_current_head()
    monkeypatch.setattr(MigrationContext, 'get_current_revision', lambda self: head)
    return head


# ── The contract ────────────────────────────────────────────────────────────

def test_returns_200_json(client):
    resp = client.get('/api/verification/self')
    assert resp.status_code == 200
    assert resp.content_type.startswith('application/json')


def test_envelope_matches_the_contract(client):
    body = _get(client)
    # SUPERSET. The merged framework also publishes `generatedAt` (its own
    # name for the same instant). The contract these tests defend is that the
    # deployed keys are all still there and still mean what they meant --
    # pinning the set exactly would forbid ever adding a field.
    assert {"application", "status", "timestamp", "checks"} <= set(body)
    assert body["application"] == "streakfit"
    assert body["timestamp"].endswith("Z")
    assert body["generatedAt"] == body["timestamp"]


def test_checks_array_is_never_empty(client):
    """Mudman Command turns an empty array into a critical UNKNOWN -- an app
    that answered 200 and said nothing has reported no evidence at all."""
    assert len(_get(client)["checks"]) > 0


def test_reports_the_four_checks_in_a_stable_order(client):
    """The deployed four, still present and still in that relative order.

    The merged build reports twelve. What an external consumer depends on is
    that the ids it already reads have not vanished or been reordered
    underneath it, not that nothing was ever added beside them.
    """
    ids = [c["id"] for c in _get(client)["checks"]]
    for name in CHECK_IDS:
        assert name in ids, f"{name} disappeared from the published checks"
    positions = [ids.index(name) for name in CHECK_IDS]
    assert positions == sorted(positions), \
        f"the deployed checks were reordered: {list(zip(CHECK_IDS, positions))}"
    assert len(ids) == len(set(ids)), f"duplicate check id in {ids}"


def test_every_check_carries_the_contract_fields(client):
    required = {"id", "asserts", "method", "status", "level", "observed",
                "failureReason", "durationMs", "timestamp", "critical"}
    for check in _get(client)["checks"]:
        assert required.issubset(check), f"{check['id']} is missing {required - set(check)}"


def test_statuses_and_levels_are_words_the_consumer_defines(client):
    """A value outside these sets is coerced to UNKNOWN by the dashboard, which
    would quietly drop the check from the verdict."""
    for check in _get(client)["checks"]:
        assert check["status"] in VALID_STATUSES, f"{check['id']}: {check['status']}"
        assert check["level"] in VALID_LEVELS, f"{check['id']}: {check['level']}"


def test_critical_is_an_explicit_boolean(client):
    """Omitting it makes the dashboard default to false, silently demoting a
    check that should block a release."""
    for check in _get(client)["checks"]:
        assert isinstance(check["critical"], bool)


def test_the_database_checks_are_critical_and_the_rest_are_not(client):
    body = _get(client)
    assert _check(body, 'db.reachable')["critical"] is True
    assert _check(body, 'db.schema-current')["critical"] is True
    assert _check(body, 'assets.present')["critical"] is False
    assert _check(body, 'coach.configured')["critical"] is False


def test_a_passing_check_states_no_failure_reason(client, at_head, monkeypatch):
    monkeypatch.setattr(app_module, '_anthropic_api_key', 'configured')
    for check in _get(client)["checks"]:
        if check["status"] == 'PASS':
            assert check["failureReason"] is None, f"{check['id']} passed but gave a reason"


def test_durations_are_recorded(client):
    for check in _get(client)["checks"]:
        assert isinstance(check["durationMs"], (int, float))
        assert check["durationMs"] >= 0


# ── db.reachable: positive and negative ─────────────────────────────────────

def test_db_reachable_passes_against_a_live_database(client):
    check = _check(_get(client), 'db.reachable')
    assert check["status"] == 'PASS'
    assert check["level"] == 'VERIFIED'
    assert check["failureReason"] is None


def test_db_reachable_fails_when_the_database_is_gone(client, monkeypatch):
    """The negative control that earns this check its VERIFIED."""
    def boom(*_a, **_k):
        raise OperationalError('SELECT 1', {}, Exception('connection refused'))

    monkeypatch.setattr(app_module.db.session, 'execute', boom)

    check = _check(_get(client), 'db.reachable')
    assert check["status"] == 'FAIL'
    assert check["level"] == 'VERIFIED'
    assert check["failureReason"]


def test_a_dead_database_does_not_take_the_endpoint_down(client, monkeypatch):
    def boom(*_a, **_k):
        raise OperationalError('SELECT 1', {}, Exception('connection refused'))

    monkeypatch.setattr(app_module.db.session, 'execute', boom)
    assert client.get('/api/verification/self').status_code == 200


# ── db.schema-current: positive and negative ────────────────────────────────

def test_schema_current_passes_when_the_database_is_at_head(client, at_head):
    check = _check(_get(client), 'db.schema-current')
    assert check["status"] == 'PASS'
    assert check["level"] == 'VERIFIED'
    assert 'at head' in check["observed"]


def test_schema_current_fails_when_the_database_is_behind(client, monkeypatch):
    from alembic.runtime.migration import MigrationContext
    monkeypatch.setattr(MigrationContext, 'get_current_revision',
                        lambda self: 'a3f8b1c2d4e5')   # the baseline revision
    check = _check(_get(client), 'db.schema-current')
    assert check["status"] == 'FAIL'
    assert check["level"] == 'VERIFIED'
    assert check["failureReason"]


def test_schema_current_is_unknown_not_fail_when_it_cannot_be_read(client, monkeypatch):
    """A database we cannot reach has not been shown to be WRONG."""
    from alembic.runtime.migration import MigrationContext

    def boom(*_a, **_k):
        raise RuntimeError('could not connect')

    monkeypatch.setattr(MigrationContext, 'configure', boom)

    check = _check(_get(client), 'db.schema-current')
    assert check["status"] == 'UNKNOWN'
    assert check["level"] == 'UNKNOWN'
    assert check["status"] != 'FAIL'


def test_schema_current_reuses_the_build_identity_helper(client, at_head):
    """The two endpoints must never disagree about the schema."""
    identity = client.get('/api/build-identity').get_json()["migration"]
    check = _check(_get(client), 'db.schema-current')
    assert identity["atHead"] is True
    assert check["status"] == 'PASS'


# ── assets.present: positive and negative ───────────────────────────────────

def test_assets_present_passes_against_the_shipped_tree(client):
    check = _check(_get(client), 'assets.present')
    assert check["status"] == 'PASS'
    assert check["level"] == 'VERIFIED'


def test_assets_present_fails_when_the_service_worker_is_missing(client, monkeypatch):
    real = app_module.os.path.exists
    monkeypatch.setattr(app_module.os.path, 'exists',
                        lambda p: False if str(p).endswith('sw.js') else real(p))
    check = _check(_get(client), 'assets.present')
    assert check["status"] == 'FAIL'
    assert 'sw.js' in check["observed"]


def test_assets_present_fails_when_the_manifest_is_missing(client, monkeypatch):
    real = app_module.os.path.exists
    monkeypatch.setattr(app_module.os.path, 'exists',
                        lambda p: False if str(p).endswith('manifest.json') else real(p))
    check = _check(_get(client), 'assets.present')
    assert check["status"] == 'FAIL'
    assert 'manifest' in check["observed"]


def test_assets_present_fails_when_a_declared_icon_is_absent(client, monkeypatch):
    monkeypatch.setattr(app_module.json, 'load',
                        lambda _f: {"icons": [{"src": "/static/icons/does-not-exist.png"}]})
    check = _check(_get(client), 'assets.present')
    assert check["status"] == 'FAIL'
    assert check["failureReason"]


def test_assets_present_fails_when_the_manifest_declares_no_icons(client, monkeypatch):
    monkeypatch.setattr(app_module.json, 'load', lambda _f: {"icons": []})
    check = _check(_get(client), 'assets.present')
    assert check["status"] == 'FAIL'


def test_an_unparseable_manifest_is_unknown_not_fail(client, monkeypatch):
    """Present but unreadable is not the same as absent."""
    def boom(_f):
        raise ValueError('invalid JSON')

    monkeypatch.setattr(app_module.json, 'load', boom)
    check = _check(_get(client), 'assets.present')
    assert check["status"] == 'UNKNOWN'
    assert check["level"] == 'UNKNOWN'


def test_missing_icon_paths_are_counted_not_named(client, monkeypatch):
    """Filesystem layout is not this payload's business."""
    monkeypatch.setattr(app_module.json, 'load',
                        lambda _f: {"icons": [{"src": "/static/icons/secret-path-name.png"}]})
    check = _check(_get(client), 'assets.present')
    assert 'secret-path-name' not in json.dumps(check)


# ── coach.configured: positive and negative ─────────────────────────────────

def test_coach_configured_passes_when_a_key_is_present(client, monkeypatch):
    monkeypatch.setattr(app_module, '_anthropic_api_key', 'sentinel-key-value')
    check = _check(_get(client), 'coach.configured')
    assert check["status"] == 'PASS'


def test_coach_configured_fails_when_no_key_is_present(client, monkeypatch):
    monkeypatch.setattr(app_module, '_anthropic_api_key', '')
    check = _check(_get(client), 'coach.configured')
    assert check["status"] == 'FAIL'
    assert check["failureReason"]


def test_coach_configured_is_observed_never_verified(client, monkeypatch):
    """A key that is present has not been shown to WORK, and proving that would
    mean a paid call to a third party -- which this contract forbids."""
    monkeypatch.setattr(app_module, '_anthropic_api_key', 'sentinel-key-value')
    assert _check(_get(client), 'coach.configured')["level"] == 'OBSERVED'


def test_coach_check_never_contacts_anthropic(client, monkeypatch):
    """Free and non-destructive: no third party is contacted."""
    def boom(*_a, **_k):
        raise AssertionError('the self-check must not construct an Anthropic client')

    monkeypatch.setattr(app_module._anthropic_lib, 'Anthropic', boom)
    monkeypatch.setattr(app_module, '_anthropic_api_key', 'sentinel-key-value')
    assert client.get('/api/verification/self').status_code == 200


def test_the_coach_key_value_never_appears(client, monkeypatch):
    monkeypatch.setattr(app_module, '_anthropic_api_key', 'sk-ant-sentinel-secret-value')
    assert 'sentinel-secret-value' not in json.dumps(_get(client))


# ── Unexpected exceptions ───────────────────────────────────────────────────

def test_an_unexpected_exception_is_unknown_not_fail(client, monkeypatch):
    """A broken CHECK must not masquerade as a broken PRODUCT.

    The seam moved: the deployed build had one function per check, and the
    merged framework builds them inline. `_read_migration_state` is the
    equivalent seam -- it is what the schema checks call, and it is shared
    with /api/build-identity.
    """
    def boom():
        raise RuntimeError('something nobody predicted')

    monkeypatch.setattr(app_module, '_read_migration_state', boom)

    body = _get(client)
    for name in ('db.migrations', 'db.schema-current'):
        check = _check(body, name)
        assert check["status"] == 'UNKNOWN', f"{name} reported {check['status']}"
        assert check["level"] == 'UNKNOWN'
        # The TYPE, and nothing that came with it.
        assert check["observed"] == 'RuntimeError'
        assert 'nobody predicted' not in json.dumps(check)


def test_an_unexpected_exception_does_not_leak_its_message(client, monkeypatch):
    """An exception message can carry a connection string. Only the type is reported."""
    def boom():
        raise RuntimeError('postgresql://user:pw@internal-host.example/db')

    monkeypatch.setattr(app_module, '_read_migration_state', boom)

    body = _get(client)
    raw = json.dumps(body)
    assert 'internal-host.example' not in raw
    assert 'postgresql://' not in raw
    assert 'user:pw' not in raw
    assert 'RuntimeError' in json.dumps(_check(body, 'db.migrations'))


def test_one_broken_check_does_not_take_the_endpoint_down(client, monkeypatch):
    """An endpoint that 500s reports nothing at all about the other checks."""
    def boom():
        raise RuntimeError('nope')

    before = len(_get(client)["checks"])
    monkeypatch.setattr(app_module, '_read_migration_state', boom)

    body = _get(client)          # _get asserts 200
    assert len(body["checks"]) == before, \
        "a broken check removed checks from the response instead of reporting itself"
    # Every other check still reported a real verdict.
    others = [c for c in body["checks"]
              if c["id"] not in ('db.migrations', 'db.schema-current')]
    assert others and all(c["status"] in VALID_STATUSES for c in others)


# ── Roll-up: weakest link, never an average ─────────────────────────────────

def test_roll_up_is_pass_only_when_everything_passes():
    assert app_module._roll_up([{"status": 'PASS'}, {"status": 'PASS'}]) == 'PASS'


def test_roll_up_is_unknown_when_anything_is_unknown():
    assert app_module._roll_up([{"status": 'PASS'}, {"status": 'UNKNOWN'}]) == 'UNKNOWN'


def test_roll_up_is_fail_when_anything_fails():
    assert app_module._roll_up([{"status": 'PASS'}, {"status": 'FAIL'}]) == 'FAIL'


def test_a_fail_outranks_an_unknown():
    """The ordering that stops a failure hiding behind an inconclusive result."""
    assert app_module._roll_up(
        [{"status": 'UNKNOWN'}, {"status": 'FAIL'}, {"status": 'PASS'}]) == 'FAIL'


def test_one_failure_is_not_averaged_away():
    assert app_module._roll_up(
        [{"status": 'PASS'}] * 9 + [{"status": 'FAIL'}]) == 'FAIL'


def test_overall_status_is_the_roll_up_of_the_published_checks(client, at_head,
                                                               monkeypatch):
    """The envelope's verdict must be derived from the checks it published.

    This asserted four PASSes end to end when there were exactly four checks.
    The merged build reports twelve, several of which are legitimately UNKNOWN
    in this suite (no content store, no retention runs, no delivery worker), so
    a blanket PASS is no longer the right expectation. The property that
    actually mattered survives: `status` is the weakest link of the array in
    the same response, so one bad subsystem cannot hide behind the others.
    """
    monkeypatch.setattr(app_module, '_anthropic_api_key', 'configured')
    body = _get(client)
    assert body["status"] == app_module._roll_up(body["checks"])
    statuses = {c["status"] for c in body["checks"]}
    if 'FAIL' in statuses:
        assert body["status"] == 'FAIL'
    elif 'UNKNOWN' in statuses:
        assert body["status"] == 'UNKNOWN'
    else:
        assert body["status"] == 'PASS'
    # The deployed four still carry real verdicts rather than placeholders.
    for name in CHECK_IDS:
        assert _check(body, name)["status"] in VALID_STATUSES


def test_overall_status_reflects_a_real_failure(client, monkeypatch):
    """Unmigrated database in this suite: schema-current FAILs, so does the roll-up."""
    monkeypatch.setattr(app_module, '_anthropic_api_key', 'configured')
    body = _get(client)
    assert _check(body, 'db.schema-current')["status"] == 'FAIL'
    assert body["status"] == 'FAIL'


# ── Read-only, and nothing state-changing ───────────────────────────────────

def test_endpoint_issues_no_writes(client, app):
    from sqlalchemy import event

    engine = app_module.db.engine
    statements = []

    def record(_conn, _cursor, statement, *_args):
        statements.append(statement)

    event.listen(engine, 'before_cursor_execute', record)
    try:
        assert client.get('/api/verification/self').status_code == 200
    finally:
        event.remove(engine, 'before_cursor_execute', record)

    forbidden = ('INSERT', 'UPDATE', 'DELETE', 'CREATE', 'DROP', 'ALTER', 'TRUNCATE')
    for statement in statements:
        assert not statement.lstrip().upper().startswith(forbidden), f"wrote: {statement}"


def test_endpoint_creates_no_verification_run_rows(client, app):
    """StreakFit's own /api/admin/verify writes these. This must never start it."""
    before = app_module.db.session.query(app_module.VerificationRun).count()
    client.get('/api/verification/self')
    assert app_module.db.session.query(app_module.VerificationRun).count() == before


def test_endpoint_does_not_start_the_background_verification(client):
    before = dict(app_module._verification_state)
    client.get('/api/verification/self')
    assert dict(app_module._verification_state) == before
    assert app_module._verification_state["running"] is False


def test_endpoint_does_not_stamp_the_database(client, app):
    from sqlalchemy import inspect
    client.get('/api/verification/self')
    assert 'alembic_version' not in inspect(app_module.db.engine).get_table_names()


def test_repeated_calls_change_nothing(client, app):
    """Safe to poll: two calls, same verdict, no rows created."""
    before = app_module.db.session.query(app_module.VerificationRun).count()
    first = _get(client)
    second = _get(client)
    assert [c["id"] for c in first["checks"]] == [c["id"] for c in second["checks"]]
    assert first["status"] == second["status"]
    assert app_module.db.session.query(app_module.VerificationRun).count() == before


# ── Nothing sensitive in the payload ────────────────────────────────────────

def test_no_secret_value_appears_anywhere(client, monkeypatch):
    secrets = {
        'SECRET_KEY': 'sentinel-flask-secret',
        'JWT_SECRET_KEY': 'sentinel-jwt-secret',
        'ANTHROPIC_API_KEY': 'sentinel-anthropic-key',
        'ADMIN_SECRET': 'sentinel-admin-secret',
        'DATABASE_URL': 'postgresql://sentinel_user:sentinel_pw@sentinel-host.example/sentinel_db',
    }
    for name, value in secrets.items():
        monkeypatch.setenv(name, value)

    raw = json.dumps(_get(client))
    for name, value in secrets.items():
        assert value not in raw, f"{name}'s value leaked"
    for fragment in ('sentinel_user', 'sentinel_pw', 'sentinel-host.example', 'sentinel_db'):
        assert fragment not in raw, f"connection-string fragment {fragment!r} leaked"


def test_no_infrastructure_identifier_appears(client, monkeypatch):
    monkeypatch.setenv('RENDER_SERVICE_ID', 'srv-sentinel123')
    monkeypatch.setenv('RENDER_INSTANCE_ID', 'srv-sentinel123-xyz')
    assert 'srv-sentinel123' not in json.dumps(_get(client))


def test_no_filesystem_path_leaks(client):
    raw = json.dumps(_get(client))
    assert '/home/' not in raw
    assert '/opt/render' not in raw
    assert '/static/' not in raw


def test_payload_carries_no_user_data(client, app):
    client.post('/api/register', json={'username': 'sentineluser', 'password': 'WalkTest123!'})
    assert 'sentineluser' not in json.dumps(_get(client))


def test_requires_no_credentials(client):
    """Unauthenticated by contract, exactly as /api/build-identity is."""
    resp = client.get('/api/verification/self')
    assert resp.status_code == 200
    assert resp.status_code not in (401, 403)


def test_admin_secret_is_not_required(client, monkeypatch):
    """A guard here would make the endpoint unreachable for the dashboard, which
    sends no headers -- and an app whose auth is broken is the one worth reporting on."""
    monkeypatch.setenv('ADMIN_SECRET', 'sentinel-admin-secret')
    assert client.get('/api/verification/self').status_code == 200
