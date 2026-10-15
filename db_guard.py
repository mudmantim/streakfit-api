"""Which database may this process use? Decided before anything connects.

Incident 2026-10-13: a staging web service was created with production's
DATABASE_URL. Nothing in the app could tell -- every Render service called
itself "production" (`_detect_environment()` reads only "am I on Render"), the
boot check only asked "is the schema at head" (production's was), and the
retention sweeper started against production from a test service. The checks
that would have caught it were all manual: a person reading a hostname.

This module is the automatic version. It is pure (no Flask, no SQLAlchemy at
import) so the app, the migration runner and the tests share one copy.

RULES, each one an observed failure or a near-miss:

  R1  A hosted process never falls back to SQLite. Missing DATABASE_URL on
      Render used to mean "quietly use a file on the container's disk".
      (An explicitly configured non-PostgreSQL URL is left alone.)
  R2  The production database is reachable only from production Render
      services (by service ID, which Render sets and nobody types). A copy of
      the service -- staging, a preview, a one-off -- is refused, whatever its
      own variables say. This alone would have stopped the incident.
  R3  A deployment that declares itself non-production
      (STREAKFIT_DEPLOYMENT=staging|development) never uses the production
      database.
  R4  A process declaring production must be a production service, and must
      be on the production database, so production cannot quietly run on a
      staging copy either. Disaster recovery onto a NEW endpoint is possible
      only by naming that endpoint in STREAKFIT_PRODUCTION_DB_ENDPOINT, which
      must then match the URL exactly.
  R5  A laptop or CI job reaching the production database must say so
      (STREAKFIT_ALLOW_PRODUCTION_DB_FROM_HERE=1). Nothing in normal
      development or CI does.
  R6  Connection strings that route around the host check are refused:
      libpq honours ?host=, ?hostaddr= and ?service= over the URL's host, and
      PGHOSTADDR / PGSERVICE / PGSERVICEFILE from the environment.
  R7  After connecting, the SERVER's own idea of which Neon endpoint it is
      (`neon.endpoint_id`) must match the URL, and is re-checked against
      R2-R5 and R8. A DNS trick or a proxy cannot make the URL lie for it.
  R8  A hosted non-production process (staging, a preview, a one-off) may use
      ONLY an allowlisted staging endpoint (STAGING_DB_ENDPOINTS). R2 refuses
      the one database we know is production; R8 refuses everything we have not
      approved, including copies of production nobody listed (2026-10-15: the
      incident's recovery branch was a full copy that R2 did not know about).
      Production-derived copies we do know (PRODUCTION_DERIVED_DB_ENDPOINTS)
      get production's protection everywhere (R2/R3/R5). "Hosted" here means
      Render's own markers (RENDER / RENDER_SERVICE_ID): the threat is a copy
      of the Render service, not a local process that sets the head flag.
  R9  Connection strings libpq reads differently from Python are refused: more
      than one host (libpq tries each in turn), more than one '@', a
      percent-encoded host, and Neon's `options=endpoint=...` (or PGOPTIONS),
      password-prefix (`endpoint=<id>;<password>`) and database-name routing,
      each of which picks the endpoint without the hostname.

Nothing here ever puts a password in an error message: errors name the
endpoint and the rule, never the URL.
"""
import logging
import os
import re
import urllib.parse
from dataclasses import dataclass
from typing import Mapping, Optional

# Not secrets: an endpoint ID is the first label of a hostname, a service ID is
# a dashboard identifier. Changing either is a deliberate, reviewed code change.
PRODUCTION_DB_ENDPOINTS = frozenset({'ep-odd-star-ajye3lag'})
PRODUCTION_RENDER_SERVICE_IDS = frozenset({'srv-d8f1hh6rnols73ak3a10'})   # streakfit-api
# Copies of production data. Never a staging database; production itself may use
# one only through the explicit recovery override (R4).
PRODUCTION_DERIVED_DB_ENDPOINTS = frozenset({
    'ep-dawn-fire-aj4zfzmz',      # Neon branch br-rough-smoke-ajnojcbg, incident recovery copy 2026-10-13
})
# The ONLY databases a hosted non-production process may use (R8). Adding one is
# a reviewed code change, deliberately not an environment variable: the
# 2026-10-13 incident was one pasted value, and an allowlist the same paste
# could widen would not have stopped it.
STAGING_DB_ENDPOINTS = frozenset({
    'ep-young-sound-b4qsoqe8',    # Neon project round-rain-13734596 (streakfit-staging-pw1), empty at creation
})

