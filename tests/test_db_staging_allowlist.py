"""R8 (staging allowlist) and R9 (connection-string shapes libpq reads differently).

2026-10-15: the staging rehearsal review found that R2 protects only the one
database known to be production. The incident's recovery branch -- a full copy
of production data -- was not on that list, and a hosted staging process would
have connected to it, or to any other endpoint nobody had approved. R8 turns
the question around: a hosted non-production process may use only an endpoint
on a reviewed allowlist. R9 refuses URLs whose host Python and libpq would read
differently. Synthetic values only; nothing here connects anywhere.
"""
import pytest

import db_guard as G

STAGING = 'ep-young-sound-b4qsoqe8'
PROD = 'ep-odd-star-ajye3lag'
RECOVERY = 'ep-dawn-fire-aj4zfzmz'
UNKNOWN = 'ep-unknown-branch-12345678'
PROD_SVC = 'srv-d8f1hh6rnols73ak3a10'
NEW_SVC = 'srv-fakerehearsal0000000'


def url(ep, pooler=False, extra='', host_suffix='.c-2.us-east-2.aws.neon.tech'):
    host = f'{ep}-pooler' if pooler else ep
    return f'postgresql://neondb_owner:not-a-password@{host}{host_suffix}/neondb?sslmode=require{extra}'


def hosted_staging(u, **extra):
    return {'RENDER': 'true', 'RENDER_SERVICE_ID': NEW_SVC, 'STREAKFIT_DEPLOYMENT': 'staging',
            'DATABASE_URL': u, **extra}


def refused(env, rule):
    with pytest.raises(G.DatabaseBindingError) as exc:
        G.resolve(env)
    assert str(exc.value).startswith(rule + ':'), str(exc.value)
    assert 'not-a-password' not in str(exc.value)
    return str(exc.value)


class FakeConn:
    class dialect:
        name = 'postgresql'

    def __init__(self, reported):
        self.reported = reported

    def exec_driver_sql(self, _sql):
        reported = self.reported

        class R:
            def scalar(self):
                return reported
        return R()


# --- the lists themselves --------------------------------------------------

def test_the_lists_are_disjoint_well_formed_code_constants():
    assert G.STAGING_DB_ENDPOINTS == frozenset({STAGING})
    assert RECOVERY in G.PRODUCTION_DERIVED_DB_ENDPOINTS
    protected = G.PRODUCTION_DB_ENDPOINTS | G.PRODUCTION_DERIVED_DB_ENDPOINTS
    assert not (protected & G.STAGING_DB_ENDPOINTS)
    for ep in protected | G.STAGING_DB_ENDPOINTS:
        assert ep == ep.lower() and ep.startswith('ep-') and not ep.endswith('-pooler')


def test_no_environment_variable_can_widen_the_allowlist():
    for var in ('STREAKFIT_STAGING_DB_ENDPOINTS', 'STAGING_DB_ENDPOINTS', G.RECOVERY_ENDPOINT_VAR,
                G.LOCAL_PRODUCTION_OVERRIDE_VAR):
        refused(hosted_staging(url(UNKNOWN), **{var: UNKNOWN if 'ALLOW' not in var else '1'}), 'R8')


# --- the approved staging database ------------------------------------------

@pytest.mark.parametrize('pooler', [False, True])
def test_the_approved_staging_endpoint_is_accepted_direct_and_pooled(pooler):
    b = G.resolve(hosted_staging(url(STAGING, pooler=pooler)))
    assert (b.deployment, b.endpoint) == ('staging', STAGING)


def test_an_upper_case_host_is_the_same_endpoint():
    assert G.resolve(hosted_staging(url(STAGING.upper()))).endpoint == STAGING


# --- wrong, unknown or lookalike endpoints ----------------------------------

@pytest.mark.parametrize('ep', [UNKNOWN, STAGING + 'x', 'x' + STAGING, STAGING + '-pooler-pooler',
                                STAGING[:-1], 'ep-young-sound-b4qsoqe9'])
def test_a_hosted_staging_process_refuses_any_unapproved_neon_endpoint(ep):
    refused(hosted_staging(url(ep)), 'R8')


@pytest.mark.parametrize('u', [
    'postgresql://u:p@db.example.com/x',
    f'postgresql://u:p@{STAGING}.neon.tech.evil.example/neondb',
    f'postgresql://u:p@{STAGING}.example.com/neondb',
])
def test_a_hosted_staging_process_refuses_non_neon_and_lookalike_hosts(u):
    refused(hosted_staging(u), 'R8')


def test_a_hosted_development_process_is_held_to_the_same_allowlist():
    refused({'RENDER': 'true', 'RENDER_SERVICE_ID': NEW_SVC, 'STREAKFIT_DEPLOYMENT': 'development',
             'DATABASE_URL': url(UNKNOWN)}, 'R8')


def test_local_development_and_ci_are_not_restricted_by_the_allowlist():
    """R8 is about hosted copies of the service. A laptop or CI database is free."""
    for env in ({'DATABASE_URL': url(UNKNOWN)}, {'DATABASE_URL': 'postgresql://u:p@localhost/x'},
                {'STREAKFIT_DEPLOYMENT': 'staging', 'DATABASE_URL': url(UNKNOWN)}):
        G.resolve(env)


