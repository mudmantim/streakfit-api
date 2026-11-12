"""scripts/rehearse_rollback.py refuses anything but a local disposable server.

The rehearsal itself needs two git refs and a PostgreSQL server; it was run on
2026-10-13 against PG17 and matched the recorded release history (main ->
6b6e1cd SAFE, PW-1 -> main BLOCKED, PW-1 -> rollback/pw1-compat SAFE). This
pins the safety property: it creates and drops a database and starts real app
processes, so it must never be pointed at a hosted server."""
import subprocess
import sys
from pathlib import Path

import pytest

TOOL = Path(__file__).resolve().parent.parent / 'scripts' / 'rehearse_rollback.py'


@pytest.mark.parametrize('server', [
    'postgresql://u:p@ep-odd-star-ajye3lag.c-2.us-east-2.aws.neon.tech/neondb',
    'postgresql://u:p@db.example.com/postgres',
    'postgresql://u:p@10.0.0.5/postgres',
])
def test_refuses_non_local_servers(server):
    p = subprocess.run([sys.executable, str(TOOL), 'HEAD', 'HEAD', server], capture_output=True, text=True,
                       timeout=60)
    assert p.returncode != 0
    assert 'refusing' in (p.stdout + p.stderr)
    assert ':p@' not in p.stdout + p.stderr


def test_usage_without_arguments():
    p = subprocess.run([sys.executable, str(TOOL)], capture_output=True, text=True, timeout=60)
    assert p.returncode == 2 and 'DISPOSABLE' in p.stderr


def test_rehearsal_processes_declare_a_development_deployment():
    """With db_guard (PR #10), STREAKFIT_ENFORCE_DB_HEAD=1 marks a process as
    hosted, and an undeclared hosted process is production, which R4 forbids
    on a localhost database. Found 2026-10-13 by rehearsing the merged safety
    bundle: the deployed build refused to boot and the verdict was BROKEN."""
    import importlib.util
    spec = importlib.util.spec_from_file_location('rehearse_rollback', TOOL)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    r = object.__new__(mod.Rehearsal)
    r.url = 'postgresql://postgres:x@127.0.0.1:5432/scratch'
    for boot in (False, True):
        env = r._env(boot=boot)
        assert env['STREAKFIT_DEPLOYMENT'] == 'development'
        assert ('STREAKFIT_ENFORCE_DB_HEAD' in env) is boot
        assert 'RENDER' not in env and 'RENDER_SERVICE_ID' not in env
