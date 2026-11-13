"""Team creators can leave and delete their accounts (owner decision 2026-11-13).

Before this, `team_owned` blocked account deletion for anybody who had ever
created a team, and the refusal told them to "hand it over or remove the team
first" -- which no route could do. Tim's decision, verbatim: "delete the team
when the owner is its only member, plus automatic ownership transfer ... Owner
can pick the new owner if the owner decides to leave."

  * owner is the only member (or nobody is left): the team is deleted, through
    the same guarded path as the QA-team cleanup, unless a report about it is
    pending or under legal hold;
  * owner is in it with other people: they pick the next owner first
    (POST /api/teams/<id>/owner, or leave with new_owner_user_id);
  * the owner already left and cannot pick: the FALLBACK hands it to the
    longest-standing adult member, else the longest-standing member.

SQLite enforces foreign keys here, so a row left pointing at a deleted team or
person fails the test the way it would fail on PostgreSQL.
"""
import datetime
import pathlib
import uuid

import pytest
from sqlalchemy import event

import app as A
from app import db
from conftest import auth_headers, register_and_login

PW = 'WalkTest123!'
ROOT = pathlib.Path(__file__).resolve().parent.parent


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


def _delete_me(client, tok):
    return client.delete('/api/me', json={'password': PW}, headers=auth_headers(tok))


def _fill(client, tok, tid):
    """A message, a photo row, a challenge and a moment -- everything a team holds."""
    client.post(f'/api/teams/{tid}/messages', json={'body': 'we did it'}, headers=auth_headers(tok))
    uid = client.get('/api/me', headers=auth_headers(tok)).get_json()['id']
    photo = A.TeamPhoto(public_id=uuid.uuid4().hex, team_id=tid, sender_user_id=uid,
                        caption='at the park', image_data=b'px', byte_size=2)
    db.session.add(photo)
    db.session.flush()
    db.session.add(A.TeamMessage(team_id=tid, sender_type='user', sender_user_id=uid,
                                 body='at the park', photo_id=photo.id))
    db.session.commit()
    client.post(f'/api/teams/{tid}/challenges',
                json={'preset_key': A.CHALLENGE_PRESETS[0]['key']}, headers=auth_headers(tok))


def _team_rows(tid):
    db.session.expire_all()
    return {label: A._qa_team_rows(model, attr, tid).count()
            for label, model, attr in A._TEAM_ROW_DELETES}


def _report(tid, status='pending', hold=False, about=None, by=None):
    rep = A.Report(public_id=uuid.uuid4().hex, team_id=tid, category='harassment',
                   subject_type='user', status=status, legal_hold=hold,
                   legal_hold_reason='test' if hold else None,
                   reported_user_id=about, reporter_user_id=by)
    db.session.add(rep)
    db.session.commit()
    return rep.id


# ── The plan: one place decides, for every caller ──────────────────────────

def test_the_plan_names_the_right_action_for_each_shape(client):
    me, tok = _person(client, 'plan_me')
    mate, mtok = _person(client, 'plan_mate')
    sole, _ = _team(client, tok, 'sole')
    shared, code = _team(client, tok, 'shared')
    _join(client, mtok, shared, code)
    left, code2 = _team(client, tok, 'left')
    _join(client, mtok, left, code2)
    db.session.execute(db.delete(A.TeamMembership).where(
        A.TeamMembership.team_id == left, A.TeamMembership.user_id == me))
    empty = A.Team(name='empty', created_by_user_id=me)
    db.session.add(empty)
    db.session.commit()

    plan = {p['team_id']: p for p in A._owned_team_plan(me)}
    assert plan[sole]['action'] == 'delete' and plan[sole]['refusals'] == []
    assert plan[empty.id]['action'] == 'delete'
    assert plan[shared]['action'] == 'pick'
    assert plan[left]['action'] == 'transfer' and plan[left]['new_owner_user_id'] == mate

    counts = A.delete_user_account(me, dry_run=True)['counts']
    assert counts['team_owned'] == 1            # only `shared` needs a pick
    assert counts['team_owned_deleted'] == 2
    assert counts['team_owned_transferred'] == 1
    assert counts['team_report_open'] == 0


# ── Account deletion ───────────────────────────────────────────────────────

