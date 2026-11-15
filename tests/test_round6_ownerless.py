"""PR #23 round 6: owner decision #1 as DECIDED (Tim, 2026-11-14/15) and the
round-5 review fixes D-R5-1 and D-R5-3.

  M1        team.created_by_user_id is nullable (NULL = ownerless); the
            downgrade refuses while any team is ownerless.
  P1        only a verified adult who is not suspended can receive a team
            (pick, leave, fallback); everyone else is 400 / ownerless.
  P3        an owner may leave choosing "nobody".
  P4        an ownerless team's code answers like an unknown code (lookup)
            or a wrong one (join); the team view hides the code.
  P4-safety an ownerless team is READ-ONLY at the API: every write path is
            refused, including system writers and a direct ORM insert.
  P7        a suspended owner keeps the team but cannot remove members.
  P9        (tests/test_team_owner_rules.py) a sole owner deleting under a
            team report: the team is kept ownerless, the deletion proceeds.
  Reaper    empty ownerless teams are deleted once nothing keeps them.
  Traps     nobody can ever be unable to leave or delete their account.
  D-R5-1    the sole-owner copy is true when a review keeps the team.
  D-R5-3    qa_smoke_ is reserved at sign-up; the cleanup needs provenance.

SQLite with foreign keys ON. The races are in tests/team_owner_race.py and
tests/ownerless_race.py (PostgreSQL).
"""
import datetime
import io
import json
import os
import pathlib
import subprocess
import sys
import tempfile
import uuid

import pytest
from sqlalchemy import create_engine, event, text

import app as A
from app import db
from conftest import auth_headers, mission_json, qa_tooling_created, register_and_login

import scripts.cleanup_qa_smoke as C

PW = 'WalkTest123!'
ROOT = pathlib.Path(__file__).resolve().parent.parent
REPO = str(ROOT)


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


def _person(client, name, band=None):
    tok = register_and_login(client, name, PW)
    uid = client.get('/api/me', headers=auth_headers(tok)).get_json()['id']
    if band is not None:
        db.session.get(A.User, uid).age_band = band
        db.session.commit()
    return uid, tok


def _team(client, tok, name='Hill Family'):
    t = client.post('/api/teams', json={'name': name}, headers=auth_headers(tok)).get_json()['team']
    return t['id'], t['invite_code']


def _join(client, tok, tid, code):
    r = client.post(f'/api/teams/{tid}/join', json={'code': code}, headers=auth_headers(tok))
    assert r.status_code == 200, r.get_json()


def _suspend(uid):
    db.session.add(A.UserRestriction(user_id=uid, kind='social_suspended'))
    db.session.commit()


def _report(tid, status='pending', hold=False, about=None, by=None,
            subject_type='user', subject_ref=None):
    rep = A.Report(public_id=uuid.uuid4().hex, team_id=tid, category='harassment',
                   subject_type=subject_type, subject_ref=subject_ref, status=status,
                   legal_hold=hold, legal_hold_reason='test' if hold else None,
                   reported_user_id=about, reporter_user_id=by)
    db.session.add(rep)
    db.session.commit()
    return rep.id


def _ownerless_world(client, tag='ow'):
    """Owner O leaves choosing nobody; members M and K stay. A challenge M
    started and a photo M shared exist from before. Returns ids and tokens."""
    o, otok = _person(client, f'{tag}_o')
    m, mtok = _person(client, f'{tag}_m')
    k, ktok = _person(client, f'{tag}_k')
    tid, code = _team(client, otok, f'Fam {tag}')
    _join(client, mtok, tid, code)
    _join(client, ktok, tid, code)
    ch = client.post(f'/api/teams/{tid}/challenges',
                     json={'preset_key': A.CHALLENGE_PRESETS[0]['key']},
                     headers=auth_headers(mtok)).get_json()
    photo = A.TeamPhoto(public_id=uuid.uuid4().hex, team_id=tid, sender_user_id=m,
                        caption='park', image_data=b'px', byte_size=2,
                        expires_at=datetime.datetime.utcnow() + datetime.timedelta(days=5))
    db.session.add(photo)
    db.session.commit()
    r = client.post(f'/api/teams/{tid}/leave', json={'no_owner': True},
                    headers=auth_headers(otok))
    assert r.status_code == 200, r.get_json()
    db.session.expire_all()
    assert db.session.get(A.Team, tid).created_by_user_id is None
    return {'o': o, 'otok': otok, 'm': m, 'mtok': mtok, 'k': k, 'ktok': ktok,
            'tid': tid, 'code': code, 'photo': photo.public_id,
            'challenge': (ch.get('challenge') or ch).get('public_id')}


def _content_counts(tid):
    db.session.expire_all()
    return {
        'messages': A.TeamMessage.query.filter_by(team_id=tid).count(),
        'moments': A.TeamMoment.query.filter_by(team_id=tid).count(),
        'photos': A.TeamPhoto.query.filter_by(team_id=tid).count(),
        'challenges': A.TeamChallenge.query.filter_by(team_id=tid).count(),
        'completions': A.TeamChallengeCompletion.query.join(
            A.TeamChallenge, A.TeamChallenge.id == A.TeamChallengeCompletion.challenge_id
        ).filter(A.TeamChallenge.team_id == tid).count(),
        'members': A.TeamMembership.query.filter_by(team_id=tid).count(),
        'campfire': (A.TeamCampfire.query.filter_by(team_id=tid).one().total_team_missions),
    }


def _jpeg():
    from test_photo_privacy import _jpeg as build      # the suite's own valid JPEG
    return build(width=64, height=48)


# ── M1: the migration ──────────────────────────────────────────────────────

def _flask_db(url, *args):
    env = {**os.environ, 'DATABASE_URL': url, 'FLASK_APP': 'app',
           'SECRET_KEY': 'test', 'JWT_SECRET_KEY': 'test'}
    env.pop('STREAKFIT_ENFORCE_DB_HEAD', None)
    env.pop('STREAKFIT_RETENTION_SWEEPER', None)
    return subprocess.run([sys.executable, '-m', 'flask', 'db', *args],
                          cwd=REPO, env=env, capture_output=True, text=True, timeout=300)


