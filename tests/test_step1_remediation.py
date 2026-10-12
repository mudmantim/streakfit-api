"""Step 1 review remediation: regression tests for each confirmed finding.

M2  one authoritative UTC instant per request (day and timestamp agree).
M1  the server says how long the day has left, from that same instant.
M3  a stored mission survives an exercise being renamed or removed.
     + the effort tie-break, the deletion race, export, tier readiness,
       historical Brain Boost answers, out-of-date clients, and the approved
       policy: skill and effort may change freely before the first exercise.
"""
import datetime
import json
import os
import subprocess
import sys

import pytest
from sqlalchemy.exc import IntegrityError

import app as appmod
from app import (BrainBoostAnswer, DailyCompletion, DailyEffort,
                 DailyMissionAward, User, db)
from conftest import auth_headers, register_and_login

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
D = datetime.date(2031, 5, 12)
ONE = datetime.timedelta(days=1)


class _Instant:
    """A clock that is one UTC instant (naive, like every *_at column)."""

    def __init__(self, at=None):
        self.at = at or datetime.datetime(2031, 5, 12, 12, 0)

    def now(self):
        return self.at

    # The pre-remediation interface, derived from the same instant, so these
    # tests fail on the old code for the defect they describe, not for a
    # missing method.
    def today(self):
        return self.at.date()

    def utcnow(self):
        return self.at


@pytest.fixture()
def clock(monkeypatch):
    c = _Instant()
    monkeypatch.setattr(appmod, 'server_clock', c)
    return c


def _user(client, name, skill=None):
    token = register_and_login(client, name)
    h = auth_headers(token)
    if skill:
        assert client.patch('/api/me', json={'skill_level': skill}, headers=h).status_code == 200
    return db.session.execute(db.select(User.id).where(User.username == name)).scalar(), h


def _daily(client, h):
    r = client.get('/api/daily', headers=h)
    assert r.status_code == 200, r.get_json()
    return r.get_json()


def _keys(daily):
    return [e['key'] for e in daily['exercises']]


def _complete(client, h, key, day):
    return client.post(f'/api/daily/{key}/complete', headers=h, json={'mission_date': day.isoformat()})


# ── M2: one instant ──────────────────────────────────────────────────────────

def test_the_day_and_the_timestamp_come_from_one_instant(client, monkeypatch):
    """A clock that moves on every read: the request must read it once, so the
    stamped date and completed_at agree even one microsecond before midnight."""
    before = datetime.datetime(2031, 5, 12, 23, 59, 59, 999999)

    class _Ticking:
        reads = 0

        def now(self):
            _Ticking.reads += 1
            return before + datetime.timedelta(microseconds=2 * (_Ticking.reads - 1))

    monkeypatch.setattr(appmod, 'server_clock', _Instant(before - datetime.timedelta(minutes=1)))
    uid, h = _user(client, 'r_instant')
    key = _keys(_daily(client, h))[0]
    monkeypatch.setattr(appmod, 'server_clock', _Ticking())
    r = _complete(client, h, key, D)
    assert r.status_code == 200, r.get_json()
    assert _Ticking.reads == 1
    row = DailyCompletion.query.filter_by(user_id=uid).one()
    assert row.date == D and row.completed_at.date() == D and row.completed_at == before


@pytest.mark.parametrize('tz', ['Pacific/Kiritimati', 'Etc/GMT+12', 'America/Detroit'])
def test_the_mission_day_is_the_utc_date_whatever_the_process_timezone(tz):
    """At any real moment, at least one of +14 / -12 has a local date that is
    not the UTC date. The mission day must still be the UTC date, and the day
    must not already be over (that was the client reload loop, M1)."""
    code = (
        "import os, json, datetime\n"
        "import app as A\n"
        "with A.app.test_request_context('/'):\n"
        "    day, now = A.server_today(), A.server_now()\n"
        "    utc = datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None)\n"
        "    print(json.dumps({'day': day.isoformat(), 'utc_day': utc.date().isoformat(),\n"
        "        'local_day': datetime.date.today().isoformat(),\n"
        "        'ends_after_now': A.mission_day_ends_at(day) > now,\n"
        "        'seconds_left_ok': 0 < (A.mission_day_ends_at(day) - now).total_seconds() <= 86400}))\n")
    env = {**os.environ, 'TZ': tz, 'DATABASE_URL': 'sqlite://', 'SECRET_KEY': 't',
           'JWT_SECRET_KEY': 't', 'PYTHONPATH': REPO}
    out = subprocess.run([sys.executable, '-c', code], cwd=REPO, env=env, capture_output=True, text=True)
    assert out.returncode == 0, out.stderr[-2000:]
    got = json.loads(out.stdout.strip().splitlines()[-1])
    assert got['day'] == got['utc_day'], got
    assert got['ends_after_now'] and got['seconds_left_ok'], got


