"""PR #23 follow-up: the independent review's defects D1, D3 and D8, the
reviewer's ad-hoc checks made permanent, and the owner decisions that stay
pending pinned as such.

D2 and D4 (and the join-during-deletion / two-hand-overs races) need two real
connections and run on PostgreSQL: tests/team_owner_race.py, driven by
tests/test_account_deletion_postgres.py.

Decision-pending tests are `xfail(strict=True)` with the decision named in the
reason: they describe what WOULD be true under one answer to a decision that
is Tim's, and they fail today because the code deliberately does not choose.
Strict, so the day somebody changes the behaviour the test turns red and the
decision has to be looked at, not slipped in.
"""
import datetime
import re
import uuid

import pytest
from sqlalchemy import event

import app as A
from app import db
from conftest import auth_headers, register_and_login

import scripts.cleanup_qa_smoke as C

PW = 'WalkTest123!'


@pytest.fixture()
def fk_client(app):
    def _on(dbapi_conn, _record):
        dbapi_conn.execute('PRAGMA foreign_keys=ON')
    event.listen(db.engine, 'connect', _on)
    db.session.remove()
    db.engine.dispose()
    try:
        yield app.test_client()
    finally:
        db.session.remove()
        event.remove(db.engine, 'connect', _on)
        db.engine.dispose()


def _person(client, name):
    tok = register_and_login(client, name, PW)
    uid = client.get('/api/me', headers=auth_headers(tok)).get_json()['id']
    return uid, tok


def _team(client, tok, name='Hill Family'):
    t = client.post('/api/teams', json={'name': name}, headers=auth_headers(tok)).get_json()['team']
    return t['id'], t['invite_code']


def _join(client, tok, tid, code):
    r = client.post(f'/api/teams/{tid}/join', json={'code': code}, headers=auth_headers(tok))
    assert r.status_code == 200, r.get_json()


def _report(tid=None, status='pending', hold=False, subject_type='user', subject_ref=None,
            about=None, by=None):
    rep = A.Report(public_id=uuid.uuid4().hex, team_id=tid, category='harassment',
                   subject_type=subject_type, subject_ref=subject_ref, status=status,
                   legal_hold=hold, legal_hold_reason='test' if hold else None,
                   reported_user_id=about, reporter_user_id=by)
    db.session.add(rep)
    db.session.commit()
    return rep.id


# ── D1: after a sole owner leaves, nothing tells "kept for a report" from "deleted" ──

def _world(client, tag, report):
    """Owner O and member M share a team with a message, a photo and a
    challenge. M leaves (having reported, in the report world); then O, alone,
    leaves with delete_team. Returns what O can still name."""
    o, otok = _person(client, f'd1o_{tag}')
    m, mtok = _person(client, f'd1m_{tag}')
    tid, code = _team(client, otok, f'Fam {tag}')
    _join(client, mtok, tid, code)
    client.post(f'/api/teams/{tid}/messages', json={'body': 'hello'}, headers=auth_headers(otok))
    photo = A.TeamPhoto(public_id=uuid.uuid4().hex, team_id=tid, sender_user_id=o,
                        caption='park', image_data=b'px', byte_size=2,
                        expires_at=datetime.datetime.utcnow() + datetime.timedelta(days=5))
    db.session.add(photo)
    db.session.commit()
    photo_ref = photo.public_id
    ch = client.post(f'/api/teams/{tid}/challenges',
                     json={'preset_key': A.CHALLENGE_PRESETS[0]['key']},
                     headers=auth_headers(otok)).get_json() or {}
    ch_id = (ch.get('challenge') or ch).get('public_id', 'none')
    if report == 'pending':
        _report(tid, status='pending', about=o, by=m)
    elif report == 'hold':
        _report(tid, status='closed', hold=True, about=o, by=m)
    assert client.post(f'/api/teams/{tid}/leave', json={},
                       headers=auth_headers(mtok)).status_code == 200
    r = client.post(f'/api/teams/{tid}/leave', json={'delete_team': True},
                    headers=auth_headers(otok))
    assert r.status_code == 200, r.get_json()
    db.session.expire_all()
    kept = db.session.get(A.Team, tid) is not None
    assert kept == (report is not None)
    return {'o': o, 'otok': otok, 'm': m, 'tid': tid, 'code': code,
            'photo': photo_ref, 'challenge': ch_id, 'tag': tag}