def _nullable_sqlite(engine):
    with engine.connect() as c:
        cols = {r[1]: r[3] for r in c.execute(text("PRAGMA table_info('team')"))}
    return cols['created_by_user_id'] == 0     # notnull flag 0 -> nullable


def test_m1_upgrades_and_downgrades_on_sqlite_and_refuses_while_a_team_is_ownerless():
    fd, path = tempfile.mkstemp(suffix='.db')
    os.close(fd)
    url = f'sqlite:///{path}'
    try:
        up = _flask_db(url, 'upgrade')
        assert up.returncode == 0, up.stderr[-2000:]
        eng = create_engine(url)
        with eng.connect() as c:
            assert c.execute(text('select version_num from alembic_version')).scalar() == 'a1c4e0f2b7d9'
        assert _nullable_sqlite(eng)
        with eng.begin() as c:
            c.execute(text("insert into team (name, created_by_user_id, created_at) "
                           "values ('ownerless', NULL, CURRENT_TIMESTAMP)"))
        down = _flask_db(url, 'downgrade', 'd4f1b6e2a9c3')
        assert down.returncode != 0 and '1 team(s) are ownerless' in down.stderr
        with eng.connect() as c:
            assert c.execute(text('select version_num from alembic_version')).scalar() == 'a1c4e0f2b7d9'
        with eng.begin() as c:
            c.execute(text('delete from team'))
        down = _flask_db(url, 'downgrade', 'd4f1b6e2a9c3')
        assert down.returncode == 0, down.stderr[-2000:]
        assert not _nullable_sqlite(eng)
        up = _flask_db(url, 'upgrade')
        assert up.returncode == 0 and _nullable_sqlite(eng)
        eng.dispose()
    finally:
        for suffix in ('', '-journal', '-wal', '-shm'):
            try:
                os.unlink(path + suffix)
            except FileNotFoundError:
                pass


def test_the_model_allows_an_ownerless_team():
    assert A.Team.__table__.c.created_by_user_id.nullable is True
    # And team.created_by_user_id is a by-hand rule in the deletion FK guard.
    assert A._user_fk_classification()[('team', 'created_by_user_id')] == 'by_hand'


def test_the_migration_is_the_single_head():
    from alembic.config import Config
    from alembic.script import ScriptDirectory
    cfg = Config()
    cfg.set_main_option('script_location', str(ROOT / 'migrations'))
    assert ScriptDirectory.from_config(cfg).get_heads() == ['a1c4e0f2b7d9']


# ── P3: an owner may leave choosing nobody ────────────────────────────────

def test_an_owner_can_leave_choosing_nobody_and_the_team_becomes_ownerless(client):
    o, otok = _person(client, 'nb_o')
    m, mtok = _person(client, 'nb_m', band=A.AGE_ADULT)      # eligible, and still not forced
    tid, code = _team(client, otok)
    _join(client, mtok, tid, code)
    r = client.post(f'/api/teams/{tid}/leave', headers=auth_headers(otok))
    assert r.status_code == 409 and r.get_json() == {
        'error': 'owner_must_choose',
        'message': "You created this team. Choose who looks after it before you "
                   "leave, or leave it without an owner."}
    r = client.post(f'/api/teams/{tid}/leave', json={'no_owner': True, 'new_owner_user_id': m},
                    headers=auth_headers(otok))
    assert r.status_code == 400
    r = client.post(f'/api/teams/{tid}/leave', json={'no_owner': True},
                    headers=auth_headers(otok))
    assert r.status_code == 200 and r.get_json() == {'message': 'Left team'}
    db.session.expire_all()
    assert db.session.get(A.Team, tid).created_by_user_id is None
    assert A.TeamMembership.query.filter_by(team_id=tid, user_id=o).count() == 0
    # The former owner can now delete their account: nothing is owned.
    assert client.delete('/api/me', json={'password': PW},
                         headers=auth_headers(otok)).status_code == 200


def test_no_owner_must_be_literally_true(client):
    o, otok = _person(client, 'nbt_o')
    m, mtok = _person(client, 'nbt_m')
    tid, code = _team(client, otok)
    _join(client, mtok, tid, code)
    for bad in ('true', 1, 'yes', None):
        r = client.post(f'/api/teams/{tid}/leave', json={'no_owner': bad},
                        headers=auth_headers(otok))
        assert r.status_code == 409 and r.get_json()['error'] == 'owner_must_choose', bad
    db.session.expire_all()
    assert db.session.get(A.Team, tid).created_by_user_id == o


def test_every_handover_today_is_refused_with_the_documented_message(client):
    """P1 + P2 deferred: nothing records an age, so every real handover is 400."""
    o, otok = _person(client, 'ho_o')
    m, mtok = _person(client, 'ho_m')
    tid, code = _team(client, otok)
    _join(client, mtok, tid, code)
    expected = {
        'error': 'new_owner_not_eligible',
        'message': "A team can only be handed to a member confirmed as an adult whose "
                   "account has no restrictions. StreakFit can't confirm anyone's age "
                   "yet, so for now you can leave the team without an owner instead."}
    for route in ('owner', 'leave'):
        r = client.post(f'/api/teams/{tid}/{route}', json={'new_owner_user_id': m},
                        headers=auth_headers(otok))
        assert r.status_code == 400 and r.get_json() == expected, route


# ── P4: no joining, no code, no oracle ────────────────────────────────────

def _strip(resp):
    return resp.status_code, resp.get_data(as_text=True), sorted(
        (k, v) for k, v in resp.headers.items() if k != 'Content-Length')


def test_an_ownerless_teams_code_answers_like_an_unknown_one(client):
    w = _ownerless_world(client)
    _s, stok = _person(client, 'ow_stranger')
    h = auth_headers(stok)
    real = client.get(f"/api/teams/lookup/{w['code']}", headers=h)
    unknown = client.get('/api/teams/lookup/ZZZZZ9', headers=h)
    assert real.status_code == 404 and _strip(real) == _strip(unknown)
    # Join with the right code == join with a wrong code (an owned team's answer).
    right = client.post(f"/api/teams/{w['tid']}/join", json={'code': w['code']}, headers=h)
    wrong = client.post(f"/api/teams/{w['tid']}/join", json={'code': 'ZZZZZ9'}, headers=h)
    assert right.status_code == 403 and _strip(right) == _strip(wrong)
    assert right.get_json() == {'error': 'Invalid invite code'}
    # ...and the same as an OWNED team answers a wrong code.
    o2, o2tok = _person(client, 'ow_owner2')
    t2, _c2 = _team(client, o2tok, 'Owned')
    owned_wrong = client.post(f'/api/teams/{t2}/join', json={'code': 'ZZZZZ9'}, headers=h)
    assert _strip(owned_wrong) == _strip(wrong)
    assert A.TeamMembership.query.filter_by(team_id=w['tid']).count() == 2


