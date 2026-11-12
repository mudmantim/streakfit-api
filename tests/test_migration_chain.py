"""scripts/build_check.py: the migration chain must be one straight line.

Two heads make `flask db upgrade` and the boot head check fail -- on the
server, at deploy time. This pins the CI-time check that catches it first."""
import importlib.util
import shutil
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent


@pytest.fixture
def build_check(tmp_path, monkeypatch):
    spec = importlib.util.spec_from_file_location('build_check_under_test', REPO / 'scripts' / 'build_check.py')
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    root = tmp_path / 'repo'
    shutil.copytree(REPO / 'migrations', root / 'migrations',
                    ignore=shutil.ignore_patterns('__pycache__'))
    monkeypatch.setattr(mod, 'ROOT', root)
    monkeypatch.setattr(mod, 'failures', [])
    return mod, root


def head_of(root):
    from alembic.config import Config
    from alembic.script import ScriptDirectory
    cfg = Config()
    cfg.set_main_option('script_location', str(root / 'migrations'))
    return ScriptDirectory.from_config(cfg).get_current_head()


def add_revision(root, rev, down):
    (root / 'migrations' / 'versions' / f'{rev}_test.py').write_text(
        f"revision = '{rev}'\ndown_revision = {down!r}\nbranch_labels = None\ndepends_on = None\n\n"
        "def upgrade():\n    pass\n\ndef downgrade():\n    pass\n")


def test_the_real_chain_passes(build_check):
    mod, _ = build_check
    mod.check_migration_chain()
    assert mod.failures == []


def test_a_second_head_fails(build_check):
    mod, root = build_check
    parent = head_of(root)
    add_revision(root, 'aaaa0000test', parent)
    add_revision(root, 'bbbb0000test', parent)       # sibling: two heads, one branch point
    mod.check_migration_chain()
    assert any('2 heads' in f for f in mod.failures), mod.failures


def test_a_merge_point_fails(build_check):
    mod, root = build_check
    parent = head_of(root)
    add_revision(root, 'aaaa0000test', parent)
    add_revision(root, 'bbbb0000test', parent)
    add_revision(root, 'cccc0000test', ('aaaa0000test', 'bbbb0000test'))
    mod.check_migration_chain()
    assert any('not linear' in f for f in mod.failures), mod.failures


def test_a_new_linear_revision_passes(build_check):
    mod, root = build_check
    add_revision(root, 'aaaa0000test', head_of(root))
    mod.check_migration_chain()
    assert mod.failures == []
