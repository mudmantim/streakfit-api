"""PR #23 round 5: Tim's decisions of 2026-11-14 (#6 and #7; #2-#5 are in
test_privacy_disclosures.py and test_pr23_followup.py).

* #6 Sole-owner leave: the confirmation says exactly what is lost -- the
  team's chat and photos, former members' included, permanently. Copy only;
  the behaviour is unchanged (test_team_owner_rules.py covers it).
* #7 QA cleanup: only positively verified test data is deleted. Confirmed
  end to end through the script's own main(), and the run prints a read-only
  "kept for manual review" listing.
"""
import pathlib
import sys
import uuid

import pytest
from sqlalchemy import event

import app as A
from app import db
from conftest import auth_headers, register_and_login

import scripts.cleanup_qa_smoke as C

ROOT = pathlib.Path(__file__).resolve().parent.parent
PW = 'WalkTest123!'

SOLE_OWNER_WARNING = (
    "You're the only one in this team, so leaving permanently deletes it. "
    "All of its chat and photos go, including messages and photos from people "
    "who have already left, and so does its history. This can't be undone. "
    "Your own streak, XP and acorns stay.")


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


# ── #6: the sole owner's leave says what is permanently lost ─────────────────

def test_the_sole_owner_leave_refusal_names_former_members_content(client):
    tok = register_and_login(client, 'r5_sole', PW)
    tid = client.post('/api/teams', json={'name': 'Solo'},
                      headers=auth_headers(tok)).get_json()['team']['id']
    r = client.post(f'/api/teams/{tid}/leave', json={}, headers=auth_headers(tok))
    assert r.status_code == 409
    body = r.get_json()
    assert body['error'] == 'team_will_be_deleted'
    assert body['message'] == SOLE_OWNER_WARNING
    # Behaviour unchanged: the team is still there until delete_team: true.
    db.session.expire_all()
    assert db.session.get(A.Team, tid) is not None


def test_the_sole_owner_leave_confirmation_in_the_app_says_the_same():
    js = (ROOT / 'static' / 'app.js').read_text(encoding='utf-8')
    body = js[js.index("leaveBtn.textContent = 'Leave Team';"):]
    body = body[:body.index('wrap.appendChild(leaveBtn);')]
    assert ("leaveBtn.textContent = 'Tap again to permanently delete '\n"
            "                    + (data.name || 'this team') + ' and leave';") in body
    flat = ' '.join(line.strip() for line in body.splitlines())
    for piece in ('"You\'re the only one in this team, so leaving "',
                  "'permanently deletes it. All of its chat and photos go, including '",
                  "'messages and photos from people who have already left, and so '",
                  "\"does its history. This can't be undone. Your streak, XP and \"",
                  "'acorns stay.'"):
        assert piece in flat, piece


# ── #7: the QA cleanup deletes only positively verified test data ────────────

def _u(name):
    u = A.User(username=name, password_hash='x')
    db.session.add(u)
    db.session.commit()
    return u.id


def _team(owner, members, owner_in=True, name='Smoke Test run'):
    t = A.Team(name=name, created_by_user_id=owner)
    db.session.add(t)
    db.session.flush()
    for uid in ([owner] if owner_in else []) + list(members):
        db.session.add(A.TeamMembership(team_id=t.id, user_id=uid))
    db.session.commit()
    return t.id


def _state(tid):
    db.session.expire_all()
    t = db.session.get(A.Team, tid)
    return (t is not None, t.created_by_user_id if t else None,
            A.TeamMembership.query.filter_by(team_id=tid).count(),
            A.TeamMessage.query.filter_by(team_id=tid).count(),
            A.TeamPhoto.query.filter_by(team_id=tid).count())


