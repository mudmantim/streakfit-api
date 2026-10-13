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
