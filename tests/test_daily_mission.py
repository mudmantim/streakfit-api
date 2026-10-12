"""Step 1 (D50/D59/D68/D69/D70): the day's mission is stored, and every write
names the day it is for.

Mission days are still UTC dates here (Step 2 makes them local). The clock is
replaced with a settable one, years from the real date, so nothing can agree
with the system clock by accident.
"""
import datetime

import pytest

import app as appmod
from app import (BrainBoostAnswer, DailyCompletion, DailyEffort, DailyMission,
                 DailyMissionAward, User, db)
from conftest import auth_headers, register_and_login

D = datetime.date(2031, 5, 12)
ONE = datetime.timedelta(days=1)


class _Clock:
    """One UTC instant, settable by day and hour (the clock is one reading)."""

    def __init__(self, day=D, hour=12):
        self.day, self.hour = day, hour

    def now(self):
        return datetime.datetime.combine(self.day, datetime.time(self.hour, 0))


@pytest.fixture()
def clock(monkeypatch):
    c = _Clock()
    monkeypatch.setattr(appmod, 'server_clock', c)
    return c


def _user(client, name, skill=None):
    token = register_and_login(client, name)
    h = auth_headers(token)
    if skill:
        assert client.patch('/api/me', json={'skill_level': skill}, headers=h).status_code == 200
    uid = db.session.execute(db.select(User.id).where(User.username == name)).scalar()
    return uid, h


def _daily(client, h):
    r = client.get('/api/daily', headers=h)
    assert r.status_code == 200, r.get_json()
    return r.get_json()


def _keys(daily):
    return [e['key'] for e in daily['exercises']]


def _complete(client, h, key, day):
    return client.post(f'/api/daily/{key}/complete', headers=h,
                       json={'mission_date': day.isoformat()})


def _rows(uid):
    return sorted((str(d), k) for d, k in db.session.execute(
        db.select(DailyCompletion.date, DailyCompletion.exercise_key)
        .where(DailyCompletion.user_id == uid)).all())


def _awards(uid):
    return sorted(str(d) for d in db.session.execute(
        db.select(DailyMissionAward.mission_date)
        .where(DailyMissionAward.user_id == uid)).scalars())


def _seed(uid, day, keys):
    for i, k in enumerate(keys):
        db.session.add(DailyCompletion(
            user_id=uid, date=day, exercise_key=k,
            completed_at=datetime.datetime.combine(day, datetime.time(9, i))))
    db.session.commit()


def _tier_keys(tier, category=None):
    return [ex['key'] for cat, exs in appmod.EXERCISE_LIBRARY[tier].items()
            for ex in exs if category in (None, cat)]


def _ticks(daily):
    return sum(1 for e in daily['exercises'] if e['completed'])


# ── The stored five ──────────────────────────────────────────────────────────

def test_the_first_request_stores_the_five_the_old_code_would_have_shown(client, clock):
    uid, h = _user(client, 'dm_store')
    expected = [ex['key'] for ex in appmod.todays_mission(uid, 'beginner', D)['exercises']]
    daily = _daily(client, h)
    assert _keys(daily) == expected
    row = db.session.get(DailyMission, (uid, D))
    assert [s['key'] for s in row.slots] == expected
    assert [s['category'] for s in row.slots] == list(appmod._CATEGORIES)
    assert daily['mission_date'] == D.isoformat()
    assert daily['day_ends_at'] == (D + ONE).isoformat() + 'T00:00:00Z'
    assert _keys(_daily(client, h)) == expected
    assert db.session.query(DailyMission).filter_by(user_id=uid).count() == 1


def test_skill_changes_never_swap_todays_five(client, clock):
    uid, h = _user(client, 'dm_skill')
    keys = _keys(_daily(client, h))
    _complete(client, h, keys[0], D)
    assert client.patch('/api/me', json={'skill_level': 'advanced'}, headers=h).status_code == 200
    daily = _daily(client, h)
    assert _keys(daily) == keys
    assert daily['skill_level'] == 'advanced'
    assert daily['mission_skill_level'] == 'beginner'
    assert 'tomorrow' in daily['skill_note'].lower()
    assert _ticks(daily) == daily['completed_count'] == 1

    clock.day = D + ONE
    tomorrow = _daily(client, h)
    assert tomorrow['mission_skill_level'] == 'advanced'
    assert tomorrow['skill_note'] is None
    own = set(_tier_keys('advanced'))
    assert sum(k in own for k in _keys(tomorrow)) >= 3