def _requests(w):
    t, h = w['tid'], auth_headers(w['otok'])
    return [
        ('GET teams', 'get', '/api/teams', None),
        ('GET team', 'get', f'/api/teams/{t}', None),
        ('lookup old code', 'get', f"/api/teams/lookup/{w['code']}", None),
        ('join old code', 'post', f'/api/teams/{t}/join', {'code': w['code']}),
        ('join wrong code', 'post', f'/api/teams/{t}/join', {'code': 'ZZZZZZ'}),
        ('leave', 'post', f'/api/teams/{t}/leave', {}),
        ('leave delete_team', 'post', f'/api/teams/{t}/leave', {'delete_team': True}),
        ('leave hand over', 'post', f'/api/teams/{t}/leave', {'new_owner_user_id': w['m']}),
        ('remove member', 'delete', f"/api/teams/{t}/members/{w['m']}", None),
        ('hand over', 'post', f'/api/teams/{t}/owner', {'new_owner_user_id': w['m']}),
        ('hand over self', 'post', f'/api/teams/{t}/owner', {'new_owner_user_id': w['o']}),
        ('rotate invite', 'post', f'/api/teams/{t}/rotate-invite', None),
        ('campfire', 'get', f'/api/teams/{t}/campfire', None),
        ('moments', 'get', f'/api/teams/{t}/moments', None),
        ('messages', 'get', f'/api/teams/{t}/messages', None),
        ('post message', 'post', f'/api/teams/{t}/messages', {'body': 'still here?'}),
        ('post challenge', 'post', f'/api/teams/{t}/challenges',
         {'preset_key': A.CHALLENGE_PRESETS[0]['key']}),
        ('complete challenge', 'post', f"/api/teams/{t}/challenges/{w['challenge']}/complete", {}),
        ('upload photo', 'post', f'/api/teams/{t}/photos', None),
        ('get photo', 'get', f"/api/teams/{t}/photos/{w['photo']}", None),
        ('delete photo', 'delete', f"/api/teams/{t}/photos/{w['photo']}", None),
        ('report about the team', 'post', '/api/reports',
         {'category': 'other', 'subject_type': 'user', 'reported_user_id': w['m'], 'team_id': t}),
    ], h


def _normalise(text, w):
    """Take out what differs only because the two worlds are different rows."""
    text = re.sub(r'\d{4}-\d\d-\d\dT[\d:.]+(\+00:00|Z)?', '<time>', text)
    for key, label in (('code', '<code>'), ('photo', '<photo>'), ('challenge', '<ch>'),
                       ('tag', '<tag>')):
        text = text.replace(str(w[key]), label)
    for key, label in (('tid', '<team>'), ('o', '<me>'), ('m', '<mate>')):
        text = re.sub(rf'\b{w[key]}\b', label, text)
    return text


def _side_effects(w):
    db.session.expire_all()
    t = w['tid']
    inv = A.TeamInviteCode.query.filter_by(team_id=t).one_or_none()
    team = db.session.get(A.Team, t)
    return {
        'invite': None if inv is None else (inv.code == w['code'], inv.rotated_at),
        'owner': None if team is None else team.created_by_user_id,
        'members': A.TeamMembership.query.filter_by(team_id=t).count(),
        'messages_by_me': A.TeamMessage.query.filter_by(sender_user_id=w['o']).count(),
        'challenges_by_me': A.TeamChallenge.query.filter_by(created_by_user_id=w['o']).count(),
        'photos_live': A.TeamPhoto.query.filter_by(team_id=t, deleted_at=None).count(),
        'reports_by_me': A.Report.query.filter_by(reporter_user_id=w['o']).count(),
    }