def test_an_explicit_non_postgres_url_on_a_hosted_staging_process_is_left_alone():
    """The test suite fakes hosted processes on SQLite; no production data is reachable there."""
    assert G.resolve(hosted_staging('sqlite:///x.db')).endpoint is None


def test_production_is_not_affected_by_the_staging_allowlist():
    b = G.resolve({'RENDER': 'true', 'RENDER_SERVICE_ID': PROD_SVC, 'DATABASE_URL': url(PROD)})
    assert (b.deployment, b.endpoint) == ('production', PROD)


# --- copies of production data (the recovery branch) -------------------------

@pytest.mark.parametrize('pooler', [False, True])
def test_a_hosted_staging_service_never_reaches_the_recovery_branch(pooler):
    msg = refused(hosted_staging(url(RECOVERY, pooler=pooler)), 'R2')
    assert 'copy of production data' in msg


def test_a_declared_staging_process_without_a_service_id_is_refused_the_recovery_branch():
    refused({'STREAKFIT_DEPLOYMENT': 'staging', 'DATABASE_URL': url(RECOVERY)}, 'R3')
    refused({'RENDER': 'true', 'STREAKFIT_DEPLOYMENT': 'staging', 'DATABASE_URL': url(RECOVERY)}, 'R3')


def test_a_laptop_needs_the_explicit_override_for_the_recovery_branch_too():
    refused({'DATABASE_URL': url(RECOVERY)}, 'R5')
    assert G.resolve({'DATABASE_URL': url(RECOVERY), G.LOCAL_PRODUCTION_OVERRIDE_VAR: '1'}).endpoint == RECOVERY


def test_production_never_silently_runs_on_the_recovery_branch():
    env = {'RENDER': 'true', 'RENDER_SERVICE_ID': PROD_SVC, 'DATABASE_URL': url(RECOVERY)}
    refused(env, 'R4')
    # a deliberate disaster recovery onto it stays possible, named exactly
    assert G.resolve({**env, G.RECOVERY_ENDPOINT_VAR: RECOVERY}).endpoint == RECOVERY


# --- R7 with R8: the server's own answer is held to the allowlist ------------

def test_the_server_reporting_the_recovery_branch_is_refused_even_if_the_url_looked_right():
    b = G.resolve(hosted_staging(url(STAGING)))
    with pytest.raises(G.DatabaseBindingError, match='^R7:'):
        G.verify_server(FakeConn(RECOVERY), b)


def test_a_url_naming_no_neon_endpoint_cannot_borrow_an_unapproved_server_identity():
    """Declared staging, unhosted (CI-like): URL host is not Neon, but the server
    turns out to be an unknown hosted Neon endpoint. Not hosted, so R8 does not
    apply; a hosted process with a non-Neon URL never gets this far (R8 at resolve)."""
    b = G.resolve({'STREAKFIT_DEPLOYMENT': 'staging', 'DATABASE_URL': 'postgresql://u:p@db.example.com/x'})
    assert G.verify_server(FakeConn(UNKNOWN), b) == UNKNOWN
    with pytest.raises(G.DatabaseBindingError, match='^R3:'):
        G.verify_server(FakeConn(RECOVERY), b)


def test_the_approved_server_passes():
    b = G.resolve(hosted_staging(url(STAGING, pooler=True)))
    assert G.verify_server(FakeConn(STAGING), b) == STAGING


# --- R9: URLs libpq reads differently ---------------------------------------

@pytest.mark.parametrize('u', [
    # libpq tries each host in turn; urlsplit judges only the joined string's first label
    f'postgresql://u:p@{STAGING}.c-2.us-east-2.aws.neon.tech,{PROD}.c-2.us-east-2.aws.neon.tech/neondb',
    f'postgresql://u:p@{STAGING}.c-2.us-east-2.aws.neon.tech:5432,{RECOVERY}.c-2.us-east-2.aws.neon.tech:5432/neondb',
    # two '@': Python takes the last, libpq the first
    f'postgresql://u:p@{PROD}.c-2.us-east-2.aws.neon.tech@{STAGING}.c-2.us-east-2.aws.neon.tech/neondb',
    # percent-encoded host
    f'postgresql://u:p@{STAGING}%2Eevil.example/neondb',
    # Neon's SNI-less routing chooses the endpoint without the hostname
    url(STAGING, extra=f'&options=endpoint%3D{PROD}'),
    url(STAGING, extra=f'&options=-c%20x%3D1%20endpoint%3D{RECOVERY}'),
])
def test_ambiguous_connection_strings_are_refused_hosted_and_local(u):
    refused(hosted_staging(u), 'R9')
    refused({'DATABASE_URL': u}, 'R9')


def test_pgoptions_cannot_choose_the_endpoint_either():
    refused(hosted_staging(url(STAGING), PGOPTIONS=f'endpoint={PROD}'), 'R9')


