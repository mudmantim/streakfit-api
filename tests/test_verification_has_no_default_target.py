"""The verification tools have no default target (security review P4, 2026-10-15).

Every verification module WRITES to the app it targets (throwaway qa_smoke_*
accounts, a disposable team). Until 2026-10-15 a run with no target went to
https://streakfit.pro, so a forgotten --base-url during staging work silently
wrote to production. Now every entry point stops with exit 2 before its first
request. The subprocesses run with every proxy pointed at a closed local port,
so even a regression here could not reach a real host.
"""
import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / 'scripts'))

from verification._client import resolve_base_url  # noqa: E402

DEAD_PROXY = 'http://127.0.0.1:9'
MODULES = sorted(p.name for p in (REPO / 'scripts' / 'verification').glob('*.py')
                 if not p.name.startswith('_') and 'run_module_standalone(' in p.read_text())


def _no_target_env():
    env = {k: v for k, v in os.environ.items()
           if k not in ('SMOKE_BASE_URL',) and not k.lower().endswith('_proxy')}
    for k in ('http_proxy', 'https_proxy', 'HTTP_PROXY', 'HTTPS_PROXY'):
        env[k] = DEAD_PROXY
    env['no_proxy'] = env['NO_PROXY'] = ''
    return env


def _run(script, *args, env_extra=None):
    env = {**_no_target_env(), **(env_extra or {})}
    return subprocess.run([sys.executable, str(REPO / 'scripts' / script), *args], cwd=REPO, env=env,
                          capture_output=True, text=True, timeout=60)


def test_every_standalone_module_was_found():
    assert len(MODULES) >= 13, MODULES


@pytest.mark.parametrize('script', ['verify_all.py', 'post_deploy_check.py']
                         + [f'verification/{m}' for m in MODULES])
def test_no_target_stops_before_any_request(script):
    p = _run(script)
    assert p.returncode == 2, (p.returncode, p.stdout[-500:], p.stderr[-500:])
    assert 'no default target' in p.stderr
    assert 'streakfit.pro' not in p.stdout          # nothing claimed it as the target
    assert 'Target:' not in p.stdout                 # stopped before starting


def test_a_blank_target_is_no_target():
    p = _run('verify_all.py', env_extra={'SMOKE_BASE_URL': '   '})
    assert p.returncode == 2 and 'no default target' in p.stderr


def test_the_precedence_order_is_unchanged():
    assert resolve_base_url('http://a', 'http://b', 'http://c') == 'http://a'
    assert resolve_base_url(None, 'http://b', 'http://c') == 'http://b'
    assert resolve_base_url(None, None, 'http://c') == 'http://c'
    with pytest.raises(SystemExit) as exc:
        resolve_base_url(None, None, None)
    assert exc.value.code == 2


def test_production_is_still_one_explicit_argument_away():
    assert resolve_base_url(None, 'https://streakfit.pro', None) == 'https://streakfit.pro'