def test_the_ownerless_team_view_hides_the_code_and_shows_the_notice(client):
    w = _ownerless_world(client)
    d = client.get(f"/api/teams/{w['tid']}", headers=auth_headers(w['mtok'])).get_json()
    assert d['ownerless'] is True and d['read_only'] is True and d['is_creator'] is False
    assert d['invite_code'] is None and w['code'] not in json.dumps(d)
    assert d['ownerless_notice'] == A.TEAM_OWNERLESS_NOTICE
    assert not any(m['is_creator'] for m in d['members'])
    listing = client.get('/api/teams', headers=auth_headers(w['mtok'])).get_json()
    assert [t['ownerless'] for t in listing] == [True]
    # An owned team says so too.
    o2, o2tok = _person(client, 'ow_owner3')
    t2, _c = _team(client, o2tok, 'Owned')
    d2 = client.get(f'/api/teams/{t2}', headers=auth_headers(o2tok)).get_json()
    assert d2['ownerless'] is False and d2['invite_code'] and d2['ownerless_notice'] is None


def test_nobody_holds_owner_powers_over_an_ownerless_team(client):
    w = _ownerless_world(client)
    for tok in (w['mtok'], w['ktok']):
        h = auth_headers(tok)
        assert client.post(f"/api/teams/{w['tid']}/rotate-invite", headers=h).status_code == 403
        other = w['k'] if tok == w['mtok'] else w['m']
        assert client.delete(f"/api/teams/{w['tid']}/members/{other}", headers=h).status_code == 403
        assert client.post(f"/api/teams/{w['tid']}/owner", json={'new_owner_user_id': other},
                           headers=h).status_code == 403
    assert A.TeamMembership.query.filter_by(team_id=w['tid']).count() == 2


# ── P4-safety: READ-ONLY at the API, every write path ─────────────────────

def _write_requests(client, w, tok):
    t = w['tid']
    h = auth_headers(tok)
    return {
        'message': lambda: client.post(f'/api/teams/{t}/messages', json={'body': 'hi'}, headers=h),
        'reaction': lambda: client.post(f'/api/teams/{t}/messages', json={'body': '👍'}, headers=h),
        'challenge': lambda: client.post(f'/api/teams/{t}/challenges',
                                         json={'preset_key': A.CHALLENGE_PRESETS[0]['key']},
                                         headers=h),
        'challenge_named': lambda: client.post(
            f'/api/teams/{t}/challenges',
            json={'preset_key': A.CHALLENGE_PRESETS[0]['key'], 'target_user_id': w['k']},
            headers=h),
        'complete': lambda: client.post(f"/api/teams/{t}/challenges/{w['challenge']}/complete",
                                        json={}, headers=h),
        'photo': lambda: client.post(f'/api/teams/{t}/photos', headers=h,
                                     data={'photo': (io.BytesIO(_jpeg()), 'p.jpg'),
                                           'caption': 'new'},
                                     content_type='multipart/form-data'),
    }


@pytest.mark.parametrize('path', ['message', 'reaction', 'challenge', 'challenge_named',
                                  'complete', 'photo'])
def test_every_content_write_to_an_ownerless_team_is_refused(fk_client, path):
    c = fk_client
    w = _ownerless_world(c, f'ro_{path}')
    before = _content_counts(w['tid'])
    r = _write_requests(c, w, w['ktok'])[path]()
    assert r.status_code == 403, (path, r.get_json())
    assert r.get_json() == {'error': 'team_read_only', 'message': A.TEAM_READ_ONLY_MESSAGE}
    assert _content_counts(w['tid']) == before


def test_the_same_writes_succeed_in_an_owned_team(fk_client):
    """Positive control: the gate is ownerlessness, nothing else."""
    c = fk_client
    o, otok = _person(c, 'ctl_o')
    k, ktok = _person(c, 'ctl_k')
    m, mtok = _person(c, 'ctl_m')
    tid, code = _team(c, otok)
    _join(c, ktok, tid, code)
    _join(c, mtok, tid, code)
    ch = c.post(f'/api/teams/{tid}/challenges', json={'preset_key': A.CHALLENGE_PRESETS[0]['key']},
                headers=auth_headers(mtok)).get_json()
    w = {'tid': tid, 'k': k, 'challenge': (ch.get('challenge') or ch).get('public_id')}
    for path, call in _write_requests(c, w, ktok).items():
        assert call().status_code in (200, 201), path


def _complete_mission(client, tok):
    d = client.get('/api/daily', headers=auth_headers(tok)).get_json()
    keys = [e['key'] for e in d['exercises']][:5]
    out = None
    for key in keys:
        out = client.post(f'/api/daily/{key}/complete', json=mission_json(),
                          headers=auth_headers(tok))
        assert out.status_code == 200, out.get_json()
    return out.get_json()


def test_a_mission_completion_writes_nothing_into_an_ownerless_team(fk_client):
    """The campfire, its log moment, the stage moment and Rickie's post are
    all new content: none is written. The person's own completion is unaffected."""
    c = fk_client
    w = _ownerless_world(c, 'mission')
    o2, o2tok = _person(c, 'mission_o2')
    t2, code2 = _team(c, o2tok, 'Owned too')
    _join(c, w['ktok'], t2, code2)
    before = _content_counts(w['tid'])
    before_owned = _content_counts(t2)
    out = _complete_mission(c, w['ktok'])
    assert out['completed_count'] == 5
    assert _content_counts(w['tid']) == before
    after_owned = _content_counts(t2)
    assert after_owned['campfire'] > before_owned['campfire']
    assert after_owned['moments'] > before_owned['moments']
    assert {u['team_id'] for u in out.get('team_campfire_updates', [])} == {t2}