@pytest.mark.parametrize('report', ['pending', 'hold'])
def test_a_sole_owner_who_left_cannot_tell_a_kept_team_from_a_deleted_one(fk_client, report):
    """PR #23 review D1. Every team endpoint the former owner can call answers
    the same status, the same body and the same headers whether a report kept
    the team or there was none and it was deleted; and none of them changes
    anything in the kept team (no new invite code, no new owner, no row)."""
    c = fk_client
    deleted = _world(c, 'qzgone', None)
    kept = _world(c, 'qzkept', report)
    before = _side_effects(kept)
    assert before['invite'] == (True, None) and before['owner'] == kept['o']

    reqs_d, hd = _requests(deleted)
    reqs_k, hk = _requests(kept)
    diffs = []
    for (name, verb, path_d, body_d), (_n, _v, path_k, body_k) in zip(reqs_d, reqs_k):
        rd = getattr(c, verb)(path_d, json=body_d, headers=hd)
        rk = getattr(c, verb)(path_k, json=body_k, headers=hk)
        # Content-Length is left out of the header comparison only because the
        # bodies themselves are compared exactly (after the same normalising).
        a = (rd.status_code, _normalise(rd.get_data(as_text=True), deleted),
             sorted((k, _normalise(v, deleted)) for k, v in rd.headers.items()
                    if k != 'Content-Length'))
        b = (rk.status_code, _normalise(rk.get_data(as_text=True), kept),
             sorted((k, _normalise(v, kept)) for k, v in rk.headers.items()
                    if k != 'Content-Length'))
        if a != b:
            diffs.append((name, a[0], b[0], a[1] == b[1],
                          sorted(set(a[2]) ^ set(b[2]))))
        assert rk.status_code != 200 or name == 'GET teams', (name, rk.get_json())
    assert not diffs, diffs

    after = _side_effects(kept)
    assert after == before, (before, after)
    # The kept team still belongs to nobody new and is still closed.
    assert after['owner'] == kept['o'] and after['members'] == 0


def test_rotate_on_a_kept_team_never_returns_a_code(fk_client):
    """The reviewer's sharpest probe, on its own: 200 + a fresh invite code."""
    c = fk_client
    w = _world(c, 'r', 'pending')
    r = c.post(f"/api/teams/{w['tid']}/rotate-invite", headers=auth_headers(w['otok']))
    assert r.status_code == 404 and 'invite_code' not in (r.get_json() or {})
    assert A.TeamInviteCode.query.filter_by(team_id=w['tid']).one().code == w['code']


def test_a_team_with_people_in_it_keeps_its_creator_powers(client):
    """Control for D1: only an EMPTY team reads as gone."""
    o, otok = _person(client, 'ctl_o')
    m, mtok = _person(client, 'ctl_m')
    tid, code = _team(client, otok)
    _join(client, mtok, tid, code)
    r = client.post(f'/api/teams/{tid}/rotate-invite', headers=auth_headers(otok))
    assert r.status_code == 200 and r.get_json()['invite_code'] != code


@pytest.mark.parametrize('report', ['pending', 'hold'])
def test_decision_pending_the_export_still_lists_a_photo_kept_for_review(fk_client, report):
    """DOCUMENTED CURRENT BEHAVIOUR -- a residual of D1 left for the owner
    (decisions #5 export/privacy wording and #6 sole-owner leave).

    GET /api/me/data lists the person's own photos. A photo in a team kept
    for a review still exists, so it is listed; deleted with the team, it is
    not. That difference tells the former owner a report kept the team.
    Leaving it out would make the export omit data that is still held about
    the person, which is a transparency / legal choice, not a bug fix. Only
    `photos_shared` differs; everything else in the export is the same."""
    c = fk_client
    deleted = _world(c, 'qzexpg', None)
    kept = _world(c, 'qzexpk', report)
    a = c.get('/api/me/data', headers=auth_headers(deleted['otok'])).get_json()
    b = c.get('/api/me/data', headers=auth_headers(kept['otok'])).get_json()
    differs = sorted(k for k in set(a) | set(b)
                     if _normalise(repr(a.get(k)), deleted) != _normalise(repr(b.get(k)), kept))
    assert differs == ['photos_shared'], differs
    assert a['photos_shared'] == [] and len(b['photos_shared']) == 1


