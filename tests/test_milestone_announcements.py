"""Every milestone announces itself at the moment it is crossed.

_milestones_crossed exists so that no milestone lands silently. But the
completion path never supplied the `days_active` or `best_streak` metrics, so
Three Days, A Full Week and Two Weeks were unlocked in the Memory Book and
never in `milestones_unlocked` (E2E R1 D10, reproduced twice). The Brain
Boost answer, which can also be a day's first activity, had the same gap for
`days_active`.

The metric definitions are the Memory Book's: a day is active if it has any
exercise completion or Brain Boost answer; best_streak is the longest run of
full-mission days. A milestone is announced once, by the action that crosses
it.
"""
import datetime

from app import BrainBoostAnswer, DailyCompletion, User, db
from conftest import auth_headers, register_and_login

FIVE = ['a1', 'a2', 'a3', 'a4', 'a5']


def _uid(username):
    return db.session.execute(db.select(User.id).where(User.username == username)).scalar_one()


def _seed(username, days_ago, keys):
    uid = _uid(username)
    day = datetime.date.today() - datetime.timedelta(days=days_ago)
    for k in keys:
        db.session.add(DailyCompletion(user_id=uid, date=day, exercise_key=k))
    db.session.commit()


def _keys(client, token):
    return [e['key'] for e in client.get('/api/daily', headers=auth_headers(token)).get_json()['exercises']]


def _complete(client, token, key):
    return client.post(f'/api/daily/{key}/complete', headers=auth_headers(token)).get_json()


def _announced(payload):
    return {m['key'] for m in payload['milestones_unlocked']}


def test_three_days_is_announced_by_the_first_completion_of_the_third_day(client):
    token = register_and_login(client, 'ms_days3')
    _seed('ms_days3', 5, FIVE[:1])
    _seed('ms_days3', 2, FIVE[:1])
    keys = _keys(client, token)

    first = _complete(client, token, keys[0])
    second = _complete(client, token, keys[1])

    assert 'days_3' in _announced(first)
    assert 'days_3' not in _announced(second)


def test_a_full_week_is_announced_by_the_completion_that_makes_seven(client):
    token = register_and_login(client, 'ms_week')
    for d in range(1, 7):
        _seed('ms_week', d, FIVE)
    keys = _keys(client, token)

    payloads = [_complete(client, token, k) for k in keys]

    assert [('streak_7' in _announced(p)) for p in payloads] == [False] * 4 + [True]


def test_two_weeks_is_announced_by_the_completion_that_makes_fourteen(client):
    token = register_and_login(client, 'ms_fortnight')
    for d in range(1, 14):
        _seed('ms_fortnight', d, FIVE)
    keys = _keys(client, token)

    payloads = [_complete(client, token, k) for k in keys]

    assert 'streak_14' in _announced(payloads[-1])
    assert 'streak_7' not in _announced(payloads[-1])   # crossed long ago


def test_a_brain_boost_that_is_the_days_first_activity_can_cross_three_days(client):
    token = register_and_login(client, 'ms_bb')
    _seed('ms_bb', 4, FIVE[:1])
    _seed('ms_bb', 1, FIVE[:1])

    r = client.post('/api/brain-boost/answer', json={'selected_index': 0},
                    headers=auth_headers(token))
    assert r.status_code == 200
    assert 'days_3' in _announced(r.get_json())


def test_a_brain_boost_after_todays_completion_does_not_announce_the_day_again(client):
    token = register_and_login(client, 'ms_bb_late')
    _seed('ms_bb_late', 4, FIVE[:1])
    _seed('ms_bb_late', 1, FIVE[:1])
    first = _complete(client, token, _keys(client, token)[0])
    assert 'days_3' in _announced(first)

    r = client.post('/api/brain-boost/answer', json={'selected_index': 0},
                    headers=auth_headers(token)).get_json()
    assert 'days_3' not in _announced(r)
    assert db.session.query(BrainBoostAnswer).count() == 1


def test_a_request_that_straddles_midnight_judges_the_day_it_stamped(client, monkeypatch):
    """The route stamps its row with its own `today`; the milestone check must
    use that date, not re-read a clock that may have moved past midnight."""
    import app as appmod
    token = register_and_login(client, 'ms_midnight')
    _seed('ms_midnight', 5, FIVE[:1])
    _seed('ms_midnight', 2, FIVE[:1])
    keys = _keys(client, token)
    real_today = datetime.date.today()

    class _Tomorrow(datetime.date):
        calls = 0

        @classmethod
        def today(cls):
            # First call is the route's own stamp; every later call is "after midnight".
            cls.calls += 1
            return real_today if cls.calls == 1 else real_today + datetime.timedelta(days=1)

    monkeypatch.setattr(appmod, 'date', _Tomorrow)
    first = _complete(client, token, keys[0])
    assert 'days_3' in _announced(first)