# ── M1: the server says how long the day has left ───────────────────────────

def test_daily_and_demo_say_how_long_the_day_has_left(client, clock):
    clock.at = datetime.datetime(2031, 5, 12, 23, 59, 30)
    uid, h = _user(client, 'r_left')
    assert _daily(client, h)['day_ends_in_s'] == 30
    assert client.get('/api/demo/daily').get_json()['day_ends_in_s'] == 30
    clock.at = datetime.datetime(2031, 5, 13, 0, 0, 0)
    assert _daily(client, h)['day_ends_in_s'] == 86400


# ── M3: a renamed or removed exercise ───────────────────────────────────────

def _drop(monkeypatch, *keys):
    """What a deploy that removes exercises does: they leave the library, and
    the key index built from it."""
    library = {tier: {cat: [ex for ex in exs if ex['key'] not in keys] for cat, exs in cats.items()}
               for tier, cats in appmod.EXERCISE_LIBRARY.items()}
    monkeypatch.setattr(appmod, 'EXERCISE_LIBRARY', library)
    monkeypatch.setattr(appmod, '_EXERCISE_BY_KEY', {
        ex['key']: ex for cats in library.values() for exs in cats.values() for ex in exs})
    return appmod._EXERCISE_BY_KEY


def test_an_unfinished_slot_whose_exercise_was_removed_is_redrawn(client, clock, monkeypatch):
    uid, h = _user(client, 'r_gone_open')
    keys = _keys(_daily(client, h))
    assert _complete(client, h, keys[0], D).status_code == 200
    _drop(monkeypatch, keys[2])
    daily = _daily(client, h)
    new = _keys(daily)
    assert new[0] == keys[0] and new[2] != keys[2]
    assert new[2] in appmod._EXERCISE_BY_KEY
    assert [e['category'] for e in daily['exercises']] == list(appmod._CATEGORIES)
    assert daily['revision'] == 1 and daily['completed_count'] == 1
    # A page still showing the removed one is told the mission changed.
    r = _complete(client, h, keys[2], D)
    assert r.status_code == 409 and r.get_json()['error'] == 'mission_changed'
    for k in new[1:]:
        assert _complete(client, h, k, D).status_code == 200
    assert DailyMissionAward.query.filter_by(user_id=uid).count() == 1


def test_a_completed_slot_whose_exercise_was_removed_keeps_its_history(client, clock, monkeypatch):
    uid, h = _user(client, 'r_gone_done')
    keys = _keys(_daily(client, h))
    for k in keys[:2]:
        _complete(client, h, k, D)
    _drop(monkeypatch, keys[1])
    daily = _daily(client, h)
    assert _keys(daily) == keys                       # nothing completed moves
    gone = daily['exercises'][1]
    assert gone['completed'] and gone['category'] == appmod._CATEGORIES[1]
    assert daily['completed_count'] == 2
    # Easing still works around it, and the bonus still needs all five.
    assert client.put('/api/daily/effort', json={'level': 'easy'}, headers=h).status_code == 200
    eased = _keys(_daily(client, h))
    assert eased[:2] == keys[:2]
    for k in eased[2:]:
        assert _complete(client, h, k, D).status_code == 200
    assert DailyMissionAward.query.filter_by(user_id=uid).count() == 1
    assert {r.exercise_key for r in DailyCompletion.query.filter_by(user_id=uid)} >= set(keys[:2])


# ── effort_for: a choice for tomorrow beats that same day's own effort ──────

def test_a_harder_choice_made_after_todays_effort_wins_tomorrow(client, clock):
    uid, h = _user(client, 'r_tie', 'intermediate')
    keys = _keys(_daily(client, h))
    _complete(client, h, keys[0], D)                   # started: harder is for tomorrow
    assert client.put('/api/daily/effort', json={'level': 'usual'}, headers=h).status_code == 200
    assert DailyEffort.query.filter_by(user_id=uid, date=D).one().level == 'usual'
    r = client.put('/api/daily/effort', json={'level': 'more'}, headers=h).get_json()
    assert r['applies'] == 'tomorrow'
    clock.at += ONE
    assert _daily(client, h)['effort']['level'] == 'more'


