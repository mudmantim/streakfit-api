"""The suite leaves no SQLite file behind in $TMPDIR (2026-10-15).

conftest.py creates one temp database per pytest process. It was never removed:
~2,500 leaked files (~1 GB) filled the per-user quota of a tmpfs /tmp and made
unrelated work fail with "disk quota exceeded". This runs a small pytest
process with a fresh TMPDIR and asserts the directory is empty afterwards,
including a scenario whose subprocess ends with os._exit (which skips atexit).
"""
import os
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent


def test_a_pytest_run_leaves_no_temp_database(tmp_path):
    tmp = tmp_path / 'tmpdir'
    tmp.mkdir()
    env = {**os.environ, 'TMPDIR': str(tmp), 'PYTHONDONTWRITEBYTECODE': '1'}
    env.pop('DATABASE_URL', None)
    p = subprocess.run([sys.executable, '-m', 'pytest', '-q', '-p', 'no:cacheprovider',
                        '--basetemp', str(tmp_path / 'basetemp'), 'tests/test_render_env_probe.py',
                        'tests/test_ratelimit_outage_policy.py::test_a_failure_does_not_restart_the_count'],
                       cwd=REPO, env=env, capture_output=True, text=True, timeout=300)
    assert p.returncode == 0, p.stdout[-2000:] + p.stderr[-2000:]
    left = sorted(f.name for f in tmp.iterdir())
    assert left == [], left
