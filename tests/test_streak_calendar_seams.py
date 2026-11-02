"""Streaks across calendar seams, end to end.

test_day_seams.py proves the streak walks agree with an oracle on random
histories; this file drives the real write path (five completions through
/api/daily/<key>/complete) and reads the result back from /api/me, at the
seams where a date calculation usually goes wrong: a year boundary, a leap
day, a February that has none, a finish one minute before midnight followed
by one just after it, and the grace day that keeps a streak alive while today
is still open.

The clock is set to minute precision, years from the real date, so nothing
can agree with the system clock by accident.
"""
import datetime

import pytest

import app as appmod
from app import User, db
from conftest import auth_headers, register_and_login

ONE = datetime.timedelta(days=1)


class _Clock:
    def __init__(self):
        self.at = datetime.datetime(2031, 6, 1, 12, 0)

    def now(self):
        return self.at

    def set(self, day, hour=12, minute=0):
        self.at = datetime.datetime.combine(day, datetime.time(hour, minute))


@pytest.fixture()
def clock(monkeypatch):
    c = _Clock()
    monkeypatch.setattr(appmod, 'server_clock', c)
    return c


def _user(client, name):
    h = auth_headers(register_and_login(client, name))
    uid = db.session.execute(db.select(User.id).where(User.username == name)).scalar()
    return uid, h


def _finish_day(client, h, clock, day, hour=12, minute=0, count=5):
    """Complete `count` of the day's five through the API, at day/hour:minute."""
    clock.set(day, hour, minute)
    r = client.get('/api/daily', headers=h)
    assert r.status_code == 200, r.get_json()
    for e in r.get_json()['exercises'][:count]:
        done = client.post(f"/api/daily/{e['key']}/complete", headers=h,
                           json={'mission_date': day.isoformat()})
        assert done.status_code == 200, done.get_json()


def _me(client, h, clock, day, hour=12, minute=0):
    clock.set(day, hour, minute)
    r = client.get('/api/me', headers=h)
    assert r.status_code == 200, r.get_json()
    body = r.get_json()
    return body['current_streak'], body['best_streak'], body['total_missions']


def test_a_streak_runs_through_new_year(client, clock):
    _, h = _user(client, 'seam_newyear')
    for day in (datetime.date(2031, 12, 30), datetime.date(2031, 12, 31), datetime.date(2032, 1, 1)):
        _finish_day(client, h, clock, day)
    assert _me(client, h, clock, datetime.date(2032, 1, 1)) == (3, 3, 3)


def test_a_streak_runs_through_a_leap_day(client, clock):
    _, h = _user(client, 'seam_leap')
    for day in (datetime.date(2032, 2, 28), datetime.date(2032, 2, 29), datetime.date(2032, 3, 1)):
        _finish_day(client, h, clock, day)
    assert _me(client, h, clock, datetime.date(2032, 3, 1)) == (3, 3, 3)


def test_feb_28_to_mar_1_is_consecutive_in_a_common_year(client, clock):
    _, h = _user(client, 'seam_common')
    for day in (datetime.date(2031, 2, 28), datetime.date(2031, 3, 1)):
        _finish_day(client, h, clock, day)
    assert _me(client, h, clock, datetime.date(2031, 3, 1)) == (2, 2, 2)


def test_one_minute_before_and_after_midnight_are_two_mission_days(client, clock):
    _, h = _user(client, 'seam_midnight')
    d = datetime.date(2031, 7, 14)
    _finish_day(client, h, clock, d, 23, 59)
    _finish_day(client, h, clock, d + ONE, 0, 1)
    assert _me(client, h, clock, d + ONE, 0, 2) == (2, 2, 2)


def test_today_still_open_keeps_yesterdays_streak_and_a_whole_missed_day_ends_it(client, clock):
    _, h = _user(client, 'seam_grace')
    d = datetime.date(2031, 9, 1)
    for i in range(3):
        _finish_day(client, h, clock, d + i * ONE)
    # The next day has started and nothing is done yet: never punish who showed up.
    assert _me(client, h, clock, d + 3 * ONE, 23, 59) == (3, 3, 3)
    # A whole day passed without a mission: the current streak ends, the best is kept.
    assert _me(client, h, clock, d + 4 * ONE, 0, 0) == (0, 3, 3)
    _finish_day(client, h, clock, d + 4 * ONE)
    assert _me(client, h, clock, d + 4 * ONE) == (1, 3, 4)


def test_four_of_five_is_not_a_mission_day(client, clock):
    _, h = _user(client, 'seam_four')
    d = datetime.date(2031, 10, 1)
    _finish_day(client, h, clock, d)
    _finish_day(client, h, clock, d + ONE, count=4)
    assert _me(client, h, clock, d + ONE) == (1, 1, 1)
    # ... and the day after, the four-exercise day counts as missed.
    assert _me(client, h, clock, d + 2 * ONE) == (0, 1, 1)


def test_a_completion_for_yesterday_sent_after_midnight_does_not_extend_the_streak(client, clock):
    _, h = _user(client, 'seam_stale')
    d = datetime.date(2031, 11, 5)
    _finish_day(client, h, clock, d)
    clock.set(d + ONE, 23, 50)
    fifth = client.get('/api/daily', headers=h).get_json()['exercises'][4]['key']
    _finish_day(client, h, clock, d + ONE, 23, 55, count=4)
    # The fifth tap for the day that has ended is refused, not credited late.
    clock.set(d + 2 * ONE, 0, 6)
    r = client.post(f'/api/daily/{fifth}/complete', headers=h,
                    json={'mission_date': (d + ONE).isoformat()})
    assert r.status_code == 409 and r.get_json()['error'] == 'day_changed'
    assert r.get_json()['closed_completed_count'] == 4
    assert _me(client, h, clock, d + 2 * ONE, 0, 7) == (0, 1, 1)


def test_best_streak_survives_a_longer_gap_and_a_shorter_comeback(client, clock):
    _, h = _user(client, 'seam_best')
    d = datetime.date(2031, 3, 30)
    for i in range(4):                      # Mar 30 .. Apr 2: a month seam inside the run
        _finish_day(client, h, clock, d + i * ONE)
    comeback = d + 20 * ONE
    for i in range(2):
        _finish_day(client, h, clock, comeback + i * ONE)
    assert _me(client, h, clock, comeback + ONE) == (2, 4, 6)
