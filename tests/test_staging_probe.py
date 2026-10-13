"""scripts/staging_probe.py must never be pointed at production."""
import importlib.util
import os

import pytest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _probe():
    spec = importlib.util.spec_from_file_location('staging_probe', os.path.join(REPO, 'scripts', 'staging_probe.py'))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.mark.parametrize('url', ['https://streakfit.pro', 'https://www.streakfit.pro/',
                                 'https://STREAKFIT.PRO', 'http://streakfit-staging.onrender.com'])
def test_the_probe_refuses_production_and_plain_http(monkeypatch, url):
    monkeypatch.setenv('STAGING_PROBE_PASSWORD', 'x')
    assert _probe().main([url, '--username', 'u', '--seconds', '0']) == 2


def test_the_probe_wants_the_password_from_the_environment_only(monkeypatch):
    monkeypatch.delenv('STAGING_PROBE_PASSWORD', raising=False)
    assert _probe().main(['https://streakfit-staging.onrender.com', '--username', 'u', '--seconds', '0']) == 2


STAGING = 'https://streakfit-staging.onrender.com'


def _fake(mod, monkeypatch, shas, health=200):
    """Replace the network: build identity answers each of SHAS in turn."""
    shas = list(shas)

    def call(base, path, method='GET', body=None, token=None, timeout=10):
        if path == '/api/build-identity':
            sha = shas.pop(0) if len(shas) > 1 else shas[0]
            return (200, {'gitSha': sha}) if sha else (0, None)
        if path == '/health':
            return health, None
        if path == '/api/login':
            return 200, {'access_token': 'tok-123'}
        if token == 'revoked':
            return 401, {'error': 'session_ended'}
        return 200, {}
    monkeypatch.setattr(mod, 'call', call)
    monkeypatch.setattr(mod.time, 'sleep', lambda s: None)
    monkeypatch.setenv('STAGING_PROBE_PASSWORD', 'x')


def _run(mod, *extra, rounds=3):
    return mod.main([STAGING, '--username', 'u', '--interval', '0', '--rounds', str(rounds), *extra])


def test_an_issued_token_file_is_owner_only_and_never_overwritten(monkeypatch, tmp_path):
    mod = _probe()
    _fake(mod, monkeypatch, ['a'])
    path = tmp_path / 'device-b.token'
    assert mod.main([STAGING, '--username', 'u', '--issue-token-file', str(path)]) == 0
    assert path.read_text() == 'tok-123'
    assert path.stat().st_mode & 0o777 == 0o600
    assert mod.main([STAGING, '--username', 'u', '--issue-token-file', str(path)]) == 2


def test_a_token_file_others_can_read_is_refused(monkeypatch, tmp_path):
    mod = _probe()
    _fake(mod, monkeypatch, ['a'])
    path = tmp_path / 'device-b.token'
    path.write_text('revoked')
    path.chmod(0o644)
    assert _run(mod, '--revoked-token-file', str(path)) == 2
    path.chmod(0o600)
    assert _run(mod, '--revoked-token-file', str(path)) == 0


def test_the_baseline_must_be_live_before_a_failure_test_starts(monkeypatch):
    mod = _probe()
    _fake(mod, monkeypatch, ['compat'])
    assert _run(mod, '--baseline-sha', 'pw1') == 3


def test_the_baseline_run_passes_only_if_the_baseline_answered_cleanly(monkeypatch, capsys):
    mod = _probe()
    _fake(mod, monkeypatch, ['pw1'])
    assert _run(mod, '--baseline-sha', 'PW1') == 0
    # the failed build took over for a round
    _fake(mod, monkeypatch, ['pw1', 'pw1', 'failed', 'pw1'])
    assert _run(mod, '--baseline-sha', 'pw1', rounds=4) == 1
    # a gap: no answer at all, from the same build
    _fake(mod, monkeypatch, ['pw1'], health=0)
    assert _run(mod, '--baseline-sha', 'pw1') == 1
    # and no answer to identity either (the commit is unknown then)
    _fake(mod, monkeypatch, ['pw1', 'pw1', '', 'pw1'])
    assert _run(mod, '--baseline-sha', 'pw1', rounds=4) == 1
    # a 5xx anywhere
    _fake(mod, monkeypatch, ['pw1'], health=502)
    assert _run(mod, '--baseline-sha', 'pw1') == 1
    assert 'FAIL' in capsys.readouterr().err


def test_a_revoked_session_that_answers_is_a_failure(monkeypatch, tmp_path):
    mod = _probe()
    _fake(mod, monkeypatch, ['pw1'])
    path = tmp_path / 't'
    path.write_text('not-revoked')
    path.chmod(0o600)
    assert _run(mod, '--baseline-sha', 'pw1', '--revoked-token-file', str(path)) == 1
    path.unlink()
    path.write_text('revoked')
    path.chmod(0o600)
    assert _run(mod, '--baseline-sha', 'pw1', '--revoked-token-file', str(path)) == 0
