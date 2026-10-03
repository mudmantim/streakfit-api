"""RC-B2a D37: a forked worker never reuses a connection its parent opened.

Under `gunicorn --preload` the module is imported in the master; the head check
(and possibly the retention sweeper) connect there before workers fork. A pooled
connection inherited by a child is the SAME socket as the parent's. This pins
the at-fork hook: after os.fork() the child's pool holds nothing it inherited,
and the parent's connection still works (the child did not close it).
"""
import os

from sqlalchemy import text

import app as A


def test_fork_hook_is_registered_and_child_starts_with_an_empty_pool():
    with A.app.app_context():
        with A.db.engine.connect() as c:          # parent: open and pool a connection
            c.execute(text('select 1'))
        assert A.db.engine.pool.checkedin() >= 1
        r, w = os.pipe()
        pid = os.fork()
        if pid == 0:                              # child
            os.close(r)
            try:
                with A.app.app_context():
                    n = A.db.engine.pool.checkedin()
            except Exception:
                n = 99
            os.write(w, str(n).encode())
            os._exit(0)
        os.close(w)
        child_inherited = int(os.read(r, 16) or b'99')
        os.close(r)
        os.waitpid(pid, 0)
        assert child_inherited == 0
        with A.db.engine.connect() as c:          # parent's connection untouched
            assert c.execute(text('select 1')).scalar() == 1


def test_head_check_leaves_no_pooled_connection(monkeypatch):
    with A.app.app_context():
        A.db.engine.dispose()
        from alembic.runtime import migration
        monkeypatch.setattr(migration.MigrationContext, 'get_current_revision', lambda self: 'x')
        from alembic.script import ScriptDirectory
        monkeypatch.setattr(ScriptDirectory, 'get_current_head', lambda self: 'x')
        A._assert_db_at_head()
        assert A.db.engine.pool.checkedin() == 0
