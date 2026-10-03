"""RC-B2a: a row lock's strength must not depend on which lock ran first.

SQLAlchemy 2.0's statement cache key omits with_for_update(key_share=...), so
the same select locked two ways shares one compiled entry. The race harness
caught account deletion's FOR UPDATE being emitted as FOR NO KEY UPDATE after a
completion had run in the same process. The fix routes every key_share lock
through _for_no_key_update, which adds `of=` (part of the cache key). This pins
it without PostgreSQL; the end-to-end proof is D31 running after D26-D30 in
tests/test_concurrency_postgres.py.
"""
import inspect
import re

from sqlalchemy.dialects import postgresql

import app as A


def _deletion_lock(uid):        # exactly delete_user_account's statement shape
    return A.db.select(A.User).where(A.User.id == uid).with_for_update()


def _sql(stmt):
    return str(stmt.compile(dialect=postgresql.dialect())).rstrip()


def test_sqlalchemy_cache_key_still_ignores_key_share():
    # Documents the upstream behaviour the workaround exists for. If an
    # upgrade makes this fail, the workaround is harmless and may be dropped.
    ks = A.db.select(A.User).where(A.User.id == 1).with_for_update(key_share=True)
    assert _deletion_lock(1)._generate_cache_key() == ks._generate_cache_key()


def test_user_and_team_locks_cannot_share_a_cache_entry_with_for_update():
    for model in (A.User, A.Team):
        plain = A.db.select(model).where(model.id == 1).with_for_update()
        ks = A._for_no_key_update(A.db.select(model).where(model.id == 1), model)
        assert plain._generate_cache_key() != ks._generate_cache_key()
        assert _sql(plain).endswith('FOR UPDATE')
        assert re.search(r'FOR NO KEY UPDATE OF "?%s"?$' % model.__tablename__, _sql(ks))
    ks_id = A._for_no_key_update(A.db.select(A.User.id).where(A.User.id == 1), A.User)
    plain_id = A.db.select(A.User.id).where(A.User.id == 1).with_for_update()
    assert plain_id._generate_cache_key() != ks_id._generate_cache_key()


def test_key_share_only_via_the_helper_and_plain_locks_never_use_of():
    src = inspect.getsource(A)
    calls = re.findall(r'with_for_update\(([^)]*)\)', src)
    assert calls.count('key_share=True, of=model') == 1, calls
    for args in calls:
        if args == 'key_share=True, of=model':
            continue
        assert 'key_share' not in args and 'of=' not in args, args
