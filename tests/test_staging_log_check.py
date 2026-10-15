"""scripts/staging_log_check.py against logs built from the REAL probe output
(scripts/render_env_probe.py) and the real guard log formats (db_guard.describe,
migrations/env.py, app.py), then MUTATED: every mutation of a good log must fail
with the specific finding that names it. Fixture files in
tests/fixtures/staging_rehearsal/ are judged too, so a hand-saved log can be
replayed the same way."""
import importlib.util
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / 'tests' / 'fixtures' / 'staging_rehearsal'


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


sys.path.insert(0, str(ROOT))
probe = _load('render_env_probe_under_test', ROOT / 'scripts' / 'render_env_probe.py')
chk = _load('staging_log_check_under_test', ROOT / 'scripts' / 'staging_log_check.py')
import db_guard  # noqa: E402

EP = 'ep-fake-staging-00000000'
SID = 'srv-fakestaging000000000'          # srv- + 20, like real Render IDs
COMMIT = '8c687350d70d837264ec8ffc87940c50255c21b9'
BRANCH = 'rc/safety-b'
HEAD = 'd4f1b6e2a9c3'
PROD = 'ep-odd-star-ajye3lag'
URL = f'postgresql://neondb_owner:not-a-password@{EP}.c-2.us-east-2.aws.neon.tech/neondb'
POOLED = f'postgresql://neondb_owner:not-a-password@{EP}-pooler.c-2.us-east-2.aws.neon.tech/neondb'
PROD_DUMMY = f'postgresql://nobody:not-a-password@{PROD}.c-2.us-east-2.aws.neon.tech/neondb'
ARGS = dict(endpoint=EP, head=HEAD, service=SID, commit=COMMIT, branch=BRANCH)


def args(**over):
    return SimpleNamespace(**{**ARGS, **over})


def render_env(sid=SID, url=URL, commit=COMMIT, branch=BRANCH, **extra):
    env = {'RENDER': 'true', 'RENDER_SERVICE_NAME': 'streakfit-staging-pw1', 'RENDER_SERVICE_TYPE': 'web',
           'RENDER_GIT_COMMIT': commit, 'RENDER_GIT_BRANCH': branch, 'IS_PULL_REQUEST': 'false',
           'STREAKFIT_DEPLOYMENT': 'staging', **extra}
    if url:
        env['DATABASE_URL'] = url
    if sid:
        env['RENDER_SERVICE_ID'] = sid
    return {k: v for k, v in env.items() if v is not None}


def binding(env):
    return db_guard.resolve(env)


def pre_block(**kw):
    return probe.report('pre-deploy', render_env(**kw))


def run_env(**kw):
    return render_env(STREAKFIT_ENFORCE_DB_HEAD='1', RENDER_INSTANCE_ID='srv-fakestaging000000000-abcde', **kw)


def run_block(**kw):
    return probe.report('runtime', run_env(**kw))


def migrating(env=None, server=EP):
    b = binding(env or render_env())
    return f'INFO  [alembic.env] Migrating database: {b.describe()} (server endpoint {server})'


def identity(env=None, server=EP):
    b = binding(env or run_env())
    return f'INFO in app: Database identity verified ({b.describe()}, server endpoint {server}).'


HEAD_LINE = f'INFO in app: Database migration check passed (at head {HEAD}).'


def good(url=URL):
    return (pre_block(url=url) + [migrating(render_env(url=url)), 'INFO  [alembic.runtime.migration] '
                                  f'Running upgrade  -> {HEAD}, daily mission']
            + run_block(url=url) + [identity(run_env(url=url)), HEAD_LINE])


def refusal(rule, **kw):
    if rule == 'R2':
        env = render_env(url=PROD_DUMMY, **kw)
    else:
        env = render_env(url='', **kw)
    try:
        db_guard.resolve(env)
        msg = 'accepted'
    except db_guard.DatabaseBindingError as exc:
        msg = str(exc)
    return probe.report('pre-deploy', env) + [f'CRITICAL [alembic.env] Refusing to start: {msg}']


def fails(lines, mode='deploy', **over):
    return chk.evaluate(mode, lines, args(**over)).failed_ids()