# ── Effort, at every point of the mission ────────────────────────────────────

def _intermediate_on_day_two(client, name, clock):
    uid, h = _user(client, name, 'intermediate')
    clock.day = D - ONE
    for k in _keys(_daily(client, h)):
        assert _complete(client, h, k, D - ONE).status_code == 200
    clock.day = D
    return uid, h


@pytest.mark.parametrize('done', [0, 1, 2, 3, 4, 5])
def test_easing_keeps_completed_slots_and_redraws_only_the_rest(client, clock, done):
    uid, h = _intermediate_on_day_two(client, f'dm_ease_{done}', clock)
    before = _daily(client, h)
    assert before['effort']['level'] == 'usual'
    keys = _keys(before)
    for k in keys[:done]:
        assert _complete(client, h, k, D).status_code == 200

    r = client.put('/api/daily/effort', json={'level': 'easy'}, headers=h)
    assert r.status_code == 200
    body = r.get_json()
    after = _daily(client, h)
    new_keys = _keys(after)

    assert new_keys[:done] == keys[:done]                    # completed slots never move
    assert [e['category'] for e in after['exercises']] == list(appmod._CATEGORIES)
    assert _ticks(after) == after['completed_count'] == done
    if done < 5:
        assert body['applies'] == 'today'
        assert after['effort']['level'] == 'easy' and body['revision'] == 1
        for e in after['exercises'][done:]:                  # the redrawn ones are gentle
            assert appmod._EXERCISE_BY_KEY[e['key']]['impact'] != 'high'
    else:
        assert body['applies'] == 'tomorrow' and body['revision'] == 0
        assert new_keys == keys and after['effort']['next'] == 'easy'

    for k in new_keys[done:]:
        assert _complete(client, h, k, D).status_code == 200
    final = _daily(client, h)
    assert final['completed_count'] == _ticks(final) == 5
    assert _awards(uid).count(D.isoformat()) == 1
    assert len([r for r in _rows(uid) if r[0] == D.isoformat()]) == 5


# 0 done: a harder effort applies today (the five are rebuilt) -- see
# test_step1_remediation.test_effort_change_before_the_first_exercise_rebuilds_today.
@pytest.mark.parametrize('done', [1, 3, 5])
def test_a_harder_effort_is_kept_for_the_next_mission_day(client, clock, done):
    uid, h = _intermediate_on_day_two(client, f'dm_more_{done}', clock)
    keys = _keys(_daily(client, h))
    for k in keys[:done]:
        _complete(client, h, k, D)
    r = client.put('/api/daily/effort', json={'level': 'more'}, headers=h)
    assert r.status_code == 200 and r.get_json()['applies'] == 'tomorrow'
    assert 'tomorrow' in r.get_json()['message'].lower()
    daily = _daily(client, h)
    assert _keys(daily) == keys
    assert daily['effort']['level'] == 'usual' and daily['effort']['next'] == 'more'
    # Tier readiness counts finished "more" days from DailyEffort: nothing
    # about today may claim it was a "more" day.
    assert db.session.execute(db.select(DailyEffort.level).where(
        DailyEffort.user_id == uid, DailyEffort.date == D)).scalar() in (None, 'usual')

    clock.day = D + ONE
    assert _daily(client, h)['effort']['level'] == 'more'


def test_choosing_todays_level_again_cancels_a_change_kept_for_tomorrow(client, clock):
    uid, h = _intermediate_on_day_two(client, 'dm_cancel', clock)
    _daily(client, h)
    client.put('/api/daily/effort', json={'level': 'more'}, headers=h)
    r = client.put('/api/daily/effort', json={'level': 'usual'}, headers=h)
    assert r.get_json()['applies'] == 'today' and r.get_json()['next_effort'] is None
    clock.day = D + ONE
    assert _daily(client, h)['effort']['level'] == 'usual'


# ── Writes name their day ────────────────────────────────────────────────────

