"""PR #23 round 7: the independent round-6 review's findings.

  R6-1   the documented post-deploy check (verify_all) must not leave a team
         ownerless, because an ownerless team closes the M1 rollback (the
         downgrade refuses). The ownerless path is verified here instead.
  R6-2   the empty-ownerless-team cleanup selects only teams it may delete,
         and one team that keeps failing neither stops the pass nor hides:
         it is logged by id and the self-check fails while it is stuck.
  copy   the read-only notice names challenge completion and the campfire.

R6-3 (the Leave picker's refusal staying visible) is a browser behaviour,
checked by scripts/uicheck.py ("...the reason stays visible").

SQLite with foreign keys ON, as tests/test_round6_ownerless.py.
"""
import logging
import os
import sys
import uuid

import pytest
from sqlalchemy import event, text

import app as A
from app import db

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                                "scripts"))

from verification import security  # noqa: E402
from verification._client import Results, WsgiClient  # noqa: E402
from verification._fixtures import (Scenario, create_and_join_team, new_run_tag,  # noqa: E402
                                    register_and_login_users)


@pytest.fixture()
def fk_app(app):
    def _on(dbapi_conn, _record):
        dbapi_conn.execute('PRAGMA foreign_keys=ON')
    event.listen(db.engine, 'connect', _on)
    db.session.remove()
    db.engine.dispose()
    try:
        yield app
    finally:
        db.session.remove()
        event.remove(db.engine, 'connect', _on)
        db.engine.dispose()


def _ownerless_count():
    db.session.expire_all()
    return db.session.execute(
        text("SELECT count(*) FROM team WHERE created_by_user_id IS NULL")).scalar()


# ── R6-1: the post-deploy verification leaves nothing ownerless ───────────

def test_the_post_deploy_verification_leaves_no_team_ownerless(fk_app):
    """The reviewer's reproduction, in process: the whole verify_all suite,
    in its documented order (Moderation files pending reports about the smoke
    team before Security runs). Afterwards the M1 downgrade's own guard --
    "SELECT count(*) FROM team WHERE created_by_user_id IS NULL" -- must be 0,
    so the documented rollback is still open."""
    import verify_all
    results = verify_all.run_suite(WsgiClient(fk_app))
    rows = {name: ok for name, ok, _ in results.rows}
    # The reports that keep the smoke team really were filed...
    assert A.Report.query.filter(A.Report.team_id.isnot(None),
                                 A.Report.status == 'pending').count() >= 1
    # ...yet nothing is left ownerless (the reviewer's reproduction on
    # 96b3193: the smoke team, 2 pending reports, created_by_user_id NULL)...
    assert _ownerless_count() == 0
    # ...the ownership checks ran and passed...
    for name in ('security.sole_owner_leave_needs_confirmation',
                 'security.owner_must_choose_before_leaving',
                 'security.handover_to_unconfirmed_age_refused',
                 'security.leave_handing_to_unconfirmed_age_refused',
                 'security.heir_leaves_plainly', 'security.smoke_team_left_owned'):
        assert rows.get(name) is True, (name, rows.get(name))
    # ...and nothing in the production suite makes a team ownerless.
    for name in rows:
        assert 'ownerless_team' not in name and name != 'security.owner_leaves_with_no_owner', name
    assert A.Team.query.filter(A.Team.name.like('Smoke Test %')).count() == 1


def _ownerless_scenario(flask_app):
    api = WsgiClient(flask_app)
    results = Results()
    scenario = Scenario(api, new_run_tag())
    scenario.users = register_and_login_users(api, results, scenario.run_tag)
    create_and_join_team(api, results, scenario, creator_role="a", joiner_roles=("c",))
    return api, results, scenario


def test_the_ownerless_path_is_still_verified_live_against_a_test_database(fk_app):
    """The checks verify_all used to run in production, kept: owner leaves
    with nobody, the team hides its code, refuses a post and a join, and its
    last member deletes it -- leaving nothing ownerless behind here either."""
    api, results, scenario = _ownerless_scenario(fk_app)
    tid, code = scenario.team_id, scenario.invite_code
    security.check_ownerless_team(api, results, scenario, tid, code)
    rows = {name: ok for name, ok, _ in results.rows}
    for name in ('security.owner_leaves_with_no_owner', 'security.ownerless_team_hides_its_code',
                 'security.ownerless_team_is_read_only',
                 'security.ownerless_team_takes_nobody_new',
                 'security.last_member_leaves_and_deletes', 'security.deleted_team_unreachable'):
        assert rows.get(name) is True, (name, rows.get(name))
    assert db.session.get(A.Team, tid) is None
    assert _ownerless_count() == 0