@pytest.mark.parametrize('report', ['pending', 'hold'])
def test_decision_pending_account_deletion_still_answers_differently(fk_client, report):
    """DOCUMENTED CURRENT BEHAVIOUR (owner decision #6, sole-owner leave).

    DELETE /api/me is not a team endpoint and is out of D1's reach: with the
    kept team still owned, the deletion is refused with the generic
    `safety_record` (as for ANY pending report that involves the person),
    while without a report it succeeds. Making these the same means either
    deleting a team under review or never letting the owner go -- a policy
    choice, so it is pinned here, not changed."""
    c = fk_client
    deleted = _world(c, 'xn', None)
    kept = _world(c, 'xk', report)
    rd = c.delete('/api/me', json={'password': PW}, headers=auth_headers(deleted['otok']))
    rk = c.delete('/api/me', json={'password': PW}, headers=auth_headers(kept['otok']))
    assert rd.status_code == 200
    assert rk.status_code == 409 and rk.get_json()['blocker_codes'] == ['safety_record']


# ── D3: a legal hold keeps a photo whatever the held report is about ──────────

def _expired_photo(client, tag):
    o, otok = _person(client, f'd3o_{tag}')
    tid, _code = _team(client, otok, f'D3 {tag}')
    when = datetime.datetime.utcnow() - datetime.timedelta(days=A.PHOTO_RETENTION_DAYS + 2)
    photo = A.TeamPhoto(public_id=uuid.uuid4().hex, team_id=tid, sender_user_id=o,
                        caption='kept for court', image_data=b'pixels', byte_size=6,
                        created_at=when,
                        expires_at=when + datetime.timedelta(days=A.PHOTO_RETENTION_DAYS))
    db.session.add(photo)
    db.session.flush()
    msg = A.TeamMessage(public_id=uuid.uuid4().hex, team_id=tid, sender_type='user',
                        sender_user_id=o, body='kept for court', photo_id=photo.id)
    db.session.add(msg)
    db.session.commit()
    return {'o': o, 'tid': tid, 'photo_id': photo.id, 'photo': photo.public_id,
            'message': msg.public_id}


def _erased(photo_id):
    db.session.expire_all()
    p = db.session.get(A.TeamPhoto, photo_id)
    return p.image_data is None and p.byte_size == 0 and p.caption is None


@pytest.mark.parametrize('about', ['photo', 'message', 'sender', 'team'])
def test_a_legal_hold_on_anything_the_photo_belongs_to_keeps_it(client, about):
    x = _expired_photo(client, about)
    other, _ = _person(client, f'd3x_{about}')
    kw = {'photo': dict(subject_type='photo', subject_ref=x['photo']),
          'message': dict(subject_type='message', subject_ref=x['message']),
          'sender': dict(subject_type='user', about=x['o']),
          'team': dict(subject_type='user', about=other, tid=x['tid'])}[about]
    _report(status='closed', hold=True, **kw)
    out = A._sweep_expired_team_photos()
    db.session.commit()
    assert not _erased(x['photo_id'])
    assert out == {'erased': 0, 'held_by_legal_hold': 1}


def test_a_hold_on_something_unrelated_does_not_keep_it(client):
    x = _expired_photo(client, 'unrel')
    y = _expired_photo(client, 'other')
    _report(tid=y['tid'], status='closed', hold=True, about=y['o'])
    out = A._sweep_expired_team_photos()
    db.session.commit()
    assert _erased(x['photo_id']) and not _erased(y['photo_id'])
    assert out == {'erased': 1, 'held_by_legal_hold': 1}