def test_a_tap_for_a_day_that_has_ended_is_refused_and_writes_nothing(client, clock):
    uid, h = _user(client, 'dm_stale')
    keys = _keys(_daily(client, h))
    for k in keys[:3]:
        assert _complete(client, h, k, D).status_code == 200
    before = _rows(uid)

    clock.day = D + ONE
    r = _complete(client, h, keys[3], D)
    assert r.status_code == 409
    body = r.get_json()
    assert body['error'] == 'day_changed'
    assert (body['mission_date'], body['today']) == (D.isoformat(), (D + ONE).isoformat())
    assert body['closed_completed_count'] == 3
    assert _rows(uid) == before and _awards(uid) == []


def test_a_stale_key_that_is_also_in_the_new_days_five_is_not_credited_there(client, clock):
    """The silent D50 misfile: about a fifth of stale keys are also in the next
    day's list, and used to return 200 and land on the new day."""
    for i in range(40):
        clock.day = D
        uid, h = _user(client, f'dm_overlap_{i}')
        today = _keys(_daily(client, h))
        clock.day = D + ONE
        shared = [k for k in today if k in _keys(_daily(client, h))]
        if shared:
            break
    else:
        pytest.skip('no overlapping key found in 40 users')
    r = _complete(client, h, shared[0], D)
    assert r.status_code == 409 and r.get_json()['error'] == 'day_changed'
    assert _rows(uid) == []


@pytest.mark.parametrize('body,error', [
    (None, 'mission_date_required'),
    ({}, 'mission_date_required'),
    ({'mission_date': 'yesterday'}, 'invalid_mission_date'),
    ({'mission_date': 20310512}, 'invalid_mission_date'),
    ({'mission_date': '2031-05-12T00:00'}, 'invalid_mission_date'),
    ({'mission_date': (D + ONE).isoformat()}, 'mission_date_in_future'),
])
def test_a_write_that_does_not_name_a_valid_day_is_refused(client, clock, body, error):
    uid, h = _user(client, f'dm_bad_{abs(hash(str(body))) % 10**6}')
    key = _keys(_daily(client, h))[0]
    r = client.post(f'/api/daily/{key}/complete', headers=h, json=body)
    assert (r.status_code, r.get_json()['error']) == (400, error)
    r = client.post('/api/brain-boost/answer', headers=h,
                    json={'selected_index': 0, **(body or {})})
    assert (r.status_code, r.get_json()['error']) == (400, error)
    assert _rows(uid) == []
    assert db.session.query(BrainBoostAnswer).filter_by(user_id=uid).count() == 0


def test_an_exercise_outside_todays_five_is_mission_changed_not_an_error(client, clock):
    uid, h = _user(client, 'dm_changed')
    keys = _keys(_daily(client, h))
    other = next(k for k in _tier_keys('beginner') if k not in keys)
    r = _complete(client, h, other, D)
    assert r.status_code == 409 and r.get_json()['error'] == 'mission_changed'
    r = _complete(client, h, 'not_a_real_exercise', D)
    assert r.status_code == 400
    assert _rows(uid) == []


def test_a_retry_of_a_saved_completion_is_idempotent_even_after_midnight(client, clock):
    uid, h = _user(client, 'dm_retry')
    keys = _keys(_daily(client, h))
    for k in keys:
        assert _complete(client, h, k, D).status_code == 200
    xp = db.session.get(User, uid).xp_total
    assert _awards(uid) == [D.isoformat()]

    again = _complete(client, h, keys[4], D).get_json()     # response lost, re-sent
    assert again['already_recorded'] is True and again['day_changed'] is False

    clock.day = D + ONE                                     # ... or re-sent after midnight
    r = _complete(client, h, keys[4], D)
    assert r.status_code == 200
    body = r.get_json()
    assert body['already_recorded'] is True and body['day_changed'] is True
    assert body['completed_count'] == 5 and body['mission_date'] == D.isoformat()
    assert db.session.get(User, uid).xp_total == xp
    assert _awards(uid) == [D.isoformat()]
    assert {d for d, _ in _rows(uid)} == {D.isoformat()}


def test_a_request_reads_the_clock_once(client, monkeypatch):
    """A request straddling midnight validates, stamps and counts against one
    day. The clock here moves to the next day after its first reading."""
    monkeypatch.setattr(appmod, 'server_clock', _Clock())
    uid, h = _user(client, 'dm_snapshot')
    keys = _keys(_daily(client, h))

    class _Straddling(_Clock):
        reads = 0

        def now(self):
            _Straddling.reads += 1
            return datetime.datetime.combine(D if _Straddling.reads == 1 else D + ONE,
                                             datetime.time(23, 59, 59))

    monkeypatch.setattr(appmod, 'server_clock', _Straddling())
    r = _complete(client, h, keys[0], D)
    assert r.status_code == 200, r.get_json()
    assert _Straddling.reads == 1
    assert _rows(uid) == [(D.isoformat(), keys[0])]