@pytest.mark.parametrize('kind', ['message', 'rickie', 'moment', 'photo', 'challenge',
                                  'completion'])
def test_a_direct_orm_insert_into_an_ownerless_team_is_refused_at_flush(fk_client, kind):
    """Layer 3: a background job or a future route that skips the route gate
    still cannot write (TeamReadOnly from the before_flush guard)."""
    c = fk_client
    w = _ownerless_world(c, f'orm_{kind}')
    before = _content_counts(w['tid'])
    tid = w['tid']
    ch = A.TeamChallenge.query.filter_by(public_id=w['challenge']).one()
    rows = {
        'message': lambda: A.TeamMessage(team_id=tid, sender_type='user', sender_user_id=w['m'],
                                         body='sneaky'),
        'rickie': lambda: A.TeamMessage(team_id=tid, sender_type='rickie', body='hi'),
        'moment': lambda: A.TeamMoment(team_id=tid, moment_type='member_joined'),
        'photo': lambda: A.TeamPhoto(public_id=uuid.uuid4().hex, team_id=tid, image_data=b'x',
                                     byte_size=1),
        'challenge': lambda: A.TeamChallenge(public_id=uuid.uuid4().hex, team_id=tid,
                                             preset_key='squats_10'),
        'completion': lambda: A.TeamChallengeCompletion(challenge_id=ch.id, user_id=w['k']),
    }
    db.session.add(rows[kind]())
    with pytest.raises(A.TeamReadOnly):
        db.session.commit()
    db.session.rollback()
    assert _content_counts(tid) == before


def test_a_stale_session_cannot_write_after_the_team_became_ownerless(fk_client):
    """The team row was loaded while owned; another request made it ownerless.
    The route re-reads under its lock, and the flush guard reads the database."""
    c = fk_client
    o, otok = _person(c, 'stale_o')
    m, mtok = _person(c, 'stale_m')
    tid, code = _team(c, otok)
    _join(c, mtok, tid, code)
    stale = db.session.get(A.Team, tid)
    assert stale.created_by_user_id == o
    # Another connection makes it ownerless behind this session's back.
    with db.engine.begin() as conn:
        conn.execute(text('update team set created_by_user_id = NULL where id = :t'), {'t': tid})
    assert stale.created_by_user_id == o          # this session still believes it is owned
    db.session.add(A.TeamMessage(team_id=tid, sender_type='user', sender_user_id=m, body='x'))
    with pytest.raises(A.TeamReadOnly):
        db.session.flush()
    db.session.rollback()
    r = c.post(f'/api/teams/{tid}/messages', json={'body': 'x'}, headers=auth_headers(mtok))
    assert r.status_code == 403 and r.get_json()['error'] == 'team_read_only'


def test_the_system_writers_write_nothing_for_an_ownerless_team(fk_client):
    c = fk_client
    w = _ownerless_world(c, 'sys')
    before = _content_counts(w['tid'])
    assert A.create_team_moment(w['tid'], 'campfire_log_added', subject_user_id=w['m']) is None
    assert A.create_rickie_team_message(w['tid'], 'first_log') is None
    db.session.commit()
    assert _content_counts(w['tid']) == before


def test_members_of_an_ownerless_team_can_still_read_report_and_remove_their_own_photo(fk_client):
    c = fk_client
    w = _ownerless_world(c, 'allowed')
    h = auth_headers(w['mtok'])
    t = w['tid']
    for path in (f'/api/teams/{t}', f'/api/teams/{t}/messages', f'/api/teams/{t}/moments',
                 f'/api/teams/{t}/campfire', f"/api/teams/{t}/photos/{w['photo']}"):
        assert c.get(path, headers=h).status_code == 200, path
    r = c.post('/api/reports', json={'category': 'other', 'subject_type': 'user',
                                     'reported_user_id': w['k'], 'team_id': t}, headers=h)
    assert r.status_code == 201, r.get_json()
    assert c.delete(f"/api/teams/{t}/photos/{w['photo']}", headers=h).status_code == 200
    db.session.expire_all()
    assert A.TeamPhoto.query.filter_by(public_id=w['photo']).one().image_data is None


def test_the_read_only_copy_in_the_app_matches_the_server():
    js = (ROOT / 'static' / 'app.js').read_text(encoding='utf-8')
    start = js.index('var TEAM_OWNERLESS_NOTICE = ')
    chunk = js[start:js.index(';', js.index("'no end date.'", start)) + 1]
    pieces = []
    for part in chunk[len('var TEAM_OWNERLESS_NOTICE = '):-1].split('+'):
        part = part.strip()
        pieces.append(json.loads(part) if part.startswith('"') else part[1:-1])
    assert ''.join(pieces) == A.TEAM_OWNERLESS_NOTICE
    assert 'may stay read-only with no end date' in A.TEAM_OWNERLESS_NOTICE
    # Neutral: never says why (a report must never be disclosed).
    for word in ('report about', 'reported', 'investigat', 'suspend', 'moderat'):
        assert word not in A.TEAM_OWNERLESS_NOTICE.lower(), word


# ── P7: a suspended owner keeps the team, without admin powers ────────────

def test_a_suspended_owner_keeps_the_team_but_cannot_remove_members(client):
    o, otok = _person(client, 'so_o')
    m, mtok = _person(client, 'so_m')
    tid, code = _team(client, otok)
    _join(client, mtok, tid, code)
    _suspend(o)
    r = client.delete(f'/api/teams/{tid}/members/{m}', headers=auth_headers(otok))
    assert r.status_code == 403 and r.get_json()['code'] == 'social_suspended'
    assert client.post(f'/api/teams/{tid}/rotate-invite',
                       headers=auth_headers(otok)).status_code == 403
    db.session.expire_all()
    assert db.session.get(A.Team, tid).created_by_user_id == o
    assert A.TeamMembership.query.filter_by(team_id=tid, user_id=m).count() == 1
    # Still the owner, and can still leave (nobody) and delete their account.
    d = client.get(f'/api/teams/{tid}', headers=auth_headers(otok)).get_json()
    assert d['is_creator'] is True and d['ownerless'] is False
    assert client.post(f'/api/teams/{tid}/leave', json={'no_owner': True},
                       headers=auth_headers(otok)).status_code == 200
    assert client.delete('/api/me', json={'password': PW},
                         headers=auth_headers(otok)).status_code == 200