def _world():
    """One run, every case at once:
    verified -- a QA owner alone in a QA team with only QA and Rickie content;
    real_past -- a QA owner alone, but a real person posted there and left;
    real_heir -- a QA owner who left; a real member would be handed the team;
    cut       -- a QA owner alone, with a message whose author link is cut;
    real      -- a real person's own account and team (never a candidate)."""
    w = {}
    w['qa_ok'] = _u('qa_smoke_r5_ok')
    w['qa_mate'] = _u('qa_smoke_r5_mate')
    w['verified'] = _team(w['qa_ok'], [])
    db.session.add_all([
        A.TeamMessage(team_id=w['verified'], sender_type='user',
                      sender_user_id=w['qa_mate'], body='qa'),
        A.TeamMessage(team_id=w['verified'], sender_type='rickie', body='hi')])
    w['qa_past'] = _u('qa_smoke_r5_past')
    w['real_former'] = _u('real_r5_former')
    w['real_past'] = _team(w['qa_past'], [])
    db.session.add_all([
        A.TeamMessage(team_id=w['real_past'], sender_type='user',
                      sender_user_id=w['real_former'], body='mine'),
        A.TeamPhoto(public_id=uuid.uuid4().hex, team_id=w['real_past'],
                    sender_user_id=w['real_former'], image_data=b'x', byte_size=1)])
    w['qa_heir'] = _u('qa_smoke_r5_heir')
    w['real_member'] = _u('real_r5_member')
    w['real_heir'] = _team(w['qa_heir'], [w['real_member']], owner_in=False)
    w['qa_cut'] = _u('qa_smoke_r5_cut')
    w['cut'] = _team(w['qa_cut'], [])
    db.session.add(A.TeamMessage(team_id=w['cut'], sender_type='user',
                                 sender_user_id=None, body='who wrote this?'))
    w['real_owner'] = _u('real_r5_owner')
    w['real'] = _team(w['real_owner'], [], name='Smoke Test lookalike')
    db.session.commit()
    return w


def _run(monkeypatch, capsys, *argv):
    monkeypatch.setattr(sys, 'argv', ['cleanup_qa_smoke.py', *argv])
    C.main()
    return capsys.readouterr().out


def test_the_cleanup_deletes_only_positively_verified_test_data(fk_client, monkeypatch, capsys):
    w = _world()
    kept = {k: _state(w[k]) for k in ('real_past', 'real_heir', 'cut', 'real')}
    out = _run(monkeypatch, capsys, '--execute')
    db.session.expire_all()
    # Verified test data: gone.
    assert db.session.get(A.Team, w['verified']) is None
    assert db.session.get(A.User, w['qa_ok']) is None
    assert db.session.get(A.User, w['qa_mate']) is None
    # Everything ambiguous: untouched, rows and owners exactly as they were.
    for k, before in kept.items():
        assert _state(w[k]) == before, k
    for k in ('qa_past', 'qa_heir', 'qa_cut', 'real_former', 'real_member', 'real_owner'):
        assert db.session.get(A.User, w[k]) is not None, k
    assert '0 safe qa_smoke_ accounts remain' in out


def test_the_cleanup_lists_what_it_kept_for_manual_review(fk_client, monkeypatch, capsys):
    w = _world()
    before = {k: _state(w[k]) for k in ('real_past', 'real_heir', 'cut', 'verified')}
    out = _run(monkeypatch, capsys)              # dry run
    assert 'KEPT FOR MANUAL REVIEW (read-only listing; nothing below is changed): 3 item(s)' in out
    listing = out[out.index('KEPT FOR MANUAL REVIEW'):]
    listing = listing[:listing.index('\n\n')] if '\n\n' in listing else listing
    for team, owner, plan in (('real_past', 'qa_past', 'delete'),
                              ('real_heir', 'qa_heir', 'transfer'),
                              ('cut', 'qa_cut', 'delete')):
        line = next(ln for ln in listing.splitlines() if f"team={w[team]} " in ln)
        assert f"account id={w[owner]} " in line, line
        assert f'plan={plan} members=1 reasons=team_not_qa_only' in line, line
    # Ids and codes only: no username of a real person in the listing.
    assert 'real_r5' not in listing
    # The listing is read-only: nothing changed.
    assert {k: _state(w[k]) for k in before} == before