DEPLOYMENTS = ('production', 'staging', 'development')
DEPLOYMENT_VAR = 'STREAKFIT_DEPLOYMENT'
LOCAL_PRODUCTION_OVERRIDE_VAR = 'STREAKFIT_ALLOW_PRODUCTION_DB_FROM_HERE'
RECOVERY_ENDPOINT_VAR = 'STREAKFIT_PRODUCTION_DB_ENDPOINT'
SQLITE_FALLBACK = 'sqlite:///streakfit.db'

_ROUTING_QUERY_KEYS = ('host', 'hostaddr', 'service')
_ROUTING_ENV_VARS = ('PGHOSTADDR', 'PGSERVICE', 'PGSERVICEFILE')


# Neon's SNI-less routing: `endpoint=<id>` (or the older `project=<id>`) inside the
# startup options picks the endpoint regardless of the hostname. A GUC such as
# `-c neon.endpoint_id=...` is not routing and is not matched.
_NEON_ROUTING_OPTION = re.compile(r'(?<![\w.])(?:endpoint|project)\s*=', re.IGNORECASE)


def _url_shape_problem(url: str, env: Mapping[str, str]) -> Optional[str]:
    """R9: why libpq could read this URL differently from urlsplit, or None."""
    parts = urllib.parse.urlsplit(url)
    if parts.netloc.count('@') > 1:
        return "it contains more than one '@'"
    hostport = parts.netloc.rpartition('@')[2]
    if ',' in hostport:
        return 'it names more than one host (libpq would try each in turn)'
    if '%' in hostport:
        return 'its host is percent-encoded'
    options = ' '.join(urllib.parse.parse_qs(parts.query).get('options', []))
    if _NEON_ROUTING_OPTION.search(options) or _NEON_ROUTING_OPTION.search(env.get('PGOPTIONS') or ''):
        return "it selects a Neon endpoint through options/PGOPTIONS rather than the host"
    # Neon's other SNI-less routes (its "connect from old clients" docs): the
    # endpoint at the start of the password (`endpoint=<id>;<password>`, or `$`),
    # or key=value text in the database name. Never quoted back: it is the password.
    password = urllib.parse.unquote(parts.password or '')
    if password.lower().startswith(('endpoint=', 'project=')):
        return 'its password selects a Neon endpoint rather than the host'
    if _NEON_ROUTING_OPTION.search(urllib.parse.unquote(parts.path)):
        return 'its database name selects a Neon endpoint rather than the host'
    return None


class DatabaseBindingError(RuntimeError):
    """This process must not use the database it was given."""


@dataclass(frozen=True)
class Binding:
    url: str                    # what SQLAlchemy should use (never logged)
    deployment: str             # production | staging | development
    endpoint: Optional[str]     # Neon endpoint ID, or None for anything else
    hosted: bool
    service_id: Optional[str]
    declared: bool = False      # STREAKFIT_DEPLOYMENT was set explicitly

    def describe(self) -> str:
        """Safe to log: no URL, no credentials."""
        return (f"deployment={self.deployment} endpoint={self.endpoint or 'non-neon'} "
                f"hosted={self.hosted} service={self.service_id or '-'}")


def neon_endpoint(url: str) -> Optional[str]:
    """The Neon endpoint ID in URL's host (`ep-x` or `ep-x-pooler` -> `ep-x`),
    or None when the host is not a Neon host."""
    host = (urllib.parse.urlsplit(url).hostname or '').lower().rstrip('.')
    if not (host.endswith('.neon.tech') and '.' in host):
        return None
    first = host.split('.', 1)[0]
    if first.endswith('-pooler'):
        first = first[:-len('-pooler')]
    return first or None


def _is_hosted(env: Mapping[str, str]) -> bool:
    return bool(env.get('RENDER') or env.get('RENDER_SERVICE_ID')
                or env.get('STREAKFIT_ENFORCE_DB_HEAD') == '1')


def _declared_deployment(env: Mapping[str, str]) -> Optional[str]:
    value = (env.get(DEPLOYMENT_VAR) or '').strip().lower()
    if not value:
        return None
    if value not in DEPLOYMENTS:
        raise DatabaseBindingError(f'{DEPLOYMENT_VAR}={value!r} is not one of {", ".join(DEPLOYMENTS)}')
    return value