def test_verify_all_never_calls_the_ownerless_checks():
    import verify_all
    src = open(security.__file__, encoding='utf-8').read()
    run_body = src[src.index('def run(api, results, scenario):'):]
    assert 'check_ownerless_team(' not in run_body
    assert 'no_owner' not in src[src.index('def check_team_ownership('):
                                 src.index('def check_ownerless_team(')]
    assert security in [m for _label, m in verify_all.MODULES]


# ── R6-2: the cleanup cannot starve or stall ──────────────────────────────

def _empty_ownerless_team(name):
    t = A.Team(name=name, created_by_user_id=None)
    db.session.add(t)
    db.session.flush()
    db.session.add(A.TeamCampfire(team_id=t.id, total_team_missions=0))
    db.session.add(A.TeamInviteCode(team_id=t.id, code=uuid.uuid4().hex[:6].upper()))
    db.session.commit()
    return t.id


def _pending_report(tid):
    db.session.add(A.Report(public_id=uuid.uuid4().hex, team_id=tid, category='harassment',
                            subject_type='user', status='pending'))
    db.session.commit()


def test_fifty_teams_kept_for_review_do_not_starve_a_deletable_one(fk_app):
    """R6-2(a), the reviewer's scenario: 50 empty ownerless teams under
    pending reports, then one deletable team with a higher id. On 96b3193
    every pass returned {'deleted': 0, 'kept_for_review': 50} forever."""
    kept = []
    for i in range(50):
        tid = _empty_ownerless_team(f'kept-{i}')
        _pending_report(tid)
        kept.append(tid)
    free = _empty_ownerless_team('free')
    assert free > max(kept)
    out = A._reap_empty_ownerless_teams()
    assert out['deleted'] == 1 and out['failed'] == 0, out
    assert out['kept_for_review'] == 50, out
    db.session.expire_all()
    assert db.session.get(A.Team, free) is None
    assert all(db.session.get(A.Team, t) is not None for t in kept)
    assert db.session.execute(text('PRAGMA foreign_key_check')).all() == []


def test_the_selection_never_offers_a_team_a_review_or_hold_keeps(fk_app):
    pending = _empty_ownerless_team('pending')
    _pending_report(pending)
    held = _empty_ownerless_team('held')
    db.session.add(A.Report(public_id=uuid.uuid4().hex, team_id=held, category='harassment',
                            subject_type='user', status='closed', legal_hold=True,
                            legal_hold_reason='test'))
    db.session.commit()
    closed = _empty_ownerless_team('closed')
    db.session.add(A.Report(public_id=uuid.uuid4().hex, team_id=closed, category='harassment',
                            subject_type='user', status='closed'))
    db.session.commit()
    free = _empty_ownerless_team('free')
    assert A._empty_ownerless_team_ids() == [closed, free]
    # The selection and the re-check under the locks use the same rule.
    for tid in (pending, held):
        assert A._team_deletion_refusals(tid) == ["report_pending_or_held"]
    for tid in (closed, free):
        assert A._team_deletion_refusals(tid) == []


def _failing_for(monkeypatch, bad_ids):
    real = A._stage_team_deletion

    def stage(team):
        if team.id in bad_ids:
            raise RuntimeError('injected: this team always fails')
        return real(team)
    monkeypatch.setattr(A, '_stage_team_deletion', stage)


class _Grab(logging.Handler):
    def __init__(self):
        super().__init__()
        self.lines = []

    def emit(self, record):
        self.lines.append(record.getMessage())


@pytest.fixture()
def grab():
    h = _Grab()
    A.app.logger.addHandler(h)
    old = A.app.logger.level
    A.app.logger.setLevel(logging.INFO)
    try:
        yield h
    finally:
        A.app.logger.removeHandler(h)
        A.app.logger.setLevel(old)


