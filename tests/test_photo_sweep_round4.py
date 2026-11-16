"""PR #23 round-3 review: NEW-1, NEW-2 and a legal hold landing mid-batch.

The behaviour that matters is concurrent and is proven on PostgreSQL by
tests/team_photo_sweep_race.py (HEAP, NB, CRM, CRR, CRT, LHM1-5, ORD). These
SQLite tests pin what one connection can show:

  * the sweep commits ONE TEAM AT A TIME, so a failure part-way keeps the
    teams already finished (whole) and leaves the rest intact for the next
    pass -- nothing is half-erased (NEW-2);
  * a team's erase is split into transactions of at most
    _TEAM_PHOTO_SWEEP_CHUNK photos, and a pass stops at _TEAM_PHOTO_SWEEP_BATCH;
  * account deletion reads (on PostgreSQL: locks) the sender's photos in id
    order before writing any message or moment (NEW-1);
  * multi-row caption-copy writes lock their rows in id order (NEW-1 audit);
  * placing a legal hold enters the legal-hold fence before anything else.

Every test here fails on c484ca8.
"""
import inspect
import re

import pytest
from sqlalchemy import event

import app as A
from app import db, RetentionRun, RETENTION_TEAM_PHOTO
from conftest import auth_headers, register_and_login
from test_team_photo_expiry import _shared


@pytest.fixture()
def three_teams(client):
    """Three teams, each with a captioned and a captionless expired photo."""
    teams = []
    for k in range(3):
        tok = register_and_login(client, f'r4_owner{k}')
        t = client.post('/api/teams', json={'name': f'R4 {k}'},
                        headers=auth_headers(tok)).get_json()['team']
        uid = client.get('/api/me', headers=auth_headers(tok)).get_json()['id']
        fam = {'tok': tok, 'uid': uid, 'tid': t['id']}
        teams.append([_shared(client, fam, caption=f'team {k}'),
                      _shared(client, fam, caption=None)])
    return teams


class _Hooks:
    """Statements and commits on the app's engine, in order; optionally raise
    before the nth statement that `fail_on` matches."""

    def __init__(self, fail_on=None, nth=1):
        self.seen, self.fail_on, self.nth, self.hits = [], fail_on, nth, 0

    def _stmt(self, _conn, _cursor, statement, _params, _context, _many):
        sql = ' '.join(statement.split())
        self.seen.append(sql)
        if self.fail_on and self.fail_on(sql):
            self.hits += 1
            if self.hits == self.nth:
                raise RuntimeError('injected sweep failure')

    def _commit(self, _conn):
        self.seen.append('COMMIT')

    def __enter__(self):
        event.listen(db.engine, 'before_cursor_execute', self._stmt)
        event.listen(db.engine, 'commit', self._commit)
        return self

    def __exit__(self, *exc):
        event.remove(db.engine, 'before_cursor_execute', self._stmt)
        event.remove(db.engine, 'commit', self._commit)


def _state(pid):
    """'erased' (pixels, caption and message body all gone), 'intact' (all
    there), or 'HALF' (anything else)."""
    db.session.expire_all()
    p = db.session.get(A.TeamPhoto, pid)
    body = A.TeamMessage.query.filter_by(photo_id=pid).one().body
    if p.image_data is None and p.byte_size == 0 and p.caption is None and body == '':
        return 'erased'
    if p.image_data is not None and p.byte_size != 0 and body == (p.caption or ''):
        return 'intact'
    return 'HALF'


def _sweep_step():
    return A._run_sweeper_step('team photo sweep', A._sweeper_team_photo_step,
                               lambda exc: A._record_retention_failure(
                                   RETENTION_TEAM_PHOTO, A.SOURCE_THREAD, exc))


def _last_run():
    db.session.expire_all()
    r = RetentionRun.query.filter_by(kind=RETENTION_TEAM_PHOTO).order_by(
        RetentionRun.id.desc()).first()
    return (r.outcome, r.error_type, r.deleted) if r else None


def test_a_failure_in_the_second_team_keeps_the_first_team_erased_and_the_rest_intact(
        client, three_teams):
    """NEW-2: the failure is injected at team 2's message write, after its
    photo write. On c484ca8 the whole 500-photo batch rolled back, so team 1
    lost its erasure too; now team 1 is committed whole, team 2 rolls back
    whole, team 3 was never started, and the next pass finishes."""
    t1, t2, t3 = three_teams
    with _Hooks(lambda s: s.upper().startswith('UPDATE TEAM_MESSAGE'), nth=2):
        assert _sweep_step() is False
    assert _last_run()[:2] == ('failed', 'RuntimeError')
    assert [_state(p) for p in t1] == ['erased', 'erased']
    assert [_state(p) for p in t2 + t3] == ['intact'] * 4
    assert _sweep_step() is True
    assert [_state(p) for p in t1 + t2 + t3] == ['erased'] * 6
    assert _last_run() == ('ok', None, 4)


def test_the_sweep_commits_after_each_team_and_never_holds_two_teams_at_once(
        client, three_teams):
    """Each team's erase is its own transaction: between one team's first
    write and the next team's, a COMMIT."""
    with _Hooks() as h:
        out = A._sweep_expired_team_photos()
    assert out['erased'] == 6
    # Each team_photo UPDATE (one per team) and the commits, in order.
    timeline = []
    for sql in h.seen:
        if sql == 'COMMIT':
            timeline.append('C')
        elif sql.upper().startswith('UPDATE TEAM_PHOTO'):
            timeline.append('W')
    assert timeline.count('W') == 3, h.seen
    assert ''.join(timeline).count('WC') == 3, timeline       # each team's write is committed