def test_an_unsuspended_owner_can_still_remove_a_member(client):
    o, otok = _person(client, 'su_o')
    m, mtok = _person(client, 'su_m')
    tid, code = _team(client, otok)
    _join(client, mtok, tid, code)
    assert client.delete(f'/api/teams/{tid}/members/{m}',
                         headers=auth_headers(otok)).status_code == 200


# ── Leaving the last seat of an ownerless team ────────────────────────────

def test_the_last_member_of_an_ownerless_team_is_warned_and_then_it_is_deleted(fk_client):
    c = fk_client
    w = _ownerless_world(c, 'last')
    assert c.post(f"/api/teams/{w['tid']}/leave", headers=auth_headers(w['ktok'])).status_code == 200
    r = c.post(f"/api/teams/{w['tid']}/leave", headers=auth_headers(w['mtok']))
    assert r.status_code == 409 and r.get_json() == {
        'error': 'team_will_be_deleted', 'message': A._SOLE_OWNER_LEAVE_WARNING}
    r = c.post(f"/api/teams/{w['tid']}/leave", json={'delete_team': True},
               headers=auth_headers(w['mtok']))
    assert r.status_code == 200
    db.session.expire_all()
    assert db.session.get(A.Team, w['tid']) is None


def test_the_last_member_leaving_a_reported_ownerless_team_keeps_it_until_the_review_ends(fk_client):
    c = fk_client
    w = _ownerless_world(c, 'lastrep')
    rid = _report(w['tid'], about=w['k'], by=w['m'])
    for tok in (w['ktok'], w['mtok']):
        r = c.post(f"/api/teams/{w['tid']}/leave", json={'delete_team': True},
                   headers=auth_headers(tok))
        assert r.status_code == 200 and r.get_json() == {'message': 'Left team'}
    db.session.expire_all()
    assert db.session.get(A.Team, w['tid']) is not None
    assert A._reap_empty_ownerless_teams()['kept_for_review'] == 1
    db.session.get(A.Report, rid).status = 'closed'
    db.session.commit()
    assert A._reap_empty_ownerless_teams()['deleted'] == 1
    assert db.session.get(A.Team, w['tid']) is None


# ── D-R5-1: the sole-owner copy is true when a review keeps the team ──────

def test_the_sole_owner_warning_is_the_same_with_or_without_a_report_and_is_true(fk_client):
    c = fk_client
    msgs = {}
    for tag, with_report in (('plain', False), ('reported', True)):
        o, otok = _person(c, f'dr1_{tag}')
        tid, _code = _team(c, otok, f'Solo {tag}')
        c.post(f'/api/teams/{tid}/messages', json={'body': 'hi'}, headers=auth_headers(otok))
        rid = _report(tid) if with_report else None
        r = c.post(f'/api/teams/{tid}/leave', headers=auth_headers(otok))
        msgs[tag] = r.get_json()
        r = c.post(f'/api/teams/{tid}/leave', json={'delete_team': True}, headers=auth_headers(otok))
        assert r.get_json() == {'message': 'Left team'}
        db.session.expire_all()
        if with_report:
            # Kept for the review -- and the copy said it might be...
            assert db.session.get(A.Team, tid) is not None
            assert A.TeamMessage.query.filter_by(team_id=tid).count() >= 1
            db.session.get(A.Report, rid).status = 'actioned'
            db.session.commit()
            # ...and then it is deleted once the review has ended.
            assert A._reap_empty_ownerless_teams()['deleted'] == 1
        assert db.session.get(A.Team, tid) is None
    assert msgs['plain'] == msgs['reported']
    text_ = msgs['plain']['message']
    assert "it's kept only until that ends and then deleted" in text_
    assert 'permanently deletes it' not in text_
    assert 'report' not in text_.replace('safety review', '').lower()


def test_the_delete_screen_says_the_same_about_a_team_only_you_are_in():
    js = (ROOT / 'static' / 'app.js').read_text(encoding='utf-8')
    flat = ' '.join(js.split())
    assert ("'A team only you are in is deleted too. If any of it has to ' + \"be kept for a "
            "safety review or a legal hold, it's kept only until that ends \" + 'and then "
            "deleted.") in flat
    assert 'hand it over first' not in flat


# ── The reaper: no empty ownerless team survives, holds respected ─────────

def _empty_ownerless_team(name='orphan'):
    t = A.Team(name=name, created_by_user_id=None)
    db.session.add(t)
    db.session.flush()
    db.session.add(A.TeamCampfire(team_id=t.id, total_team_missions=0))
    db.session.add(A.TeamInviteCode(team_id=t.id, code=uuid.uuid4().hex[:6].upper()))
    db.session.commit()
    return t.id


def test_the_reaper_deletes_empty_ownerless_teams_and_nothing_else(fk_client):
    c = fk_client
    gone = _empty_ownerless_team('gone')
    pending = _empty_ownerless_team('pending')
    held = _empty_ownerless_team('held')
    photo_held = _empty_ownerless_team('photo_held')
    _report(pending)
    _report(held, status='closed', hold=True)
    _report(photo_held, status='closed', hold=True, subject_type='photo', subject_ref='x')
    o, otok = _person(c, 'reap_owner')
    owned_empty = A.Team(name='owned-empty', created_by_user_id=o)   # legacy: owner left
    db.session.add(owned_empty)
    db.session.commit()
    w = _ownerless_world(c, 'reap')                                    # ownerless, 2 members
    out = A._reap_empty_ownerless_teams()
    assert out == {'deleted': 1, 'kept_for_review': 3, 'skipped': 0}
    db.session.expire_all()
    assert db.session.get(A.Team, gone) is None
    for tid in (pending, held, photo_held, owned_empty.id, w['tid']):
        assert db.session.get(A.Team, tid) is not None, tid
    assert db.session.execute(text('PRAGMA foreign_key_check')).all() == []