# ── the account-deletion race ───────────────────────────────────────────────

def test_a_mission_insert_that_fails_with_no_winner_is_404_not_500(client, clock, monkeypatch):
    """The insert lost to something other than a concurrent creator (on
    PostgreSQL: the account was deleted mid-request, a foreign-key failure).
    There is no winner to read back; the person is gone."""
    uid, h = _user(client, 'r_gone_user')

    def _fail(*a, **k):
        raise IntegrityError('insert', {}, Exception('fk violation'))
    monkeypatch.setattr(appmod, '_insert_mission', _fail)
    r = client.get('/api/daily', headers=h)
    assert r.status_code == 404


# ── export ───────────────────────────────────────────────────────────────────

def test_the_personal_export_includes_missions_and_effort_choices(client, clock):
    uid, h = _user(client, 'r_export', 'intermediate')
    keys = _keys(_daily(client, h))
    _complete(client, h, keys[0], D)
    client.put('/api/daily/effort', json={'level': 'easy'}, headers=h)
    client.put('/api/daily/effort', json={'level': 'more'}, headers=h)
    body = client.get('/api/me/data', headers=h).get_json()
    m = body['daily_missions']
    assert len(m) == 1 and m[0]['date'] == D.isoformat()
    assert m[0]['effort'] == 'easy' and m[0]['next_effort'] == 'more' and m[0]['revision'] == 1
    assert [s['exercise'] for s in m[0]['exercises']][0] == keys[0]
    assert body['effort_choices'] == [{'date': D.isoformat(), 'level': 'easy'}]


# ── tier readiness ──────────────────────────────────────────────────────────

def test_choosing_more_on_five_days_offers_the_next_level_even_when_each_is_deferred(client, clock):
    uid, h = _user(client, 'r_ready', 'beginner')
    for i in range(5):
        keys = _keys(_daily(client, h))
        _complete(client, h, keys[0], clock.at.date())     # started: "more" is for tomorrow
        assert client.put('/api/daily/effort', json={'level': 'more'}, headers=h).status_code == 200
        if i < 4:
            assert _daily(client, h)['tier_readiness'] is None
        clock.at += ONE
    assert _daily(client, h)['tier_readiness']['next_level'] == 'intermediate'


# ── historical Brain Boost answers ──────────────────────────────────────────

def test_an_answer_from_before_stored_questions_is_never_regraded(client, clock):
    uid, h = _user(client, 'r_bb_legacy')
    past = D - 3 * ONE
    db.session.add(BrainBoostAnswer(user_id=uid, date=past, correct=True, points_earned=10,
                                    answered_at=datetime.datetime.combine(past, datetime.time(9))))
    db.session.commit()
    r = client.post('/api/brain-boost/answer', headers=h,
                    json={'selected_index': 0, 'mission_date': past.isoformat()})
    assert r.status_code == 200
    body = r.get_json()
    assert body['correct'] is True and body['already_recorded'] is True
    # The question asked that day was not stored; today's library is not it.
    assert body['correct_index'] is None and body['explanation'] is None
    assert body['question_available'] is False


# ── out-of-date clients ─────────────────────────────────────────────────────

def test_a_client_that_does_not_name_the_day_is_told_to_refresh(client, clock):
    uid, h = _user(client, 'r_old_tab')
    key = _keys(_daily(client, h))[0]
    for path, body in ((f'/api/daily/{key}/complete', None),
                       ('/api/brain-boost/answer', {'selected_index': 0})):
        r = client.post(path, headers=h, json=body)
        assert r.status_code == 400
        assert r.get_json()['refresh_required'] is True
    assert DailyCompletion.query.filter_by(user_id=uid).count() == 0


# ── approved policy: free changes before the first exercise ─────────────────

def test_skill_change_before_the_first_exercise_rebuilds_today(client, clock):
    uid, h = _user(client, 'r_skill0')
    first = _daily(client, h)
    boost = first['brain_boost']['question']
    r = client.patch('/api/me', json={'skill_level': 'advanced'}, headers=h)
    assert r.status_code == 200 and r.get_json()['skill_applies'] == 'today'
    daily = _daily(client, h)
    assert daily['mission_skill_level'] == 'advanced' and daily['skill_note'] is None
    assert daily['revision'] == 1 and daily['brain_boost']['question'] == boost
    expected = [ex['key'] for ex in appmod.get_daily_exercises(
        uid, D.isoformat(), 'advanced', recent=appmod.recent_movement(uid, D),
        effort=daily['effort']['level'], days_away=0)]
    assert _keys(daily) == expected


