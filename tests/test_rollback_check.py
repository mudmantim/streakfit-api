"""scripts/rollback_check.py on a throwaway git repository (CI clones are
shallow, so the real release refs are not available there)."""
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
TOOL = REPO / 'scripts' / 'rollback_check.py'


def git(cwd, *args):
    subprocess.run(['git', '-c', 'user.email=t@t', '-c', 'user.name=t', *args], cwd=cwd, check=True,
                   capture_output=True)


def rev(path, name, down, downgrade='pass'):
    (path / 'migrations' / 'versions' / f'{name}.py').write_text(
        f"revision = '{name}'\ndown_revision = {down!r}\n\ndef upgrade():\n    pass\n\n"
        f"def downgrade():\n    {downgrade}\n")


def run(cwd, a, b):
    return subprocess.run([sys.executable, str(TOOL), a, b], cwd=cwd, capture_output=True, text=True)


def test_rollback_verdicts(tmp_path):
    (tmp_path / 'migrations' / 'versions').mkdir(parents=True)
    git(tmp_path, 'init', '-q')
    rev(tmp_path, 'r1', None)
    git(tmp_path, 'add', '.')
    git(tmp_path, 'commit', '-qm', 'one')
    git(tmp_path, 'tag', 'v1')
    git(tmp_path, 'commit', '-q', '--allow-empty', '-m', 'code only')
    git(tmp_path, 'tag', 'v1b')
    rev(tmp_path, 'r2', 'r1', downgrade="op.drop_column('user', 'x')")
    git(tmp_path, 'add', '.')
    git(tmp_path, 'commit', '-qm', 'two')
    git(tmp_path, 'tag', 'v2')

    same = run(tmp_path, 'v1b', 'v1')
    assert same.returncode == 0 and 'REDEPLOY-SAFE' in same.stdout

    back = run(tmp_path, 'v2', 'v1')
    assert back.returncode == 1
    assert 'NOT REDEPLOY-SAFE' in back.stdout and 'DESTRUCTIVE: downgrade of r2' in back.stdout

    forward = run(tmp_path, 'v1', 'v2')
    assert forward.returncode == 0 and 'roll-FORWARD' in forward.stdout