def test_the_reaper_runs_in_the_retention_sweeper_and_records_its_pass(app):
    _empty_ownerless_team('swept')
    ok = A._run_sweeper_step('ownerless team cleanup', A._sweeper_ownerless_team_step,
                             lambda exc: A._record_retention_failure(
                                 A.RUN_OWNERLESS_TEAM_REAP, A.SOURCE_THREAD, exc))
    assert ok is not False
    run = A.RetentionRun.query.filter_by(kind=A.RUN_OWNERLESS_TEAM_REAP).one()
    assert run.outcome == 'ok' and run.deleted == 1
    assert run.detail == 'deleted=1 kept_for_review=0 skipped=0'
    assert A.Team.query.count() == 0
    assert A.RUN_OWNERLESS_TEAM_REAP not in A.RETENTION_KINDS
    src = (ROOT / 'app.py').read_text(encoding='utf-8')
    loop = src[src.index('def _retention_sweeper_loop'):src.index('def _start_retention_sweeper')]
    assert "_run_sweeper_step('ownerless team cleanup', _sweeper_ownerless_team_step" in loop


def test_a_failing_reap_rolls_back_whole_and_keeps_the_team(fk_client, monkeypatch):
    tid = _empty_ownerless_team('boom')
    real = A._qa_team_rows
    calls = {'n': 0}

    def boom(model, attr, team_id):
        calls['n'] += 1
        if calls['n'] == 4:
            raise RuntimeError('midway')
        return real(model, attr, team_id)
    monkeypatch.setattr(A, '_qa_team_rows', boom)
    with pytest.raises(RuntimeError):
        A._reap_empty_ownerless_teams()
    monkeypatch.undo()
    db.session.expire_all()
    assert db.session.get(A.Team, tid) is not None
    assert A.TeamInviteCode.query.filter_by(team_id=tid).count() == 1


# ── Trap-freedom: nobody can ever be unable to leave or delete ────────────
# Every person in every shape below gets out of every team and deletes their
# account using only their own requests (and, for a team report, the operator
# closing it -- a moderation hold about THEM is the only thing that may wait).

def _leave_everything_and_delete(c, uid, tok):
    """The person's own way out. Returns the list of answers it took."""
    steps = []
    h = auth_headers(tok)
    for _round in range(6):
        teams = c.get('/api/teams', headers=h).get_json() or []
        if not teams:
            break
        for t in teams:
            r = c.post(f"/api/teams/{t['id']}/leave", headers=h)
            steps.append(r.status_code)
            if r.status_code == 409:
                err = r.get_json()['error']
                body = {'no_owner': True} if err == 'owner_must_choose' else {'delete_team': True}
                r = c.post(f"/api/teams/{t['id']}/leave", json=body, headers=h)
                steps.append(r.status_code)
            assert r.status_code == 200, (uid, t['id'], r.get_json())
    r = c.delete('/api/me', json={'password': PW}, headers=h)
    steps.append(r.status_code)
    assert r.status_code == 200, (uid, r.get_json())
    return steps


def _delete_first(c, uid, tok):
    """Deleting straight away: at worst a 409 team_owned that Leave Team -> nobody
    always resolves, never any other refusal."""
    h = auth_headers(tok)
    r = c.delete('/api/me', json={'password': PW}, headers=h)
    if r.status_code == 409:
        assert r.get_json()['blocker_codes'] == ['team_owned'], r.get_json()
        return _leave_everything_and_delete(c, uid, tok)
    assert r.status_code == 200, r.get_json()
    return [200]


SHAPES = {
    # name: (owner band, [member bands], owner suspended?, members suspended?, report, owner_in)
    'child_only':        (None, ['child', 'child'], False, False, None, True),
    'unknown_age':       (None, [None, None, None], False, False, None, True),
    'adult_and_child':   ('adult', ['child'], False, False, None, True),
    'suspended_owner':   (None, [None], True, False, None, True),
    'suspended_members': (None, ['adult', None], False, True, None, True),
    'last_member':       (None, [], False, False, None, True),
    'report_open':       (None, [None], False, False, 'pending', True),
    'report_open_sole':  (None, [], False, False, 'pending', True),
    'legal_hold':        (None, ['child'], False, False, 'hold', True),
    'legal_hold_sole':   (None, [], False, False, 'hold', True),
    'legacy_owner_left': (None, ['child', None], False, False, None, False),
    'legacy_left_held':  (None, [None], False, False, 'hold', False),
}


def _shape(c, name):
    owner_band, bands, owner_sus, members_sus, report, owner_in = SHAPES[name]
    o, otok = _person(c, f'{name}_o', band=owner_band)
    tid, code = _team(c, otok, f'Team {name}')
    people = [(o, otok)]
    for i, band in enumerate(bands):
        uid, tok = _person(c, f'{name}_m{i}', band=band)
        _join(c, tok, tid, code)
        c.post(f'/api/teams/{tid}/messages', json={'body': f'm{i}'}, headers=auth_headers(tok))
        if members_sus:
            _suspend(uid)
        people.append((uid, tok))
    if owner_sus:
        _suspend(o)
    if not owner_in:
        A.TeamMembership.query.filter_by(team_id=tid, user_id=o).delete()
        db.session.commit()
    rid = None
    if report == 'pending':
        rid = _report(tid)
    elif report == 'hold':
        rid = _report(tid, status='closed', hold=True)
    return tid, people, rid


@pytest.mark.parametrize('order', ['owner_first', 'owner_last', 'delete_first'])
@pytest.mark.parametrize('name', sorted(SHAPES))
def test_nobody_is_ever_trapped(fk_client, name, order):
    c = fk_client
    tid, people, rid = _shape(c, name)
    seq = list(people) if order != 'owner_last' else list(people[1:]) + [people[0]]
    way_out = _delete_first if order == 'delete_first' else _leave_everything_and_delete
    for uid, tok in seq:
        way_out(c, uid, tok)
    db.session.expire_all()
    for uid, _tok in people:
        assert db.session.get(A.User, uid) is None, uid
    team = db.session.get(A.Team, tid)
    # Nobody ever became owner who was not a verified, unsuspended adult.
    assert team is None or team.created_by_user_id is None
    if rid is not None:
        # The evidence stays while the review or hold does.
        assert team is not None and db.session.get(A.Report, rid).team_id == tid
        assert A._reap_empty_ownerless_teams()['kept_for_review'] == 1
        rep = db.session.get(A.Report, rid)
        rep.status, rep.legal_hold = 'closed', False
        db.session.commit()
    A._reap_empty_ownerless_teams()
    db.session.expire_all()
    assert db.session.get(A.Team, tid) is None or A.TeamMembership.query.filter_by(
        team_id=tid).count() > 0
    assert db.session.execute(text('PRAGMA foreign_key_check')).all() == []