def test_a_sole_member_owner_can_delete_their_account_and_the_team_goes(fk_client):
    c = fk_client
    me, tok = _person(c, 'sole_owner')
    tid, code = _team(c, tok)
    _fill(c, tok, tid)
    closed = _report(tid, status='closed', by=me)
    assert all(v >= 0 for v in _team_rows(tid).values())

    r = _delete_me(c, tok)
    assert r.status_code == 200, r.get_json()
    assert r.get_json()['counts']['team_owned_deleted'] == 1
    db.session.expire_all()
    assert db.session.get(A.Team, tid) is None
    assert db.session.get(A.User, me) is None
    assert sum(_team_rows(tid).values()) == 0
    # The closed report outlives the team, with the link cut.
    assert db.session.get(A.Report, closed).team_id is None
    assert db.session.execute(db.text('PRAGMA foreign_key_check')).all() == []
    # The code no longer resolves for anybody.
    _o, otok = _person(c, 'sole_stranger')
    assert c.get(f'/api/teams/lookup/{code}', headers=auth_headers(otok)).status_code == 404


def test_an_empty_team_the_owner_already_left_goes_with_them(fk_client):
    c = fk_client
    me, tok = _person(c, 'empty_owner')
    tid, _code = _team(c, tok)
    db.session.execute(db.delete(A.TeamMembership).where(A.TeamMembership.team_id == tid))
    db.session.commit()
    assert _delete_me(c, tok).status_code == 200
    assert db.session.get(A.Team, tid) is None


def test_an_owner_still_in_a_team_with_others_must_pick_first(fk_client):
    c = fk_client
    me, tok = _person(c, 'pick_owner')
    mate, mtok = _person(c, 'pick_mate')
    tid, code = _team(c, tok)
    _join(c, mtok, tid, code)

    r = _delete_me(c, tok)
    body = r.get_json()
    assert r.status_code == 409
    assert body['blocker_codes'] == ['team_owned']
    # The refusal points at controls that now exist.
    assert 'Hand over the team' in body['message'] and 'Leave Team' in body['message']
    assert 'remove the team' not in body['message']
    assert db.session.get(A.User, me) is not None

    assert c.post(f'/api/teams/{tid}/owner', json={'new_owner_user_id': mate},
                  headers=auth_headers(tok)).status_code == 200
    assert _delete_me(c, tok).status_code == 200
    db.session.expire_all()
    team = db.session.get(A.Team, tid)
    assert team is not None and team.created_by_user_id == mate
    assert c.get(f'/api/teams/{tid}', headers=auth_headers(mtok)).get_json()['is_creator'] is True


def test_the_refusal_names_controls_the_app_really_has():
    """The 409 says "Hand over the team" and "Leave Team": both must be real
    labels in the client, or the message is the same dead end as before."""
    js = (ROOT / 'static' / 'app.js').read_text(encoding='utf-8')
    assert "'Hand over the team'" in js
    assert "'Leave Team'" in js
    assert "'/owner'" in js


def test_the_fallback_hands_a_team_the_owner_already_left_to_the_longest_standing_member(fk_client):
    c = fk_client
    me, tok = _person(c, 'fb_owner')
    first, ftok = _person(c, 'fb_first')
    second, stok = _person(c, 'fb_second')
    tid, code = _team(c, tok)
    _join(c, ftok, tid, code)
    _join(c, stok, tid, code)
    # Make the join order unambiguous, then the owner leaves the old way
    # (before 2026-11-13 leaving kept them as owner).
    db.session.query(A.TeamMembership).filter_by(team_id=tid, user_id=first).update(
        {A.TeamMembership.joined_at: datetime.datetime(2026, 1, 1)})
    db.session.query(A.TeamMembership).filter_by(team_id=tid, user_id=second).update(
        {A.TeamMembership.joined_at: datetime.datetime(2026, 2, 1)})
    db.session.execute(db.delete(A.TeamMembership).where(
        A.TeamMembership.team_id == tid, A.TeamMembership.user_id == me))
    db.session.commit()

    r = _delete_me(c, tok)
    assert r.status_code == 200, r.get_json()
    assert r.get_json()['counts']['team_owned_transferred'] == 1
    db.session.expire_all()
    assert db.session.get(A.Team, tid).created_by_user_id == first


def test_the_fallback_prefers_a_member_recorded_as_an_adult(app):
    users = []
    for i, band in enumerate([None, 'child', 'adult', 'adult']):
        u = A.User(username=f'band_{i}', password_hash='x', age_band=band)
        db.session.add(u)
        db.session.commit()
        users.append(u.id)
    assert A._fallback_team_owner(users) == users[2]
    assert A._fallback_team_owner(users[:2]) == users[0]      # nobody adult: longest-standing
    assert A._fallback_team_owner(users, exclude=[users[2]]) == users[3]
    assert A._fallback_team_owner([]) is None