def test_a_photo_whose_sender_was_deleted_is_still_swept(client):
    """NULL-safety of the hold predicate: NOT(... IN ...) must not be NULL."""
    x = _expired_photo(client, 'nulls')
    _report(status='closed', hold=True, subject_type='user',
            about=_person(client, 'd3_unrelated')[0])
    p = db.session.get(A.TeamPhoto, x['photo_id'])
    p.sender_user_id = None
    db.session.commit()
    A._sweep_expired_team_photos()
    db.session.commit()
    assert _erased(x['photo_id'])


@pytest.mark.xfail(strict=True, reason=(
    "OWNER DECISION #4 pending: whether a merely PENDING (not held) report "
    "about a photo stops the 30-day erasure. Today it does not; only a legal "
    "hold does (PR #23 review D3)."))
def test_decision_pending_a_pending_report_keeps_an_expired_photo(client):
    x = _expired_photo(client, 'pend')
    _report(status='pending', subject_type='photo', subject_ref=x['photo'], tid=x['tid'])
    A._sweep_expired_team_photos()
    db.session.commit()
    assert not _erased(x['photo_id'])


# ── D8: the QA cleanup never touches a team a real person is in ─────────────

def _u(name):
    u = A.User(username=name, password_hash='x')
    db.session.add(u)
    db.session.commit()
    return u.id


def _qa_team(owner, members, owner_in=True, name='Smoke Test run'):
    t = A.Team(name=name, created_by_user_id=owner)
    db.session.add(t)
    db.session.flush()
    for uid in ([owner] if owner_in else []) + list(members):
        db.session.add(A.TeamMembership(team_id=t.id, user_id=uid))
    db.session.commit()
    return t.id


def _snapshot(tid):
    db.session.expire_all()
    t = db.session.get(A.Team, tid)
    return (t is not None, t.created_by_user_id if t else None,
            A.TeamMembership.query.filter_by(team_id=tid).count(),
            A.TeamMessage.query.filter_by(team_id=tid).count(),
            A.TeamPhoto.query.filter_by(team_id=tid).count())


def test_a_real_former_members_chat_and_photos_are_never_deleted(fk_client):
    """QA owner alone in its team, but a real person posted there and left."""
    qa = _u('qa_smoke_d8_owner1')
    real = _u('real_d8_former')
    tid = _qa_team(qa, [])
    db.session.add_all([
        A.TeamMessage(team_id=tid, sender_type='user', sender_user_id=real, body='mine'),
        A.TeamPhoto(public_id=uuid.uuid4().hex, team_id=tid, sender_user_id=real,
                    image_data=b'x', byte_size=1)])
    db.session.commit()
    before = _snapshot(tid)
    safe, blocked, _counts = C.survey()
    assert qa not in {u.id for u in safe}
    assert any(u.id == qa and 'non-qa_smoke_' in r for u, r in blocked)
    C.execute(safe, {})
    rep = A.delete_user_account(qa, dry_run=False, qa_teams_only=True)
    assert rep['blocked'] and 'team_not_qa_only' in rep['blocker_codes']
    assert _snapshot(tid) == before and db.session.get(A.User, qa) is not None


def test_a_qa_team_is_never_handed_to_a_real_member(fk_client):
    """QA owner already left; the fallback would pick a real member."""
    qa = _u('qa_smoke_d8_owner2')
    real = _u('real_d8_member')
    tid = _qa_team(qa, [real], owner_in=False)
    assert A._owned_team_plan(qa)[0]['action'] == A.TEAM_OWNER_TRANSFER
    before = _snapshot(tid)
    safe, blocked, _counts = C.survey()
    assert qa in {u.id for u, _r in blocked} and qa not in {u.id for u in safe}
    C.execute(safe, {})
    assert _snapshot(tid) == before
    assert db.session.get(A.Team, tid).created_by_user_id == qa