def test_the_only_owner_assigned_in_any_shape_is_a_verified_unsuspended_adult(fk_client):
    """adult_and_child: the adult owner leaves picking the child -> 400; the
    fallback for a legacy owner with one adult and one child picks the adult."""
    c = fk_client
    tid, people, _rid = _shape(c, 'adult_and_child')
    (o, otok), (kid, _ktok) = people
    r = c.post(f'/api/teams/{tid}/leave', json={'new_owner_user_id': kid}, headers=auth_headers(otok))
    assert r.status_code == 400 and r.get_json()['error'] == 'new_owner_not_eligible'
    owner = A.User(username='fb_legacy', password_hash='x')
    adult = A.User(username='fb_adult', password_hash='x', age_band=A.AGE_ADULT)
    child = A.User(username='fb_child', password_hash='x', age_band=A.AGE_CHILD)
    db.session.add_all([owner, adult, child])
    db.session.commit()
    t = A.Team(name='legacy', created_by_user_id=owner.id)
    db.session.add(t)
    db.session.flush()
    db.session.add_all([A.TeamMembership(team_id=t.id, user_id=child.id,
                                         joined_at=datetime.datetime(2026, 1, 1)),
                        A.TeamMembership(team_id=t.id, user_id=adult.id,
                                         joined_at=datetime.datetime(2026, 2, 1))])
    db.session.commit()
    assert A.delete_user_account(owner.id, dry_run=False)['executed']
    db.session.expire_all()
    assert db.session.get(A.Team, t.id).created_by_user_id == adult.id


# ── D-R5-3: the qa_smoke_ prefix is reserved at sign-up ───────────────────

TAKEN = {'error': 'That username is taken — try another.'}

LOOKALIKES = [
    'qa_smoke_actually_a_real_person', 'QA_SMOKE_x', 'Qa_Smoke_x', 'qa-smoke-x', 'qa.smoke.x',
    'qa smoke x', 'qasmoke', 'qa__smoke_x', ' qa_smoke_x', 'qa_sm0ke_x', 'qa_$moke_x',
    'qа_smoke_x',            # Cyrillic a
    'qa_smоke_x',            # Cyrillic o
    'qa_ѕmoke_x',            # Cyrillic s
    'qa​_smoke_x',           # zero-width space
    'ｑａ＿ｓｍｏｋｅ_x',   # fullwidth
    'qá_smöke_x',       # accents
    'qa­smoke',              # soft hyphen
]


@pytest.mark.parametrize('name', LOOKALIKES)
def test_signup_refuses_the_qa_prefix_and_its_lookalikes_like_a_taken_name(client, name):
    r = client.post('/api/register', json={'username': name, 'password': PW})
    assert r.status_code == 400 and r.get_json() == TAKEN, name
    assert A.User.query.count() == 0


def test_the_reservation_answer_is_identical_to_a_taken_name(client):
    client.post('/api/register', json={'username': 'already_here', 'password': PW})
    taken = client.post('/api/register', json={'username': 'already_here', 'password': PW})
    reserved = client.post('/api/register', json={'username': 'qa_smoke_me', 'password': PW})
    assert _strip(taken) == _strip(reserved)


# (Anything that FOLDS to "qasmoke..." is reserved, so e.g. "qa_smokey" is too:
# a deliberate, tiny collateral of failing closed.)
@pytest.mark.parametrize('name', ['aqa_smoke', 'smoke_qa', 'timhill',
                                  'qa', 'q_a', 'my_qa_smoke', 'qa_smoothie'])
def test_ordinary_names_are_unaffected(client, name):
    assert client.post('/api/register', json={'username': name, 'password': PW}).status_code == 201


def test_only_the_tooling_token_can_register_the_exact_prefix(client, monkeypatch):
    hdr = A.QA_PROVISIONING_HEADER
    # Wrong or empty tokens: refused like a taken name.
    for bad in ('', 'nope', A._QA_IN_PROCESS_TOKEN[:-1]):
        r = client.post('/api/register', json={'username': 'qa_smoke_t1', 'password': PW},
                        headers={hdr: bad})
        assert r.status_code == 400 and r.get_json() == TAKEN, bad
    # The in-process token: 201, and the id the tooling records.
    r = client.post('/api/register', json={'username': 'qa_smoke_t1', 'password': PW},
                    headers={hdr: A._QA_IN_PROCESS_TOKEN})
    assert r.status_code == 201
    uid = r.get_json()['user_id']
    assert db.session.get(A.User, uid).username == 'qa_smoke_t1'
    # Even the token cannot register a LOOKALIKE: only the exact prefix.
    for name in ('QA_SMOKE_t2', 'qa-smoke-t2', 'qа_smoke_t2'):
        r = client.post('/api/register', json={'username': name, 'password': PW},
                        headers={hdr: A._QA_IN_PROCESS_TOKEN})
        assert r.status_code == 400, name
    # The configured secret (CLI runs) also works; unset, it never matches ''.
    monkeypatch.setenv('STREAKFIT_QA_PROVISIONING_SECRET', 's3cret-for-tests')
    r = client.post('/api/register', json={'username': 'qa_smoke_t3', 'password': PW},
                    headers={hdr: 's3cret-for-tests'})
    assert r.status_code == 201
    monkeypatch.setenv('STREAKFIT_QA_PROVISIONING_SECRET', '')
    r = client.post('/api/register', json={'username': 'qa_smoke_t4', 'password': PW},
                    headers={hdr: ''})
    assert r.status_code == 400
    # An ordinary registration never returns an id.
    r = client.post('/api/register', json={'username': 'normal_person', 'password': PW},
                    headers={hdr: A._QA_IN_PROCESS_TOKEN})
    assert r.status_code == 201 and 'user_id' not in r.get_json()


