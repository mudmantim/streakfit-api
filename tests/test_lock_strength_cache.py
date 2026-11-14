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
    helpers = ('key_share=True, of=model',                                  # _for_no_key_update
               'read=True, key_share=True, skip_locked=True, of=model')     # _for_key_share_skip_locked
    for h in helpers:
        assert calls.count(h) == 1, calls
    for args in calls:
        if args in helpers:
            continue
        assert 'key_share' not in args and 'of=' not in args, args


def test_the_photo_sweeps_team_lock_is_key_share_skip_locked_and_its_own_cache_entry():
    """R2-DEF-A: the sweep takes FOR KEY SHARE on its teams -- the weakest
    lock, waited for only by a FOR UPDATE or DELETE of the team -- never
    anything stronger borrowed from a cached entry."""
    ks = A._for_key_share_skip_locked(A.db.select(A.Team.id).where(A.Team.id == 1), A.Team)
    assert re.search(r'FOR KEY SHARE OF "?team"? SKIP LOCKED$', _sql(ks)), _sql(ks)
    for other in (A.db.select(A.Team.id).where(A.Team.id == 1).with_for_update(),
                  A._for_no_key_update(A.db.select(A.Team.id).where(A.Team.id == 1), A.Team)):
        assert other._generate_cache_key() != ks._generate_cache_key()