def test_user_content_with_its_author_link_cut_counts_as_not_qa(fk_client):
    """Fail closed: a message whose sender was deleted may have been real."""
    qa = _u('qa_smoke_d8_owner3')
    tid = _qa_team(qa, [])
    db.session.add(A.TeamMessage(team_id=tid, sender_type='user', sender_user_id=None,
                                 body='who wrote this?'))
    db.session.commit()
    rep = A.delete_user_account(qa, dry_run=True, qa_teams_only=True)
    assert rep['blocker_codes'] == ['team_not_qa_only']


def test_an_all_qa_team_still_goes_with_its_qa_owner(fk_client):
    """Positive control, mixed fixtures in ONE run: only the all-QA team goes."""
    qa_ok = _u('qa_smoke_d8_ok')
    qa_mate = _u('qa_smoke_d8_mate')
    clean = _qa_team(qa_ok, [])
    db.session.add_all([
        A.TeamMessage(team_id=clean, sender_type='user', sender_user_id=qa_mate, body='qa'),
        A.TeamMessage(team_id=clean, sender_type='rickie', sender_user_id=None, body='hi')])
    qa_bad = _u('qa_smoke_d8_bad')
    real = _u('real_d8_in_mixed')
    mixed = _qa_team(qa_bad, [real], owner_in=False)
    db.session.commit()
    before_mixed = _snapshot(mixed)
    safe, blocked, counts = C.survey()
    assert {qa_ok, qa_mate} <= {u.id for u in safe}
    assert qa_bad in {u.id for u, _r in blocked}
    C.execute(safe, counts)
    db.session.expire_all()
    assert db.session.get(A.Team, clean) is None and db.session.get(A.User, qa_ok) is None
    assert _snapshot(mixed) == before_mixed and db.session.get(A.User, qa_bad) is not None


def test_the_qa_guard_does_not_change_ordinary_deletion(fk_client):
    """Without qa_teams_only the user-facing rules are exactly as before."""
    qa = _u('qa_smoke_d8_plain')
    real = _u('real_d8_plain')
    tid = _qa_team(qa, [real], owner_in=False)
    rep = A.delete_user_account(qa, dry_run=True)
    assert 'team_not_qa_only' not in rep['blocker_codes'] and not rep['blocked']
    assert rep['owned_teams'][0]['new_owner_user_id'] == real and tid


# ── The reviewer's ad-hoc checks, made permanent ─────────────────────────────

def _all_rows(tid, *uids):
    db.session.expire_all()
    rows = {label: A._qa_team_rows(model, attr, tid).count()
            for label, model, attr in A._TEAM_ROW_DELETES}
    rows['team'] = int(db.session.get(A.Team, tid) is not None)
    rows['users'] = sum(db.session.get(A.User, u) is not None for u in uids)
    return rows


def _filled_sole_team(client, tag):
    o, otok = _person(client, f'rb_{tag}')
    tid, _code = _team(client, otok, f'RB {tag}')
    client.post(f'/api/teams/{tid}/messages', json={'body': 'hi'}, headers=auth_headers(otok))
    db.session.add(A.TeamPhoto(public_id=uuid.uuid4().hex, team_id=tid, sender_user_id=o,
                               caption='c', image_data=b'p', byte_size=1))
    db.session.commit()
    client.post(f'/api/teams/{tid}/challenges',
                json={'preset_key': A.CHALLENGE_PRESETS[0]['key']}, headers=auth_headers(otok))
    return o, otok, tid


def _boom_after(monkeypatch, name, calls):
    orig = getattr(A, name)
    n = [0]

    def wrapped(*a, **k):
        n[0] += 1
        out = orig(*a, **k)
        if n[0] >= calls:
            raise RuntimeError('injected failure')
        return out
    monkeypatch.setattr(A, name, wrapped)


def test_a_failure_midway_through_account_deletion_rolls_everything_back(fk_client, monkeypatch):
    """Team staged for deletion, then a failure before the person goes."""
    c = fk_client
    o, otok, tid = _filled_sole_team(c, 'acct')
    before = _all_rows(tid, o)
    assert before['team'] == 1 and before['team_message'] >= 1
    _boom_after(monkeypatch, '_stage_owned_teams_for_account_deletion', 1)
    try:
        r = c.delete('/api/me', json={'password': PW}, headers=auth_headers(otok))
        assert r.status_code == 500
    except RuntimeError:
        pass                                  # TESTING propagates it
    monkeypatch.undo()
    db.session.remove()
    assert _all_rows(tid, o) == before


