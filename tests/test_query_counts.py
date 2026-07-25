"""Query-count regression tests for the WS3 N+1 fixes. These count SQL statements
issued while serving a request; under the old db.session.get(User, id)-in-a-loop
pattern the counts scaled with the number of rows (1+N). The batch _usernames_for_ids
fix keeps them flat, so a low upper-bound assertion guards against regression.
"""
import contextlib

from sqlalchemy import event

from app import db, User, Team, TeamMembership, TeamMoment, TeamMessage
from conftest import register_and_login, auth_headers


@contextlib.contextmanager
def count_queries():
    n = [0]

    def _cb(conn, cursor, statement, params, context, executemany):
        n[0] += 1

    event.listen(db.engine, "after_cursor_execute", _cb)
    try:
        yield n
    finally:
        event.remove(db.engine, "after_cursor_execute", _cb)


def _uid(username):
    return User.query.filter_by(username=username).first().id


def test_get_team_moments_is_flat_not_n_plus_1(client):
    token = register_and_login(client, "moments_req")
    rid = _uid("moments_req")
    t = Team(name="QT", created_by_user_id=rid)
    db.session.add(t)
    db.session.commit()
    db.session.add(TeamMembership(team_id=t.id, user_id=rid))
    for i in range(12):
        u = User(username=f"m_subject_{i}", password_hash="x")
        db.session.add(u)
        db.session.commit()
        db.session.add(TeamMoment(team_id=t.id, moment_type="member_joined", subject_user_id=u.id))
    db.session.commit()

    with count_queries() as n:
        resp = client.get(f"/api/teams/{t.id}/moments", headers=auth_headers(token))
    assert resp.status_code == 200
    assert len(resp.get_json()) == 12
    assert n[0] <= 6, f"query count {n[0]} scales with rows — N+1 regressed"


def test_get_team_messages_is_flat_not_n_plus_1(client):
    token = register_and_login(client, "msg_req")
    rid = _uid("msg_req")
    t = Team(name="QT2", created_by_user_id=rid)
    db.session.add(t)
    db.session.commit()
    db.session.add(TeamMembership(team_id=t.id, user_id=rid))
    for i in range(12):
        u = User(username=f"sender_{i}", password_hash="x")
        db.session.add(u)
        db.session.commit()
        db.session.add(TeamMessage(team_id=t.id, sender_type="user", sender_user_id=u.id, body="hi"))
    db.session.commit()

    with count_queries() as n:
        resp = client.get(f"/api/teams/{t.id}/messages", headers=auth_headers(token))
    assert resp.status_code == 200
    assert len(resp.get_json()) == 12
    assert n[0] <= 6, f"query count {n[0]} scales with rows — N+1 regressed"