def test_harmless_options_are_still_allowed():
    assert G.resolve(hosted_staging(url(STAGING, extra='&options=-c%20statement_timeout%3D5000'))).endpoint == STAGING
    assert G.resolve(hosted_staging(url(STAGING), PGOPTIONS='-c statement_timeout=5000')).endpoint == STAGING


def test_the_migration_url_gets_the_same_rules():
    refused({**hosted_staging(url(STAGING)), 'MIGRATION_DATABASE_URL': url(UNKNOWN)}
            | {'DATABASE_URL': url(UNKNOWN)}, 'R8')
    with pytest.raises(G.DatabaseBindingError, match='^R8:'):
        G.resolve({**hosted_staging(url(STAGING)), 'MIGRATION_DATABASE_URL': url(UNKNOWN)},
                  url_var='MIGRATION_DATABASE_URL')


# --- misconfiguration ---------------------------------------------------------

def test_missing_or_conflicting_settings_fail_closed():
    refused({'RENDER': 'true', 'RENDER_SERVICE_ID': NEW_SVC, 'STREAKFIT_DEPLOYMENT': 'staging'}, 'R1')
    with pytest.raises(G.DatabaseBindingError, match='STREAKFIT_DEPLOYMENT'):
        G.resolve(hosted_staging(url(STAGING), STREAKFIT_DEPLOYMENT='staging,production'))
    # undeclared on a non-production service: hosted means production -> R4
    refused({'RENDER': 'true', 'RENDER_SERVICE_ID': NEW_SVC, 'DATABASE_URL': url(STAGING)}, 'R4')
    # the production service declaring staging is still held to the staging allowlist
    refused({'RENDER': 'true', 'RENDER_SERVICE_ID': PROD_SVC, 'STREAKFIT_DEPLOYMENT': 'staging',
             'DATABASE_URL': url(UNKNOWN)}, 'R8')


def test_no_error_message_carries_the_password():
    for env in (hosted_staging(url(UNKNOWN)), hosted_staging(url(RECOVERY)),
                hosted_staging(url(STAGING, extra=f'&options=endpoint%3D{PROD}'))):
        with pytest.raises(G.DatabaseBindingError) as exc:
            G.resolve(env)
        assert 'not-a-password' not in str(exc.value) and 'neondb_owner' not in str(exc.value)


def test_a_neon_endpoint_guc_is_not_mistaken_for_routing():
    """`-c neon.endpoint_id=...` is how the local PostgreSQL tests fake a Neon server."""
    G.resolve({'DATABASE_URL': 'postgresql://u:p@localhost/x?options=-c%20neon.endpoint_id%3Dep-a-b-12345678'})
    G.resolve({'DATABASE_URL': 'postgresql://u:p@localhost/x?options=-c%20myapp.endpoint%3Dabc'})   # a dotted GUC
    refused({'DATABASE_URL': url(STAGING, extra=f'&options=project%3D{PROD}')}, 'R9')
    refused({'DATABASE_URL': url(STAGING, extra=f'&options=--endpoint%3D{PROD}')}, 'R9')


def test_a_local_process_with_only_the_head_flag_is_not_held_to_the_render_allowlist():
    """STREAKFIT_ENFORCE_DB_HEAD=1 alone marks a process hosted for R1/R2, but R8
    is about copies of the Render service, which always carry Render's markers."""
    b = G.resolve({'STREAKFIT_ENFORCE_DB_HEAD': '1', 'STREAKFIT_DEPLOYMENT': 'staging',
                   'DATABASE_URL': 'postgresql://u:p@localhost/x'})
    assert b.hosted and b.endpoint is None
    refused({'RENDER_SERVICE_ID': NEW_SVC, 'STREAKFIT_DEPLOYMENT': 'staging', 'DATABASE_URL': url(UNKNOWN)}, 'R8')


# Neon's other SNI-less routes (2026-10-15 evening review): the endpoint at the
# start of the password, or key=value text in the database name. libpq passes
# both to Neon untouched, so the hostname would not decide the endpoint.
@pytest.mark.parametrize('password', [
    f'endpoint={PROD};not-a-password', f'endpoint={PROD}$not-a-password',
    f'ENDPOINT={PROD};not-a-password', f'project={PROD};not-a-password',
    f'endpoint%3D{PROD}%3Bnot-a-password',
])
def test_a_password_that_selects_the_endpoint_is_refused(password):
    u = url(STAGING).replace('not-a-password', password, 1)
    msg = refused(hosted_staging(u), 'R9')
    assert 'password' in msg and PROD not in msg
    refused({'DATABASE_URL': u}, 'R9')


@pytest.mark.parametrize('db', [f'neondb%20options%3Dendpoint%3D{PROD}', f'endpoint%3D{PROD}'])
def test_a_database_name_that_selects_the_endpoint_is_refused(db):
    refused(hosted_staging(url(STAGING).replace('/neondb?', f'/{db}?', 1)), 'R9')


def test_an_ordinary_password_containing_the_word_is_allowed():
    u = url(STAGING).replace('not-a-password', 'xendpoint=1not-a-password', 1)
    assert G.resolve(hosted_staging(u)).endpoint == STAGING