def test_a_team_is_erased_in_transactions_of_at_most_the_chunk_size(client, monkeypatch):
    tok = register_and_login(client, 'r4_chunk')
    t = client.post('/api/teams', json={'name': 'R4 chunk'},
                    headers=auth_headers(tok)).get_json()['team']
    fam = {'tok': tok, 'tid': t['id']}
    pids = [_shared(client, fam, caption=f'c{k}' if k % 2 else None) for k in range(5)]
    monkeypatch.setattr(A, '_TEAM_PHOTO_SWEEP_CHUNK', 2, raising=False)
    with _Hooks(lambda s: s.upper().startswith('UPDATE TEAM_MESSAGE'), nth=2):
        assert _sweep_step() is False                     # fails in the 2nd transaction
    assert [_state(p) for p in pids] == ['erased'] * 2 + ['intact'] * 3
    assert _sweep_step() is True
    assert [_state(p) for p in pids] == ['erased'] * 5


def test_a_pass_stops_at_the_batch_size_and_the_next_pass_goes_on(client, three_teams, monkeypatch):
    monkeypatch.setattr(A, '_TEAM_PHOTO_SWEEP_BATCH', 3)
    monkeypatch.setattr(A, '_TEAM_PHOTO_SWEEP_CHUNK', 2, raising=False)
    with _Hooks() as h:
        assert A._sweep_expired_team_photos()['erased'] == 3
    assert h.seen.count('COMMIT') >= 2          # team 1 (2 photos), then 1 of team 2
    allp = [p for ps in three_teams for p in ps]
    assert sorted(_state(p) for p in allp) == ['erased'] * 3 + ['intact'] * 3
    assert A._sweep_expired_team_photos()['erased'] == 3
    assert [_state(p) for p in allp] == ['erased'] * 6


def test_account_deletion_reads_the_senders_photos_in_id_order_first(client):
    """NEW-1: the sender's photos are read -- on PostgreSQL, locked FOR NO KEY
    UPDATE -- in ONE statement ordered by id, before any message or moment is
    written. c484ca8 read them with no ORDER BY (heap order) and wrote each
    photo, its message and moment in turn, which deadlocked with the sweep."""
    tok = register_and_login(client, 'r4_sender')
    t = client.post('/api/teams', json={'name': 'R4 sender'},
                    headers=auth_headers(tok)).get_json()['team']
    fam = {'tok': tok, 'tid': t['id']}
    _shared(client, fam, caption='one', age_days=1)
    _shared(client, fam, caption='two', age_days=1)
    with _Hooks() as h:
        r = client.delete('/api/me', json={'password': 'WalkTest123!'}, headers=auth_headers(tok))
    assert r.status_code == 200, r.get_json()
    reads = [i for i, s in enumerate(h.seen)
             if s.startswith('SELECT team_photo.id') and 'WHERE team_photo.sender_user_id = ?' in s]
    assert reads, h.seen
    first = reads[0]
    assert h.seen[first].endswith('ORDER BY team_photo.id'), h.seen[first]
    first_copy_write = next(i for i, s in enumerate(h.seen)
                            if s.upper().startswith(('UPDATE TEAM_MESSAGE', 'UPDATE TEAM_MOMENT')))
    assert first < first_copy_write


def test_account_deletion_locks_the_senders_photos_on_postgresql():
    src = inspect.getsource(A._delete_user_account_once)
    i = src.index('_scrub_photo_captions(')
    assert '_for_no_key_update(db.select(TeamPhoto)' in src[i:i + 400]
    assert '.order_by(TeamPhoto.id)' in src[i:i + 400]


def test_multi_row_caption_copy_writes_lock_in_id_order(client):
    """NEW-1 audit: the residue command and the moment scrub write through
    _update_rows_in_id_order (ORDER BY id FOR NO KEY UPDATE on PostgreSQL)."""
    from sqlalchemy.dialects import postgresql
    stmt = A._for_no_key_update(
        db.select(A.TeamMoment.id).where(A.TeamMoment.team_id == 1).order_by(A.TeamMoment.id),
        A.TeamMoment)
    assert str(stmt.compile(dialect=postgresql.dialect())).endswith(
        'ORDER BY team_moment.id FOR NO KEY UPDATE OF team_moment')
    with _Hooks() as h:
        client.application.test_cli_runner().invoke(args=['photo-caption-scrub', '--execute'])
    writes = [s for s in h.seen if s.upper().startswith('UPDATE TEAM_')]
    assert len(writes) == 3, writes
    assert all(re.search(r'WHERE team_\w+\.id IN \(SELECT team_\w+\.id FROM .* ORDER BY team_\w+\.id\)$', s)
               for s in writes), writes
    for fn in (A._scrub_photo_caption_copies, A._erase_expired_photos_of_team):
        assert '_update_rows_in_id_order(TeamMoment' in inspect.getsource(fn)


def test_placing_a_legal_hold_enters_the_fence_before_anything_else():
    """The legal-hold guarantee (see _sweep_expired_team_photos): the hold's
    transaction takes the fence EXCLUSIVE before any lock or read; every erase
    transaction takes it SHARED first. PostgreSQL proof: LHM1-5."""
    src = inspect.getsource(A.admin_legal_hold)
    assert src.index('_legal_hold_fence(exclusive=True)') < src.index('_load_report_for_action(')
    erase = inspect.getsource(A._erase_expired_photos_of_team)
    body = erase[erase.index('"""', erase.index('"""') + 3) + 3:]
    assert body.strip().startswith('_legal_hold_fence(exclusive=False)')
    sweep = inspect.getsource(A._sweep_expired_team_photos)
    assert 'LEGAL HOLD CONSISTENCY GUARANTEE' in sweep