def _check_policy(endpoint: Optional[str], deployment: str, hosted: bool,
                  service_id: Optional[str], env: Mapping[str, str], is_postgres: bool = True,
                  declared: bool = True) -> None:
    """R2-R5 for one endpoint. Raises DatabaseBindingError naming the rule."""
    is_prod_db = endpoint in PRODUCTION_DB_ENDPOINTS
    is_prod_data = is_prod_db or endpoint in PRODUCTION_DERIVED_DB_ENDPOINTS
    what = 'production database' if is_prod_db else 'copy of production data'
    on_prod_service = service_id in PRODUCTION_RENDER_SERVICE_IDS
    if service_id and not on_prod_service:
        if is_prod_data:
            raise DatabaseBindingError(
                f'R2: Render service {service_id} is not a production service and may not use the '
                f'{what} ({endpoint})')
        if deployment == 'production':
            raise DatabaseBindingError(
                f'R4: Render service {service_id} is not a production service; set '
                f'{DEPLOYMENT_VAR}=staging (or development) and give it its own database')
    if is_prod_data and declared and deployment != 'production':
        raise DatabaseBindingError(
            f'R3: a {deployment} deployment may not use the {what} ({endpoint})')
    if is_prod_data and hosted and not service_id:
        raise DatabaseBindingError(
            f'R2: the {what} ({endpoint}) requires a Render service ID to prove this is '
            'a production service; RENDER_SERVICE_ID is missing')
    if is_prod_data and not hosted and env.get(LOCAL_PRODUCTION_OVERRIDE_VAR) != '1':
        raise DatabaseBindingError(
            f'R5: this is not a hosted service but DATABASE_URL points at the {what} '
            f'({endpoint}); set {LOCAL_PRODUCTION_OVERRIDE_VAR}=1 if that is really intended')
    on_render = bool(env.get('RENDER') or env.get('RENDER_SERVICE_ID'))
    if on_render and deployment != 'production' and is_postgres and endpoint not in STAGING_DB_ENDPOINTS:
        raise DatabaseBindingError(
            f'R8: a hosted {deployment} process may use only an approved staging database '
            f'({", ".join(sorted(STAGING_DB_ENDPOINTS))}), not {endpoint or "a non-Neon host"}')
    if deployment == 'production' and hosted and is_postgres and not is_prod_db:
        recovery = (env.get(RECOVERY_ENDPOINT_VAR) or '').strip().lower()
        if not (recovery and endpoint == recovery):
            raise DatabaseBindingError(
                f'R4: production must use the production database, not {endpoint or "a non-Neon host"}; '
                f'for a recovery onto a new endpoint set {RECOVERY_ENDPOINT_VAR} to exactly that endpoint')


def resolve(env: Mapping[str, str] = os.environ, url_var: str = 'DATABASE_URL') -> Binding:
    """Decide, from configuration alone, which database this process may use.

    Raises DatabaseBindingError rather than returning something unsafe."""
    raw = (env.get(url_var) or '').strip()
    url = 'postgresql://' + raw[len('postgres://'):] if raw.startswith('postgres://') else raw
    hosted = _is_hosted(env)
    service_id = (env.get('RENDER_SERVICE_ID') or '').strip() or None
    declared = _declared_deployment(env)
    deployment = declared or ('production' if hosted else 'development')

    if not url:
        if hosted:
            raise DatabaseBindingError(
                f'R1: {url_var} is not set on a hosted service; refusing the SQLite fallback')
        return Binding(SQLITE_FALLBACK, deployment, None, False, service_id, declared is not None)

    # An EXPLICIT non-PostgreSQL URL is a deliberate choice (the test suite
    # fakes a hosted process on SQLite); R1 is about the silent fallback.
    is_postgres = urllib.parse.urlsplit(url).scheme.lower().startswith('postgresql')
    if is_postgres:
        query = urllib.parse.parse_qs(urllib.parse.urlsplit(url).query)
        rerouting = [k for k in _ROUTING_QUERY_KEYS if k in query]
        if rerouting:
            raise DatabaseBindingError(f'R6: {url_var} carries {", ".join(rerouting)}= which overrides its host')
        rerouting = [k for k in _ROUTING_ENV_VARS if env.get(k)]
        if rerouting:
            raise DatabaseBindingError(f'R6: {", ".join(rerouting)} is set and would override the host')
        problem = _url_shape_problem(url, env)
        if problem:
            raise DatabaseBindingError(f'R9: {url_var} is refused because {problem}')
        # A URL with no host lets libpq take the host from PGHOST (or a local
        # socket), so the endpoint checks below would judge a host the process
        # never uses. Locally that is a legitimate socket connection; hosted, or
        # with PGHOST set, it is a reroute (overnight audit 2026-10-15).
        if not urllib.parse.urlsplit(url).hostname and (hosted or env.get('PGHOST')):
            raise DatabaseBindingError(
                f'R6: {url_var} names no host, so libpq would choose one '
                f'({"from PGHOST" if env.get("PGHOST") else "a local socket"}); give the host in the URL')

    endpoint = neon_endpoint(url)
    _check_policy(endpoint, deployment, hosted, service_id, env, is_postgres, declared is not None)
    return Binding(url, deployment, endpoint, hosted, service_id, declared is not None)


