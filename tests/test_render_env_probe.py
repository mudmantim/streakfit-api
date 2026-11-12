"""scripts/render_env_probe.py: the staging S0.7 test of RENDER_SERVICE_ID in
Pre-Deploy. It must answer the release question correctly, never print a
secret, and never fail the deploy step it precedes."""
import importlib.util
import subprocess
import sys
from pathlib import Path

TOOL = Path(__file__).resolve().parent.parent / 'scripts' / 'render_env_probe.py'
_spec = importlib.util.spec_from_file_location('render_env_probe', TOOL)
probe = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(probe)

PW = 'Zq8v' + 'Kp2Lm9Rt'
STAGING_URL = f'postgresql://neondb_owner:{PW}@ep-fake-staging-00000000.c-2.us-east-2.aws.neon.tech/neondb'


def verdict(env):
    return [line for line in probe.report('pre-deploy', env) if 'production-in-this-phase' in line][0]


def test_service_id_present_means_production_predeploy_would_pass():
    env = {'RENDER': 'true', 'RENDER_SERVICE_ID': 'srv-fakestaging0000000000', 'DATABASE_URL': STAGING_URL,
           'STREAKFIT_DEPLOYMENT': 'staging'}
    assert 'PASS' in verdict(env)


def test_service_id_missing_means_production_predeploy_would_be_refused_by_r2():
    env = {'RENDER': 'true', 'DATABASE_URL': STAGING_URL, 'STREAKFIT_DEPLOYMENT': 'staging'}
    assert 'FAIL (R2' in verdict(env)


def test_no_render_variables_at_all_is_refused_too():
    assert 'FAIL (R5' in verdict({'DATABASE_URL': STAGING_URL})


def test_never_prints_a_secret_or_the_url():
    env = {'RENDER': 'true', 'RENDER_SERVICE_ID': 'srv-fakestaging0000000000', 'DATABASE_URL': STAGING_URL,
           'SECRET_KEY': PW + 'sk', 'JWT_SECRET_KEY': PW + 'jwt', 'ADMIN_SECRET': PW + 'adm',
           'MIGRATION_DATABASE_URL': STAGING_URL}
    out = '\n'.join(probe.report('runtime', env))
    assert PW not in out and 'neondb_owner' not in out and 'postgresql://' not in out
    assert 'DATABASE_URL endpoint=ep-fake-staging-00000000' in out
    assert 'RENDER_SERVICE_ID=srv-fakestaging0000000000' in out


def test_exits_zero_even_when_production_would_be_refused():
    p = subprocess.run([sys.executable, str(TOOL), 'pre-deploy'], capture_output=True, text=True, timeout=60,
                       env={'PATH': '/usr/bin:/bin', 'RENDER': 'true', 'DATABASE_URL': STAGING_URL})
    assert p.returncode == 0, p.stderr
    assert 'RENDER_SERVICE_ID: MISSING' in p.stdout and 'FAIL (R2' in p.stdout