def stamped(lines):
    return [f'2026-10-20T12:00:{n % 60:02d}.123Z  {ln}' for n, ln in enumerate(lines)]


# --- the tool and the probe cannot drift apart ------------------------------

def test_tool_knows_exactly_the_keys_the_probe_prints():
    assert chk.SHOWN == probe.SHOWN and chk.PRESENCE_ONLY == probe.PRESENCE_ONLY
    assert chk.PROD_ENDPOINT in db_guard.PRODUCTION_DB_ENDPOINTS
    assert chk.PROD_SERVICE in db_guard.PRODUCTION_RENDER_SERVICE_IDS


def test_guard_messages_used_by_the_fixtures_are_the_real_ones():
    assert refusal('R2')[-1].endswith(
        f'R2: Render service {SID} is not a production service and may not use the production database ({PROD})')
    assert 'R1: DATABASE_URL is not set on a hosted service' in refusal('R1')[-1]


# --- good logs pass ---------------------------------------------------------

@pytest.mark.parametrize('wrap', [lambda x: x, stamped])
def test_good_logs_pass(wrap):
    assert fails(wrap(good())) == []
    assert fails(wrap(good(url=POOLED))) == []          # the pooled-string deploy (step 8b)
    assert fails(wrap(refusal('R2')), 'r2') == []
    assert fails(wrap(refusal('R1')), 'r1') == []


def test_render_checkout_line_with_the_right_commit_passes():
    assert fails([f'==> Checking out commit {COMMIT[:7]} in branch {BRANCH}'] + good()) == []


# --- mutations: each must fail, and with the finding that names it -----------

def _swap_blocks(log):
    pre_end = next(i for i, ln in enumerate(log) if 'production-in-this-phase' in ln) + 1
    run_at = next(i for i, ln in enumerate(log) if 'phase=runtime' in ln)
    run_end = run_at + len(run_block())
    return log[run_at:run_end] + log[pre_end:run_at] + log[:pre_end] + log[run_end:]


N_PRE = len(pre_block())
WRONG_COMMIT = '1d7b2f2d48b36beccc21f09cfc88675cac11629a'
OTHER_SID = 'srv-otherservice00000000'