def test_skill_change_after_the_first_exercise_waits_for_tomorrow(client, clock):
    uid, h = _user(client, 'r_skill1')
    keys = _keys(_daily(client, h))
    _complete(client, h, keys[0], D)
    r = client.patch('/api/me', json={'skill_level': 'advanced'}, headers=h)
    assert r.get_json()['skill_applies'] == 'tomorrow'
    assert _keys(_daily(client, h)) == keys


@pytest.mark.parametrize('level', ['easy', 'more'])
def test_effort_change_before_the_first_exercise_rebuilds_today(client, clock, level):
    uid, h = _user(client, f'r_effort0_{level}', 'intermediate')
    first = _daily(client, h)
    r = client.put('/api/daily/effort', json={'level': level}, headers=h).get_json()
    assert r['applies'] == 'today' and r['revision'] == 1 and r['next_effort'] is None
    daily = _daily(client, h)
    assert daily['effort']['level'] == level
    assert daily['brain_boost']['question'] == first['brain_boost']['question']
    assert DailyEffort.query.filter_by(user_id=uid, date=D).one().level == level


def test_a_repair_redraws_only_the_unfinished_slot_when_both_kinds_are_missing(client, clock, monkeypatch):
    """One completed and one unfinished exercise removed by the same deploy:
    the repair runs (for the unfinished one) and must leave the completed one
    exactly where it was."""
    uid, h = _user(client, 'r_gone_both')
    keys = _keys(_daily(client, h))
    for k in keys[:2]:
        _complete(client, h, k, D)
    lib = _drop(monkeypatch, keys[1], keys[3])         # one completed, one not yet done
    daily = _daily(client, h)
    new = _keys(daily)
    assert new[:3] == keys[:3] and new[4] == keys[4]   # completed and present ones stay
    assert new[3] != keys[3] and new[3] in lib         # the missing unfinished one is redrawn
    assert daily['exercises'][1]['retired'] and daily['exercises'][1]['completed']
    assert daily['revision'] == 1 and daily['completed_count'] == 2


def test_a_legacy_day_paid_on_five_rows_shows_its_slots_and_is_never_paid_again(client, clock):
    """Deploy day only: the old code paid the bonus on any five rows, so a day
    with two exercises in one category (an old mid-day tier switch) is paid
    and counted in the streak, but fills four of the five stored slots. Shown
    honestly as 4/5; finishing the fifth slot pays nothing twice. (No
    compatibility fix: showing 5/5 would misstate the stored five.)"""
    uid, h = _user(client, 'r_legacy_paid')
    upper = [ex['key'] for ex in appmod.EXERCISE_LIBRARY['advanced']['upper_body']][:2]
    rest = [appmod.EXERCISE_LIBRARY['advanced'][c][0]['key'] for c in ('lower_body', 'core', 'mobility')]
    for i, k in enumerate(upper + rest):
        db.session.add(DailyCompletion(user_id=uid, date=D, exercise_key=k,
                                       completed_at=datetime.datetime.combine(D, datetime.time(8, i))))
    db.session.add(DailyMissionAward(user_id=uid, mission_date=D))
    db.session.commit()
    xp = db.session.get(User, uid).xp_total
    assert appmod.get_user_stats(uid)['current_streak'] == 1          # five rows: a mission day

    daily = _daily(client, h)
    assert daily['completed_count'] == 4
    last = next(e['key'] for e in daily['exercises'] if not e['completed'])
    body = _complete(client, h, last, D).get_json()
    assert body['completed_count'] == 5
    assert not any(e['event_type'] == 'mission_complete' for e in body['progress_events'])
    assert DailyMissionAward.query.filter_by(user_id=uid).count() == 1
    assert db.session.get(User, uid).xp_total - xp == body['xp_awarded']


def test_a_skill_change_before_the_days_first_request_stores_the_new_tier(client, clock):
    """No mission stored yet when the skill changes: the PATCH stores today's
    mission itself (under the user lock), so "applies today" is what the day
    then holds. The two-request race on PostgreSQL is
    tests/concurrency_race.py S3a/S3b."""
    uid, h = _user(client, 'r_skill_first')
    assert DailyCompletion.query.filter_by(user_id=uid).count() == 0
    r = client.patch('/api/me', json={'skill_level': 'advanced'}, headers=h)
    assert r.status_code == 200 and r.get_json()['skill_applies'] == 'today'
    daily = _daily(client, h)
    assert daily['mission_skill_level'] == 'advanced' and daily['skill_note'] is None