# ── Brain Boost ──────────────────────────────────────────────────────────────

def _answer(client, h, index, day):
    return client.post('/api/brain-boost/answer', headers=h,
                       json={'selected_index': index, 'mission_date': day.isoformat()})


def test_brain_boost_is_graded_against_the_question_that_was_shown(client, clock, monkeypatch):
    uid, h = _user(client, 'dm_bb_shown')
    shown = _daily(client, h)['brain_boost']
    stored = db.session.get(DailyMission, (uid, D)).brain_boost
    assert shown['question'] == stored['question'] and shown['options'] == stored['options']
    # The content library changes during the day (a deploy).
    monkeypatch.setattr(appmod, 'get_daily_brain_boost', lambda *a, **k: {
        'question': 'replaced', 'options': ['a', 'b'], 'correct_index': 1 - (stored['correct_index'] % 2),
        'explanation': 'x'})
    r = _answer(client, h, stored['correct_index'], D)
    assert r.status_code == 200 and r.get_json()['correct'] is True


def test_brain_boost_answered_after_midnight_is_refused_and_keeps_the_new_days_answer(client, clock):
    uid, h = _user(client, 'dm_bb_midnight')
    _daily(client, h)
    clock.day = D + ONE
    r = _answer(client, h, 0, D)
    assert r.status_code == 409 and r.get_json()['error'] == 'day_changed'
    assert db.session.query(BrainBoostAnswer).filter_by(user_id=uid).count() == 0
    assert _daily(client, h)['brain_boost']['answered'] is False
    assert _answer(client, h, 0, D + ONE).status_code == 200
    assert [str(a.date) for a in BrainBoostAnswer.query.filter_by(user_id=uid)] == [(D + ONE).isoformat()]


def test_brain_boost_retry_returns_the_stored_answer_on_any_day(client, clock):
    uid, h = _user(client, 'dm_bb_retry')
    boost = _daily(client, h)['brain_boost']
    wrong = (db.session.get(DailyMission, (uid, D)).brain_boost['correct_index'] + 1) % len(boost['options'])
    first = _answer(client, h, wrong, D).get_json()
    assert first['correct'] is False
    again = _answer(client, h, (wrong + 1) % len(boost['options']), D).get_json()
    assert again['already_recorded'] is True and again['correct'] is False
    clock.day = D + ONE
    late = _answer(client, h, 0, D)
    assert late.status_code == 200 and late.get_json()['day_changed'] is True
    assert BrainBoostAnswer.query.filter_by(user_id=uid).count() == 1


# ── Guests ───────────────────────────────────────────────────────────────────

def test_the_demo_mission_names_its_day_and_when_it_ends(client, clock):
    first = client.get('/api/demo/daily').get_json()
    assert first['mission_date'] == D.isoformat()
    assert first['day_ends_at'] == (D + ONE).isoformat() + 'T00:00:00Z'
    clock.day = D + ONE
    assert client.get('/api/demo/daily').get_json()['mission_date'] == (D + ONE).isoformat()


# ── History and the day already in progress at deploy ────────────────────────

def _dump(uid):
    return {
        'completions': sorted((c.id, str(c.date), c.exercise_key, str(c.completed_at))
                              for c in DailyCompletion.query.filter_by(user_id=uid)),
        'awards': sorted((str(a.mission_date), str(a.created_at))
                         for a in DailyMissionAward.query.filter_by(user_id=uid)),
        'answers': sorted((a.id, str(a.date), a.correct, a.points_earned, str(a.answered_at))
                          for a in BrainBoostAnswer.query.filter_by(user_id=uid)),
        'efforts': sorted((e.id, str(e.date), e.level) for e in DailyEffort.query.filter_by(user_id=uid)),
    }