@pytest.mark.parametrize('status,hold', [('pending', False), ('closed', True)])
def test_a_report_about_a_sole_members_team_keeps_it_and_the_account(fk_client, status, hold):
    c = fk_client
    me, tok = _person(c, f'rep_owner_{status}')
    tid, _code = _team(c, tok)
    _fill(c, tok, tid)
    rid = _report(tid, status=status, hold=hold)
    before = _team_rows(tid)

    r = _delete_me(c, tok)
    body = r.get_json()
    assert r.status_code == 409
    # Generic, like every other safety record: it never names a report.
    assert body['blocker_codes'] == ['safety_record'] and body['blockers'] == []
    assert 'report' not in body['message'].lower()
    assert A.delete_user_account(me, dry_run=True)['blocker_codes'] == ['team_report_open']
    assert _team_rows(tid) == before and db.session.get(A.Team, tid) is not None
    # The operator, not the person, is told the report is holding someone up.
    db.session.expire_all()
    assert db.session.get(A.Report, rid).deletion_requested_at is not None


def test_once_the_report_is_decided_the_same_button_works(fk_client):
    c = fk_client
    me, tok = _person(c, 'rep_then_ok')
    tid, _code = _team(c, tok)
    rid = _report(tid)
    assert _delete_me(c, tok).status_code == 409
    db.session.get(A.Report, rid).status = 'closed'
    db.session.commit()
    assert _delete_me(c, tok).status_code == 200
    db.session.expire_all()
    assert db.session.get(A.Team, tid) is None
    assert db.session.get(A.Report, rid).team_id is None


def test_a_multi_member_owner_still_gets_the_team_answer_whatever_else_blocks(fk_client):
    """Unchanged rule (anti-polling): while a pick is needed, the answer is the
    team answer even when a report about them also blocks."""
    c = fk_client
    me, tok = _person(c, 'poll_owner')
    mate, mtok = _person(c, 'poll_mate')
    tid, code = _team(c, tok)
    _join(c, mtok, tid, code)
    before = _delete_me(c, tok).get_json()
    _report(None, about=me, by=mate)
    after = _delete_me(c, tok).get_json()
    assert before == after and after['blocker_codes'] == ['team_owned']


# ── Leaving ────────────────────────────────────────────────────────────────

def test_an_owner_with_others_must_choose_before_leaving(client):
    me, tok = _person(client, 'lv_owner')
    mate, mtok = _person(client, 'lv_mate')
    _out, otok = _person(client, 'lv_out')
    tid, code = _team(client, tok)
    _join(client, mtok, tid, code)

    r = client.post(f'/api/teams/{tid}/leave', headers=auth_headers(tok))
    assert r.status_code == 409 and r.get_json()['error'] == 'owner_must_choose'
    out_id = client.get('/api/me', headers=auth_headers(otok)).get_json()['id']
    for bad, code_ in ((out_id, 400), (me, 400), ('7', 400), (True, 400), (-1, 400)):
        r = client.post(f'/api/teams/{tid}/leave', json={'new_owner_user_id': bad},
                        headers=auth_headers(tok))
        assert r.status_code == code_, (bad, r.get_json())
    assert client.get(f'/api/teams/{tid}', headers=auth_headers(tok)).status_code == 200

    r = client.post(f'/api/teams/{tid}/leave', json={'new_owner_user_id': mate},
                    headers=auth_headers(tok))
    assert r.status_code == 200
    db.session.expire_all()
    assert db.session.get(A.Team, tid).created_by_user_id == mate
    assert client.get(f'/api/teams/{tid}', headers=auth_headers(tok)).status_code == 403
    # The new owner has the creator's safety powers.
    assert client.post(f'/api/teams/{tid}/rotate-invite',
                       headers=auth_headers(mtok)).status_code == 200
    # And a member_left moment, as for anybody leaving.
    types = [m.moment_type for m in A.TeamMoment.query.filter_by(team_id=tid)]
    assert 'member_left' in types