MUTATIONS = {
    # --- kept from s07_log_check.py ---
    'runtime block before pre-deploy block': (_swap_blocks, {'MIG-ORDER'}),
    'identity check before the runtime probe': (
        lambda g: g[:N_PRE + 2] + [identity()] + [ln for ln in g[N_PRE + 2:] if 'identity verified' not in ln],
        {'ID-ORDER'}),
    'head check before identity check': (lambda g: g[:-2] + [g[-1], g[-2]], {'HEAD-ONE'}),
    'two deploys in one log': (lambda g: g + g, {'ONE-DEPLOY'}),
    'second migration line': (lambda g: g + [migrating()], {'MIG-ONE'}),
    'second identity line': (lambda g: g + [identity()], {'ID-ONE'}),
    'duplicate key inside the pre-deploy block': (
        lambda g: g[:3] + [f'  RENDER_SERVICE_ID={OTHER_SID}'] + g[3:], {'PRE-UNAMBIGUOUS', 'PRE-SID'}),
    'malformed service ID': (lambda g: [ln.replace(SID, 'srv-x') for ln in g], {'PRE-SID', 'PRE-SERVICE'}),
    'empty service ID in migration': (
        lambda g: [ln.replace(f'service={SID} (', 'service=- (') for ln in g], {'MIG-SID'}),
    'service ID differs at runtime': (
        lambda g: g[:N_PRE + 2] + run_block(sid=OTHER_SID) + g[N_PRE + 2 + len(run_block()):],
        {'RUN-SID', 'RUN-SERVICE'}),
    'no identity line': (lambda g: [ln for ln in g if 'identity verified' not in ln], {'ID-ONE', 'HEAD-ONE'}),
    'server endpoint not reported at migration': (
        lambda g: [migrating(server='not reported') if 'Migrating' in ln else ln for ln in g], {'MIG-SERVER'}),
    'server reports a different endpoint at boot': (
        lambda g: [identity(server='ep-other-thing-11111111') if 'identity' in ln else ln for ln in g],
        {'ID-STAGING'}),
    'no service ID in Pre-Deploy (missing PRE-SID)': (
        lambda g: pre_block(sid=None) + g[N_PRE:], {'PRE-SID', 'PRE-VERDICT', 'PRE-SERVICE'}),
    'truncated before boot': (lambda g: g[:-2], {'ID-ONE', 'HEAD-ONE'}),
    'truncated inside the pre-deploy block': (lambda g: g[:3], {'ONE-DEPLOY'}),
    'migration ran as production': (
        lambda g: [ln.replace('deployment=staging', 'deployment=production') if 'Migrating' in ln else ln
                   for ln in g], {'MIG-STAGING'}),
    'production endpoint mentioned anywhere': (lambda g: g + [f'note {PROD}'], {'NO-PROD'}),
    'URL with password': (lambda g: g + ['oops ' + URL], {'NO-CREDENTIAL'}),
    'conninfo password': (lambda g: g + ['host=x dbname=y password=' + 'Zq8v' + 'Kp2Lm9Rt'], {'NO-CREDENTIAL'}),
    'Neon password': (lambda g: g + ['npg_' + 'AbCdEf123456GhIj'], {'NO-CREDENTIAL'}),
    'JWT': (lambda g: g + ['Bearer eyJ' + 'hbGciOiJIUzI1NiJ9.eyJ' + 'zdWIiOiI0MiJ9.' + 'a1b2c3d4e5f6'],
            {'NO-CREDENTIAL'}),
    'secret variable value': (lambda g: g + ['JWT_SECRET_KEY=' + 'a1b2c3d4' * 4], {'NO-CREDENTIAL'}),
    # --- (v2) build identity ---
    'wrong commit in both phases (main deployed instead)': (
        lambda g: [ln.replace(COMMIT, WRONG_COMMIT) for ln in g], {'PRE-COMMIT', 'RUN-COMMIT'}),
    'short commit printed': (
        lambda g: [ln.replace(COMMIT, COMMIT[:7]) for ln in g], {'PRE-COMMIT', 'RUN-COMMIT'}),
    'commit differs only at runtime': (
        lambda g: g[:N_PRE + 2] + run_block(commit=WRONG_COMMIT) + g[N_PRE + 2 + len(run_block()):],
        {'RUN-COMMIT'}),
    'commit missing from the probe': (
        lambda g: [ln.replace(f'RENDER_GIT_COMMIT={COMMIT}', 'RENDER_GIT_COMMIT: MISSING') for ln in g],
        {'PRE-COMMIT', 'RUN-COMMIT'}),
    'wrong branch (main)': (
        lambda g: [ln.replace(f'RENDER_GIT_BRANCH={BRANCH}', 'RENDER_GIT_BRANCH=main') for ln in g],
        {'PRE-BRANCH', 'RUN-BRANCH'}),
    'Render checkout line names another commit': (
        lambda g: ['==> Checking out commit 1d7b2f2 in branch main'] + g, {'CHECKOUT'}),
    'wrong (but well-formed) service in both phases': (
        lambda g: [ln.replace(SID, OTHER_SID) for ln in g], {'PRE-SERVICE', 'RUN-SERVICE'}),
    # --- (v2) environment the rehearsal requires ---
    'deployment not declared': (
        lambda g: [ln.replace('STREAKFIT_DEPLOYMENT=staging', 'STREAKFIT_DEPLOYMENT: MISSING') for ln in g],
        {'PRE-DECL', 'RUN-DECL'}),
    'sweeper enabled at runtime': (
        lambda g: [ln.replace('STREAKFIT_RETENTION_SWEEPER: MISSING', 'STREAKFIT_RETENTION_SWEEPER=1')
                   if i > N_PRE else ln for i, ln in enumerate(g)], {'RUN-NO-SWEEPER'}),
    'head enforcement off at runtime': (
        lambda g: [ln.replace('STREAKFIT_ENFORCE_DB_HEAD=1', 'STREAKFIT_ENFORCE_DB_HEAD: MISSING') for ln in g],
        {'RUN-HEAD-FLAG'}),
    'MIGRATION_DATABASE_URL set': (
        lambda g: [ln.replace('MIGRATION_DATABASE_URL: MISSING', 'MIGRATION_DATABASE_URL: set') for ln in g],
        {'PRE-NO-OVERRIDE', 'RUN-NO-OVERRIDE'}),
    'local production override set': (
        lambda g: [ln.replace('STREAKFIT_ALLOW_PRODUCTION_DB_FROM_HERE: MISSING',
                              'STREAKFIT_ALLOW_PRODUCTION_DB_FROM_HERE: set') for ln in g],
        {'PRE-NO-OVERRIDE', 'RUN-NO-OVERRIDE'}),
    'runtime names another endpoint': (
        lambda g: [ln.replace(f'DATABASE_URL endpoint={EP}', 'DATABASE_URL endpoint=ep-other-thing-11111111')
                   if i > N_PRE else ln for i, ln in enumerate(g)], {'RUN-EP'}),
    'head check names another revision': (
        lambda g: [ln.replace(HEAD, 'aaaaaaaaaaaa') if 'check passed' in ln else ln for ln in g], {'HEAD-REV'}),
    'head check without revision': (
        lambda g: g[:-1] + ['INFO in app: Database migration check passed'], {'HEAD-REV'}),
    'production service mentioned anywhere': (lambda g: g + ['note srv-d8f1hh6rnols73ak3a10'], {'NO-PROD'}),
    # --- (v2) incomplete / malformed probe blocks ---
    'probe block lost a key': (lambda g: [ln for ln in g if 'RENDER_SERVICE_TYPE' not in ln],
                               {'PRE-COMPLETE', 'RUN-COMPLETE'}),
    'stray line inside the pre-deploy block': (
        lambda g: g[:4] + ['==> some other output'] + g[4:], {'PRE-COMPLETE'}),
    'pre-deploy verdict missing': (
        lambda g: [ln for i, ln in enumerate(g) if not (i < N_PRE and 'production-in-this-phase' in ln)],
        {'PRE-COMPLETE', 'PRE-VERDICT'}),
    'probe crashed in pre-deploy': (
        lambda g: ['render_env_probe phase=pre-deploy error=ValueError'] + g[N_PRE:],
        {'PRE-COMPLETE', 'PRE-SID', 'PRE-COMMIT'}),
    'unknown phase block': (lambda g: probe.report('build', render_env()) + g, {'ONE-DEPLOY'}),
    # --- (v2) more credential shapes ---
    'GitHub token': (lambda g: g + ['ghp_' + 'A1b2C3d4E5f6G7h8I9j0K1'], {'NO-CREDENTIAL'}),
    'private key header': (lambda g: g + ['-----BEGIN RSA PRIV' + 'ATE KEY-----'], {'NO-CREDENTIAL'}),
    'bearer token': (lambda g: g + ['Authorization: Bearer ' + 'abcDEF123456ghiJKL7890'], {'NO-CREDENTIAL'}),
}


