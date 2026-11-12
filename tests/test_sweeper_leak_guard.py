"""The conftest guard `no_sweeper_thread_survives_its_test` must fail a test
that leaves a sweeper thread alive, reliably, whether or not that thread can
be stopped. Each case runs a throwaway test file in a separate pytest process
so the leak cannot reach this process."""
import subprocess
import sys
import uuid
from pathlib import Path

import pytest

TESTS = Path(__file__).resolve().parent

CASES = {
    # A real sweeper, started and forgotten: the guard stops it, and still fails the test.
    'stoppable': '''
import app as appmod
def test_leaks_a_real_sweeper(monkeypatch):
    monkeypatch.setattr(appmod, "_RETENTION_THREAD_INTERVAL_S", 0.2)
    appmod._start_retention_sweeper()
''',
    # A sweeper-named thread that ignores its stop request: the join is bounded and the test fails.
    'unstoppable': '''
import threading, time
def test_leaks_a_thread_that_cannot_stop():
    t = threading.Thread(target=time.sleep, args=(30,), name="streakfit-retention-stuck", daemon=True)
    t.stop_event = threading.Event()
    t.start()
''',
    # Control: a test that stops what it starts passes.
    'clean': '''
import app as appmod
def test_stops_what_it_starts(monkeypatch):
    monkeypatch.setattr(appmod, "_RETENTION_THREAD_INTERVAL_S", 0.2)
    t = appmod._start_retention_sweeper()
    t.stop_event.set(); t.join(10)
    assert not t.is_alive()
''',
}


@pytest.mark.parametrize('case', CASES)
def test_the_guard_fails_every_leak_and_only_leaks(case):
    probe = TESTS / f'test_zz_leak_probe_{uuid.uuid4().hex[:8]}.py'
    probe.write_text(CASES[case])
    try:
        p = subprocess.run([sys.executable, '-m', 'pytest', '-q', '-p', 'no:cacheprovider', '-W', 'ignore',
                            str(probe)], cwd=TESTS.parent, capture_output=True, text=True, timeout=120)
    finally:
        probe.unlink()
    out = p.stdout + p.stderr
    if case == 'clean':
        assert p.returncode == 0, out[-1500:]
    else:
        assert p.returncode != 0 and 'still running at the end of this test' in out, out[-1500:]
        if case == 'unstoppable':
            assert 'did not stop within 10 s' in out, out[-1500:]