def server_endpoint(connection) -> Optional[str]:
    """What the server says it is, or None off Neon / off PostgreSQL."""
    if getattr(getattr(connection, 'dialect', None), 'name', None) != 'postgresql':
        return None
    value = connection.exec_driver_sql("select current_setting('neon.endpoint_id', true)").scalar()
    return (value or '').strip().lower() or None


def verify_server(connection, binding: Binding, env: Mapping[str, str] = os.environ) -> Optional[str]:
    """R7: the connected server must be the endpoint the URL named, and pass the
    same policy. Returns the server-reported endpoint (or None)."""
    reported = server_endpoint(connection)
    if reported is None:
        # Off Neon there is nothing to ask. But a HOSTED process whose URL names a
        # Neon endpoint must get an answer: Neon always sets neon.endpoint_id, so
        # silence means the connection did not land where the URL says, and the
        # retention sweeper must not treat this database as proven
        # (overnight audit 2026-10-15).
        if binding.hosted and binding.endpoint is not None:
            raise DatabaseBindingError(
                f'R7: the URL names endpoint {binding.endpoint} but the server did not report one')
        return None
    if binding.endpoint is not None and reported != binding.endpoint:
        raise DatabaseBindingError(
            f'R7: the URL names endpoint {binding.endpoint} but the server reports {reported}')
    _check_policy(reported, binding.deployment, binding.hosted, binding.service_id, env,
                  declared=binding.declared)
    return reported


# --- secret redaction for every log record in the process -------------------

_URL_CREDENTIALS = re.compile(r'(?i)\b([a-z][a-z0-9+.-]*://)([^:/@\s]+):([^@\s]+)@')
_CONNINFO_PASSWORD = re.compile(r'(?i)\b(password\s*=\s*)(\'[^\']*\'|"[^"]*"|\S+)')
_BEARER = re.compile(r'(?i)\b(bearer\s+)[A-Za-z0-9._~+/=-]{8,}')
SECRET_ENV_VARS = ('SECRET_KEY', 'JWT_SECRET_KEY', 'ADMIN_SECRET', 'RESEND_API_KEY', 'ANTHROPIC_API_KEY',
                   'STREAKFIT_EVIDENCE_KEY')
_URL_ENV_VARS = ('DATABASE_URL', 'MIGRATION_DATABASE_URL', 'RATELIMIT_STORAGE_URI')


def _secret_values(env: Mapping[str, str]) -> list:
    values = [env.get(k, '') for k in SECRET_ENV_VARS]
    for k in _URL_ENV_VARS:
        password = urllib.parse.urlsplit(env.get(k, '') or '').password
        if password:
            values += [password, urllib.parse.unquote(password)]
    # Longest first so a secret containing another is masked whole.
    return sorted({v for v in values if v and len(v) >= 8}, key=len, reverse=True)


def redact(text: str, secrets: tuple = ()) -> str:
    text = _URL_CREDENTIALS.sub(r'\1\2:***@', text)
    text = _CONNINFO_PASSWORD.sub(r'\1***', text)
    text = _BEARER.sub(r'\1***', text)
    for value in secrets:
        text = text.replace(value, '***')
    return text


_installed = False


def install_log_redaction(env: Mapping[str, str] = os.environ) -> None:
    """Mask credentials in every log record this process creates, from any
    logger (app, gunicorn, alembic, SQLAlchemy). Idempotent.

    Messages: done in the record factory, not a handler filter, so a handler
    added later by a library still only ever sees redacted text.
    Tracebacks: formatted later, by each handler's Formatter, so the standard
    logging.Formatter.formatException is wrapped too. That covers every
    formatter that inherits it (gunicorn's included). A handler that formats
    exceptions some other way is not covered; the database layer already hides
    bound parameters (hide_parameters=True)."""
    global _installed
    if _installed:
        return
    secrets = tuple(_secret_values(env))
    previous = logging.getLogRecordFactory()

    def factory(*args, **kwargs):
        record = previous(*args, **kwargs)
        try:
            message = record.getMessage()
        except Exception:  # noqa: BLE001 -- a bad format string is the logger's problem, not ours
            return record
        cleaned = redact(message, secrets)
        if cleaned != message:
            record.msg, record.args = cleaned, None
        return record

    logging.setLogRecordFactory(factory)

    original_format_exception = logging.Formatter.formatException

    def format_exception(self, ei):
        return redact(original_format_exception(self, ei), secrets)

    logging.Formatter.formatException = format_exception  # type: ignore[method-assign]
    _installed = True