@pytest.mark.parametrize('name', MUTATIONS)
def test_every_mutation_fails_with_its_finding(name):
    mutate, expected = MUTATIONS[name]
    got = set(fails(mutate(good())))
    assert expected <= got, (name, sorted(got))


def test_refusal_mutations():
    assert 'SETUP' in fails(pre_block() + refusal('R2')[-1:], 'r2')           # real URL still set
    assert 'REFUSED' in fails(refusal('R2') + refusal('R2')[-1:], 'r2')       # two refusals
    assert 'REFUSED' in fails(refusal('R1'), 'r2')                            # wrong rule
    assert 'NO-MIGRATION' in fails(refusal('R2') + ['Running upgrade -> x'], 'r2')
    assert 'NOT-BOOTED' in fails(refusal('R2') + ['Database migration check passed'], 'r2')
    assert 'ORDER' in fails(refusal('R2')[-1:] + refusal('R2')[:-1], 'r2')
    # (v2) the replay must be the same build, branch and service
    assert {'PRE-COMMIT'} <= set(fails(refusal('R2', commit=WRONG_COMMIT), 'r2'))
    assert {'PRE-BRANCH'} <= set(fails(refusal('R1', branch='main'), 'r1'))
    assert {'PRE-SERVICE'} <= set(fails(refusal('R2', sid=OTHER_SID), 'r2'))