def test_a_sole_owner_leaving_deletes_the_team_only_when_confirmed(fk_client):
    c = fk_client
    me, tok = _person(c, 'lv_sole')
    tid, code = _team(c, tok)
    _fill(c, tok, tid)
    r = c.post(f'/api/teams/{tid}/leave', headers=auth_headers(tok))
    assert r.status_code == 409 and r.get_json()['error'] == 'team_will_be_deleted'
    assert 'deletes it' in r.get_json()['message']
    assert db.session.get(A.Team, tid) is not None

    r = c.post(f'/api/teams/{tid}/leave', json={'delete_team': True}, headers=auth_headers(tok))
    assert r.status_code == 200
    db.session.expire_all()
    assert db.session.get(A.Team, tid) is None
    assert sum(_team_rows(tid).values()) == 0
    assert db.session.get(A.User, me) is not None        # leaving never touches the account
    assert db.session.execute(db.text('PRAGMA foreign_key_check')).all() == []
    # And with no team left, the account can be deleted.
    assert _delete_me(c, tok).status_code == 200


def test_a_sole_owner_leaving_a_team_under_review_leaves_it_closed_not_deleted(client):
    me, tok = _person(client, 'lv_review')
    tid, code = _team(client, tok)
    _report(tid)
    r = client.post(f'/api/teams/{tid}/leave', json={'delete_team': True}, headers=auth_headers(tok))
    # Same answer as a deletion: it says nothing about a report.
    assert r.status_code == 200 and r.get_json() == {'message': 'Left team'}
    db.session.expire_all()
    assert db.session.get(A.Team, tid) is not None
    assert A.TeamMembership.query.filter_by(team_id=tid).count() == 0
    _s, stok = _person(client, 'lv_review_stranger')
    assert client.get(f'/api/teams/lookup/{code}', headers=auth_headers(stok)).status_code == 404


def test_a_member_who_is_not_the_owner_leaves_exactly_as_before(client):
    me, tok = _person(client, 'lv_plain_owner')
    mate, mtok = _person(client, 'lv_plain_mate')
    tid, code = _team(client, tok)
    _join(client, mtok, tid, code)
    # An ignored new_owner_user_id changes nothing for a non-owner.
    r = client.post(f'/api/teams/{tid}/leave', json={'new_owner_user_id': me},
                    headers=auth_headers(mtok))
    assert r.status_code == 200
    db.session.expire_all()
    assert db.session.get(A.Team, tid).created_by_user_id == me


# ── Transfer ───────────────────────────────────────────────────────────────

def test_only_the_owner_can_hand_over_and_only_to_a_member(client):
    me, tok = _person(client, 'tr_owner')
    mate, mtok = _person(client, 'tr_mate')
    out, otok = _person(client, 'tr_out')
    tid, code = _team(client, tok)
    _join(client, mtok, tid, code)
    url = f'/api/teams/{tid}/owner'

    assert client.post(url, json={'new_owner_user_id': me},
                       headers=auth_headers(mtok)).status_code == 403
    assert client.post(url, json={'new_owner_user_id': me},
                       headers=auth_headers(tok)).status_code == 400
    assert client.post(url, json={'new_owner_user_id': out},
                       headers=auth_headers(tok)).status_code == 400
    assert client.post(url, json={}, headers=auth_headers(tok)).status_code == 400
    assert client.post(url, json={'new_owner_user_id': 'x'},
                       headers=auth_headers(tok)).status_code == 400
    assert client.post('/api/teams/999999/owner', json={'new_owner_user_id': mate},
                       headers=auth_headers(tok)).status_code == 404

    r = client.post(url, json={'new_owner_user_id': mate}, headers=auth_headers(tok))
    assert r.status_code == 200 and r.get_json()['created_by_user_id'] == mate
    # The old owner stays a member, without the creator powers.
    assert client.get(f'/api/teams/{tid}', headers=auth_headers(tok)).get_json()['is_creator'] is False
    assert client.post(f'/api/teams/{tid}/rotate-invite',
                       headers=auth_headers(tok)).status_code == 403
    assert client.post(url, json={'new_owner_user_id': mate},
                       headers=auth_headers(tok)).status_code == 403


def test_the_self_deletion_counts_say_what_happened_to_their_teams(client):
    assert 'team_owned_deleted' in A._SELF_DELETION_COUNTS
    assert 'team_owned_transferred' in A._SELF_DELETION_COUNTS
    # A moderation blocker is never one of the counts shown to the person.
    assert 'team_report_open' not in A._SELF_DELETION_COUNTS
    assert 'team_report_open' in A._MODERATION_DELETION_BLOCKERS