def test_one_always_failing_team_does_not_block_the_teams_after_it(fk_app, monkeypatch, grab):
    """R6-2(b), the reviewer's scenario: the lowest-id team's deletion always
    raises. On 96b3193 the exception ended the pass, every pass, so the
    deletable team after it stayed forever."""
    stuck = _empty_ownerless_team('stuck')
    after = _empty_ownerless_team('after')
    _failing_for(monkeypatch, {stuck})
    for _pass in range(3):
        out = A._reap_empty_ownerless_teams()
        db.session.expire_all()
        assert db.session.get(A.Team, after) is None
        assert db.session.get(A.Team, stuck) is not None
        assert out['failed'] == 1, out
    assert out == {'deleted': 0, 'kept_for_review': 0, 'skipped': 0, 'failed': 1}
    # The failing team was rolled back whole, and is named in the log (type only).
    assert A.TeamInviteCode.query.filter_by(team_id=stuck).count() == 1
    assert A.TeamCampfire.query.filter_by(team_id=stuck).count() == 1
    failed = [ln for ln in grab.lines if 'event=ownerless_team_reap_failed' in ln]
    assert failed and all(f'team_id={stuck} ' in ln and 'error=RuntimeError' in ln
                          for ln in failed)
    assert not any('injected' in ln for ln in grab.lines)
    assert db.session.execute(text('PRAGMA foreign_key_check')).all() == []


def test_a_pass_walks_past_a_full_page_of_failing_and_kept_teams(fk_app, monkeypatch):
    """Paging, not just skipping: with a page size of 3, a page of three
    failing teams, and three kept ones (never selected), still reach the team after."""
    bad = {_empty_ownerless_team(f'bad-{i}') for i in range(3)}
    for i in range(3):
        _pending_report(_empty_ownerless_team(f'kept-{i}'))
    last = _empty_ownerless_team('last')
    _failing_for(monkeypatch, bad)
    out = A._reap_empty_ownerless_teams(limit=3)
    assert out == {'deleted': 1, 'kept_for_review': 3, 'skipped': 0, 'failed': 3}, out
    db.session.expire_all()
    assert db.session.get(A.Team, last) is None


def test_a_systemic_failure_stops_the_pass_after_the_failure_cap(fk_app, monkeypatch):
    ids = [_empty_ownerless_team(f'bad-{i}') for i in range(15)]
    _failing_for(monkeypatch, set(ids))
    out = A._reap_empty_ownerless_teams()
    assert out['failed'] == 10 and out['deleted'] == 0, out     # _OWNERLESS_REAP_MAX_FAILURES
    assert A.Team.query.count() == len(ids)


def _sweep_once():
    return A._run_sweeper_step('ownerless team cleanup', A._sweeper_ownerless_team_step,
                               lambda exc: A._record_retention_failure(
                                   A.RUN_OWNERLESS_TEAM_REAP, A.SOURCE_THREAD, exc))


def _check(client):
    checks = {c['id']: c for c in client.get('/api/verification/self').get_json()['checks']}
    return checks['retention.ownerless_teams']


def test_a_stuck_team_is_visible_in_the_run_record_and_the_self_check(fk_app, monkeypatch):
    client = fk_app.test_client()
    assert _check(client)['status'] == 'UNKNOWN'          # never ran
    stuck = _empty_ownerless_team('stuck')
    _empty_ownerless_team('fine')
    _failing_for(monkeypatch, {stuck})
    assert _sweep_once() is True                          # the pass itself ran
    run = A.RetentionRun.query.filter_by(kind=A.RUN_OWNERLESS_TEAM_REAP).one()
    assert run.outcome == 'ok' and run.deleted == 1
    assert run.detail == 'deleted=1 kept_for_review=0 skipped=0 failed=1'
    c = _check(client)
    assert c['status'] == 'FAIL', c
    assert '1 team(s) could not be deleted' in c['observed']
    assert 'team_id' not in c['observed'] and 'injected' not in str(c)   # counts only
    # Fixed: the next pass deletes it, and the check clears.
    monkeypatch.undo()
    assert _sweep_once() is True
    db.session.expire_all()
    assert db.session.get(A.Team, stuck) is None
    assert _check(client)['status'] == 'PASS'


def test_a_pass_that_raises_fails_the_self_check(fk_app, monkeypatch):
    client = fk_app.test_client()

    def broken(*_a, **_k):
        raise RuntimeError('injected: the selection itself fails')
    monkeypatch.setattr(A, '_empty_ownerless_team_ids', broken)
    assert _sweep_once() is False
    c = _check(client)
    assert c['status'] == 'FAIL' and 'RuntimeError' in c['observed'], c


# ── Copy: the read-only notice says what stops ────────────────────────────

def test_the_read_only_notice_names_challenge_completion_and_the_campfire():
    notice = A.TEAM_OWNERLESS_NOTICE
    assert 'start or complete challenges' in notice
    assert 'missions no longer add to its campfire' in notice
    assert 'may stay read-only with no end date' in notice
    for word in ('report about', 'reported', 'investigat', 'suspend', 'moderat', 'review'):
        assert word not in notice.lower(), word