def test_r2_replay_without_a_service_id_is_refused_by_r3_and_fails():
    """No RENDER_SERVICE_ID in Pre-Deploy: the guard still refuses, but by R3
    (declared staging on the production database), not R2. The checker must not
    accept that as the R2 replay, because it would hide the open question."""
    log = refusal('R2', sid=None)
    assert 'Refusing to start: R3' in log[-1]
    assert {'REFUSED', 'PRE-SERVICE'} <= set(fails(log, 'r2'))


def test_r2_of_the_missing_id_kind_is_not_accepted_as_the_replay():
    """An R2 refusal that says the service ID is MISSING (not "service X is not a
    production service") proves the opposite of what the replay must show. Only
    NAMES-SID can tell the two R2 wordings apart."""
    env = render_env(url=PROD_DUMMY)
    del env['STREAKFIT_DEPLOYMENT'], env['RENDER_SERVICE_ID']
    try:
        db_guard.resolve(env)
        raise AssertionError('expected a refusal')
    except db_guard.DatabaseBindingError as exc:
        assert str(exc).startswith('R2: the production database') and 'RENDER_SERVICE_ID is missing' in str(exc)
        line = f'CRITICAL [alembic.env] Refusing to start: {exc}'
    assert fails(refusal('R2')[:-1] + [line], 'r2') == ['NAMES-SID']


def test_r1_replay_with_url_still_set_fails():
    log = pre_block() + ['CRITICAL Refusing to start: R1: DATABASE_URL is not set on a hosted service']
    assert {'SETUP', 'SETUP-NO-URL'} <= set(fails(log, 'r1'))


@pytest.mark.parametrize('over, fid', [
    (dict(endpoint=PROD), 'ARG-EP'), (dict(endpoint='not-an-endpoint'), 'ARG-EP'),
    (dict(service='srv-d8f1hh6rnols73ak3a10'), 'ARG-SID'), (dict(service='srv-x'), 'ARG-SID'),
    (dict(commit='8c68735'), 'ARG-COMMIT'), (dict(commit=None), 'ARG-COMMIT'),
    (dict(branch='main'), 'ARG-BRANCH'), (dict(branch='rc/safety b'), 'ARG-BRANCH'),
    (dict(head='d4f1'), 'ARG-HEAD'), (dict(head=None), 'ARG-HEAD'),
])
def test_bad_expectations_fail_before_judging(over, fid):
    assert fails(good(), **over) == [fid]          # judged nothing else: the log was never read
    assert not chk.evaluate('deploy', good(), args(**over)).ok


def test_empty_log_fails():
    assert fails([]) == ['NONEMPTY']
    assert fails(['', '   ']) == ['NONEMPTY']


def test_report_with_no_findings_is_not_ok():
    assert not chk.Report().ok


# --- CLI: fail closed, never print log text ---------------------------------

def _cli(tmp_path, lines, *extra, mode='deploy'):
    log = tmp_path / 'log.txt'
    log.write_text('\n'.join(lines))
    base = [mode, str(log), '--service', SID, '--commit', COMMIT, '--branch', BRANCH]
    if mode == 'deploy':
        base += ['--endpoint', EP, '--head', HEAD]
    return chk.main(base + list(extra))


def test_cli_pass_and_fail(tmp_path, capsys):
    assert _cli(tmp_path, good()) == 0
    assert 'rehearsal deploy PASSED' in capsys.readouterr().out
    assert _cli(tmp_path, refusal('R2'), mode='r2') == 0
    assert _cli(tmp_path, [ln.replace(COMMIT, WRONG_COMMIT) for ln in good()]) == 1


def test_cli_unreadable_or_bad_arguments_is_unverifiable(tmp_path, capsys):
    assert chk.main(['deploy', str(tmp_path / 'nope.txt'), '--service', SID, '--commit', COMMIT,
                     '--branch', BRANCH, '--endpoint', EP, '--head', HEAD]) == 2
    assert chk.main(['deploy', 'x.txt']) == 2                     # required expectations missing
    assert chk.main(['bogus', 'x.txt', '--service', SID, '--commit', COMMIT, '--branch', BRANCH]) == 2
    out = capsys.readouterr().out
    assert out.count('UNVERIFIABLE') == 3 and 'PASSED' not in out