def test_a_failure_midway_through_team_deletion_on_leave_rolls_back(fk_client, monkeypatch):
    """Half of _TEAM_ROW_DELETES done, then a failure: nothing is gone."""
    c = fk_client
    o, otok, tid = _filled_sole_team(c, 'leave')
    before = _all_rows(tid, o)
    _boom_after(monkeypatch, '_qa_team_rows', 4)
    try:
        r = c.post(f'/api/teams/{tid}/leave', json={'delete_team': True},
                   headers=auth_headers(otok))
        assert r.status_code == 500
    except RuntimeError:
        pass
    monkeypatch.undo()
    db.session.remove()
    assert _all_rows(tid, o) == before


# ── D5 (children) stays Tim's: decision #1 ──────────────────────────────────

@pytest.mark.xfail(strict=True, reason=(
    "OWNER DECISION #1 pending: whether a child may become a team's owner "
    "(by the owner's pick or by the fallback). Today a child can: the PR "
    "flags it, and the review's D5 found the manual pick allows it too."))
def test_decision_pending_a_child_is_never_made_owner(client):
    o, otok = _person(client, 'kid_o')
    kid, ktok = _person(client, 'kid_k')
    tid, code = _team(client, otok)
    _join(client, ktok, tid, code)
    u = db.session.get(A.User, kid)
    u.age_band = A.AGE_CHILD
    db.session.commit()
    r = client.post(f'/api/teams/{tid}/owner', json={'new_owner_user_id': kid},
                    headers=auth_headers(otok))
    assert r.status_code != 200


# ── D5 (suspended members) stays Tim's: decision #1 ──────────────────────────
# A reviewer condition relayed by the coordinator asked for this to be blocked
# in code. It was NOT changed: the brief names D5 as Tim's policy, and the
# change was refused at the permission layer. Pinned here instead, so the
# moment it is decided the tests say exactly what changes.

def _suspend(uid):
    db.session.add(A.UserRestriction(user_id=uid, kind='social_suspended'))
    db.session.commit()


@pytest.mark.xfail(strict=True, reason=(
    "OWNER DECISION #1 pending (review D5): whether a socially SUSPENDED member "
    "may be picked as the next owner. Today the pick succeeds (200)."))
@pytest.mark.parametrize('route', ['owner', 'leave'])
def test_decision_pending_a_suspended_member_is_never_picked_as_owner(client, route):
    o, otok = _person(client, f'sus_o_{route}')
    s, stok = _person(client, f'sus_s_{route}')
    tid, code = _team(client, otok)
    _join(client, stok, tid, code)
    _suspend(s)
    r = client.post(f'/api/teams/{tid}/{route}', json={'new_owner_user_id': s},
                    headers=auth_headers(otok))
    assert r.status_code != 200


@pytest.mark.xfail(strict=True, reason=(
    "OWNER DECISION #1 pending (review D5, fallback eligibility rule): whether "
    "the automatic fallback skips a socially SUSPENDED member. Today it picks "
    "the longest-standing member whatever their suspension."))
def test_decision_pending_the_fallback_skips_a_suspended_member(app):
    owner = _u('fb_owner')
    sus = _u('fb_suspended')
    ok = _u('fb_ok')
    tid = _qa_team(owner, [sus, ok], owner_in=False, name='fallback')
    db.session.query(A.TeamMembership).filter_by(team_id=tid, user_id=sus).update(
        {A.TeamMembership.joined_at: datetime.datetime.utcnow() - datetime.timedelta(days=9)})
    db.session.commit()
    _suspend(sus)
    assert A._owned_team_plan(owner)[0]['new_owner_user_id'] == ok