def test_the_in_process_verifier_registers_and_records_provenance(app, tmp_path, monkeypatch):
    from scripts.verification._client import WsgiClient
    ledger = tmp_path / 'ledger.jsonl'
    monkeypatch.setenv('STREAKFIT_QA_LEDGER', str(ledger))
    api = WsgiClient(app)
    status, body = api.request('POST', '/api/register',
                               body={'username': 'qa_smoke_wsgi_1', 'password': PW})
    assert status == 201
    rec = [json.loads(line) for line in ledger.read_text().splitlines()]
    assert rec[0]['user_id'] == body['user_id'] and rec[0]['username'] == 'qa_smoke_wsgi_1'
    # A non-QA registration is not recorded.
    api.request('POST', '/api/register', body={'username': 'plain_wsgi', 'password': PW})
    assert len(ledger.read_text().splitlines()) == 1


# ── D-R5-3 follow-up: the cleanup needs provenance, and fails closed ──────

def _raw_user(name):
    """A user row the QA tooling never recorded (a real person's choice before
    the reservation, or a legacy account)."""
    u = A.User(username=name, password_hash='x')
    db.session.add(u)
    db.session.commit()
    return u.id


def _run_cleanup(monkeypatch, capsys, *argv):
    monkeypatch.setattr(sys, 'argv', ['cleanup_qa_smoke.py', *argv])
    C.main()
    return capsys.readouterr().out


def _solo_team(owner, name='Smoke Test run'):
    t = A.Team(name=name, created_by_user_id=owner)
    db.session.add(t)
    db.session.flush()
    db.session.add(A.TeamMembership(team_id=t.id, user_id=owner))
    db.session.add(A.TeamCampfire(team_id=t.id, total_team_missions=0))
    db.session.commit()
    return t.id


def test_a_forged_or_legacy_qa_name_without_provenance_is_never_deleted(fk_client, monkeypatch,
                                                                       capsys):
    """The round-5 reproduction, now refused: a real person holding a
    qa_smoke_ name (registered before the reservation) and a legacy QA account
    the ledger never saw. Both kept, listed by id only."""
    real = _raw_user('qa_smoke_actually_a_real_person')
    real_team = _solo_team(real, 'My real team')
    legacy = _raw_user('qa_smoke_a_1700000000_abc123')
    proven = _raw_user('qa_smoke_a_1800000000_def456')
    qa_tooling_created(proven, 'qa_smoke_a_1800000000_def456')
    out = _run_cleanup(monkeypatch, capsys, '--execute')
    db.session.expire_all()
    assert db.session.get(A.User, real) is not None
    assert db.session.get(A.Team, real_team) is not None
    assert db.session.get(A.User, legacy) is not None
    assert db.session.get(A.User, proven) is None             # positive control
    assert 'NO QA PROVENANCE (never deleted; read-only listing): 2 account(s)' in out
    assert f'account id={real:<6} reasons=no_qa_provenance' in out
    assert 'qa_smoke_actually_a_real_person' not in out[out.index('NO QA PROVENANCE'):]


def test_with_no_ledger_nothing_at_all_is_deleted(fk_client, monkeypatch, capsys, tmp_path):
    a = _raw_user('qa_smoke_x_1')
    b = _raw_user('qa_smoke_x_2')
    monkeypatch.setenv('STREAKFIT_QA_LEDGER', str(tmp_path / 'missing.jsonl'))
    out = _run_cleanup(monkeypatch, capsys, '--execute')
    assert 'NOTE: no QA provenance ledger at' in out
    assert 'Nothing has QA provenance, so nothing is deleted.' in out
    db.session.expire_all()
    assert db.session.get(A.User, a) is not None and db.session.get(A.User, b) is not None


@pytest.mark.parametrize('lines', [
    ['{"user_id": %(id)d, "username": "qa_smoke_other"}'],              # wrong name for the id
    ['{"user_id": %(id2)d, "username": "qa_smoke_amb_1"}'],             # right name, wrong id
    ['{"user_id": %(id)d, "username": "qa_smoke_amb_1"}',               # conflicting records
     '{"user_id": %(id)d, "username": "qa_smoke_amb_9"}'],
    ['{"user_id": "%(id)d", "username": "qa_smoke_amb_1"}'],            # id not an int
    ['not json at all'],
])
def test_ambiguous_provenance_fails_closed(fk_client, monkeypatch, capsys, tmp_path, lines):
    uid = _raw_user('qa_smoke_amb_1')
    ledger = tmp_path / 'ledger.jsonl'
    ledger.write_text('\n'.join(line % {'id': uid, 'id2': uid + 100} for line in lines) + '\n')
    monkeypatch.setenv('STREAKFIT_QA_LEDGER', str(ledger))
    _run_cleanup(monkeypatch, capsys, '--execute')
    db.session.expire_all()
    assert db.session.get(A.User, uid) is not None


def test_a_mixed_team_with_an_unproven_qa_name_is_never_deleted(fk_client, monkeypatch, capsys):
    """The QA owner has provenance; a member whose qa_smoke_ name does not is
    treated as a real person: the team and both accounts stay."""
    owner = _raw_user('qa_smoke_mix_owner')
    qa_tooling_created(owner, 'qa_smoke_mix_owner')
    forged = _raw_user('qa_smoke_mix_forged')
    tid = _solo_team(owner)
    db.session.add(A.TeamMessage(team_id=tid, sender_type='user', sender_user_id=forged,
                                 body='mine'))
    db.session.commit()
    assert A.delete_qa_smoke_team(tid, dry_run=True)['refusals'] == ['non_qa_smoke_person_linked']
    _run_cleanup(monkeypatch, capsys, '--execute', '--team-owner-policy=delete-qa-team')
    db.session.expire_all()
    assert db.session.get(A.Team, tid) is not None
    assert db.session.get(A.User, owner) is not None and db.session.get(A.User, forged) is not None
    # Once the forged name is (hypothetically) proven, the same team goes:
    # the refusal really was about provenance.
    qa_tooling_created(forged, 'qa_smoke_mix_forged')
    assert A.delete_qa_smoke_team(tid, dry_run=True)['refusals'] == []


def test_the_app_rules_fail_closed_without_any_provenance(app):
    owner = _raw_user('qa_smoke_nop_owner')
    tid = _solo_team(owner)
    A.app.config.pop('QA_PROVENANCE', None)
    assert 'owner_not_qa_smoke' in A.delete_qa_smoke_team(tid, dry_run=True)['refusals']
    assert A._team_involves_non_qa(tid) is True