def test_output_never_contains_log_text(capsys, tmp_path):
    assert _cli(tmp_path, good() + ['oops ' + URL, 'JWT_SECRET_KEY=' + 'a1b2c3d4' * 4]) == 1
    out = capsys.readouterr().out
    for fragment in ('not-a-password', 'a1b2c3d4a1b2', 'neondb_owner', SID, COMMIT, 'Migrating database:'):
        assert fragment not in out, fragment
    assert 'FAIL  NO-CREDENTIAL' in out and '[lines ' in out


# --- saved fixture files ----------------------------------------------------

FIXTURE_EXPECT = {
    'deploy_good.log': ('deploy', set()),
    'deploy_pooled_good.log': ('deploy', set()),
    'r2_good.log': ('r2', set()),
    'r1_good.log': ('r1', set()),
    'deploy_wrong_commit.log': ('deploy', {'PRE-COMMIT', 'RUN-COMMIT'}),
    'deploy_wrong_branch.log': ('deploy', {'PRE-BRANCH', 'RUN-BRANCH'}),
    'deploy_wrong_service.log': ('deploy', {'PRE-SERVICE', 'RUN-SERVICE'}),
    'deploy_missing_pre_sid.log': ('deploy', {'PRE-SID', 'PRE-VERDICT'}),
    'deploy_missing_runtime_phase.log': ('deploy', {'ONE-DEPLOY'}),
    'deploy_truncated.log': ('deploy', {'ID-ONE', 'HEAD-ONE'}),
    'deploy_malformed_block.log': ('deploy', {'PRE-COMPLETE'}),
    'deploy_sweeper_on.log': ('deploy', {'RUN-NO-SWEEPER'}),
    'r2_without_sid_r3.log': ('r2', {'REFUSED'}),
}


def build_fixtures():
    """The fixture files' content (regenerate with: python tests/test_staging_log_check.py)."""
    g = good()
    return {
        'deploy_good.log': stamped(g),
        'deploy_pooled_good.log': stamped(good(url=POOLED)),
        'r2_good.log': stamped(refusal('R2')),
        'r1_good.log': stamped(refusal('R1')),
        'deploy_wrong_commit.log': stamped(MUTATIONS['wrong commit in both phases (main deployed instead)'][0](g)),
        'deploy_wrong_branch.log': stamped(MUTATIONS['wrong branch (main)'][0](g)),
        'deploy_wrong_service.log': stamped(MUTATIONS['wrong (but well-formed) service in both phases'][0](g)),
        'deploy_missing_pre_sid.log': stamped(MUTATIONS['no service ID in Pre-Deploy (missing PRE-SID)'][0](g)),
        'deploy_missing_runtime_phase.log': stamped([ln for i, ln in enumerate(g)
                                                     if not (N_PRE + 2 <= i < N_PRE + 2 + len(run_block()))]),
        'deploy_truncated.log': stamped(g[:-2]),
        'deploy_malformed_block.log': stamped(MUTATIONS['stray line inside the pre-deploy block'][0](g)),
        'deploy_sweeper_on.log': stamped(MUTATIONS['sweeper enabled at runtime'][0](g)),
        'r2_without_sid_r3.log': stamped(refusal('R2', sid=None)),
    }


def test_fixture_files_are_current():
    for name, lines in build_fixtures().items():
        assert (FIXTURES / name).read_text().splitlines() == lines, f'{name} is stale; regenerate'


@pytest.mark.parametrize('name', FIXTURE_EXPECT)
def test_fixture_files_judged(name):
    mode, expected = FIXTURE_EXPECT[name]
    lines = (FIXTURES / name).read_text().splitlines()
    got = set(fails(lines, mode))
    assert (got == set()) if not expected else (expected <= got), (name, sorted(got))


if __name__ == '__main__':
    FIXTURES.mkdir(parents=True, exist_ok=True)
    for fname, content in build_fixtures().items():
        (FIXTURES / fname).write_text('\n'.join(content) + '\n')
    print(f'wrote {len(build_fixtures())} fixtures to {FIXTURES}')