def _seed_history(uid):
    for back in (3, 2, 1):
        day = D - back * ONE
        _seed(uid, day, [ex['key'] for ex in
                         appmod.get_daily_exercises(uid, day.isoformat(), 'beginner')])
        db.session.add(DailyMissionAward(user_id=uid, mission_date=day,
                                         created_at=datetime.datetime.combine(day, datetime.time(10))))
        db.session.add(BrainBoostAnswer(user_id=uid, date=day, correct=True, points_earned=10,
                                        answered_at=datetime.datetime.combine(day, datetime.time(11))))
    db.session.add(DailyEffort(user_id=uid, date=D - 2 * ONE, level='easy'))
    db.session.commit()


def test_history_is_never_rewritten_and_a_day_in_progress_keeps_its_five(client, clock):
    uid, h = _user(client, 'dm_history')
    _seed_history(uid)
    planned = [ex['key'] for ex in appmod.todays_mission(uid, 'beginner', D)['exercises']]
    _seed(uid, D, planned[:2])                     # two done before the deploy
    past_before = _dump(uid)
    stats_before = appmod.get_user_stats(uid)

    daily = _daily(client, h)
    assert _keys(daily) == planned                 # exactly the five on screen before
    assert daily['completed_count'] == _ticks(daily) == 2
    for k in planned[2:]:
        assert _complete(client, h, k, D).status_code == 200

    after = _dump(uid)
    for table in past_before:
        assert past_before[table] == [r for r in after[table]
                                      if r in past_before[table]], table
    assert [r for r in after['completions'] if r[1] < D.isoformat()] == \
        [r for r in past_before['completions'] if r[1] < D.isoformat()]
    assert stats_before['best_streak'] == 3
    assert appmod.get_user_stats(uid)['current_streak'] == 4
    assert _awards(uid) == [(D - k * ONE).isoformat() for k in (3, 2, 1, 0)]


def test_progress_from_a_mid_day_switch_is_adopted_and_the_bonus_waits_for_five_slots(client, clock):
    """Deploy day after an old-code tier switch: today already holds completions
    that are not in the computed five, including two in one category. Each
    category's first is adopted into its slot; the bonus needs all five slots,
    not any five rows (D68)."""
    uid, h = _user(client, 'dm_adopt')
    upper = _tier_keys('advanced', 'upper_body')[:2]
    core = _tier_keys('advanced', 'core')[:1]
    _seed(uid, D, upper + core)                   # 3 rows, 2 categories

    daily = _daily(client, h)
    slots = {e['category']: e for e in daily['exercises']}
    assert slots['upper_body']['key'] == upper[0] and slots['upper_body']['completed']
    assert slots['core']['key'] == core[0] and slots['core']['completed']
    assert daily['completed_count'] == _ticks(daily) == 2

    remaining = [e['key'] for e in daily['exercises'] if not e['completed']]
    for k in remaining[:2]:
        body = _complete(client, h, k, D).get_json()
    assert len(_rows(uid)) == 5 and body['completed_count'] == 4
    assert _awards(uid) == []                      # five rows, four slots: no bonus yet
    last = _complete(client, h, remaining[2], D).get_json()
    assert last['completed_count'] == 5
    assert _awards(uid) == [D.isoformat()]
    assert any(e['event_type'] == 'mission_complete' for e in last['progress_events'])


def test_easing_around_a_completed_explosive_slot_still_follows_the_days_rules(client, clock):
    """The redrawn slots are chosen with the whole day's rules applied to the
    combined five, including the completed slots that cannot change: they stay
    gentle, and they still avoid yesterday's exercises."""
    checked = 0
    for i in range(60):
        clock.day = D - ONE
        uid, h = _user(client, f'dm_explosive_{i}', 'intermediate')
        yesterday = _keys(_daily(client, h))
        for k in yesterday:
            _complete(client, h, k, D - ONE)
        clock.day = D
        daily = _daily(client, h)
        explosive = [n for n, e in enumerate(daily['exercises'])
                     if appmod._EXERCISE_BY_KEY[e['key']]['impact'] == 'high']
        if not explosive:
            continue
        done = [daily['exercises'][n]['key'] for n in explosive]
        for k in done:
            _complete(client, h, k, D)
        assert client.put('/api/daily/effort', json={'level': 'easy'}, headers=h).status_code == 200
        redrawn = [e['key'] for e in _daily(client, h)['exercises'] if e['key'] not in done]
        assert all(appmod._EXERCISE_BY_KEY[k]['impact'] != 'high' for k in redrawn)
        assert not set(redrawn) & set(yesterday), (i, redrawn, yesterday)
        checked += 1
    assert checked >= 5
