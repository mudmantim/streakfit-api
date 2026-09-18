"""The reward economy: what a completion actually pays, on day 1 and after.

The roadmap named this the highest-value missing test file — the award and
threshold logic was only ever exercised indirectly, by the campfire tests and
the end-to-end suite. It is also the logic that produced the day-2 silence bug,
so the day-2 shape is pinned here deliberately rather than left implicit.
"""
import datetime

from app import DailyCompletion, User, db
from conftest import auth_headers, register_and_login


def _daily_keys(client, token):
    daily = client.get('/api/daily', headers=auth_headers(token)).get_json()
    return [ex['key'] for ex in daily['exercises']]


def _complete(client, token, key):
    return client.post(f'/api/daily/{key}/complete', headers=auth_headers(token)).get_json()


def _seed_prior_day(username, keys, days_ago=1):
    """Give this user a history in which they have already done these exercises.

    This is what makes a user a *returning* one: `new_exercise` pays once ever,
    so yesterday's completions are precisely what stops today's from paying.
    """
    user = db.session.execute(db.select(User).where(User.username == username)).scalar_one()
    when = datetime.date.today() - datetime.timedelta(days=days_ago)
    for key in keys:
        db.session.add(DailyCompletion(user_id=user.id, date=when, exercise_key=key))
    db.session.commit()
    return user


# ── Day 1: every completion is a first-ever completion ──────────────────────

def test_first_ever_completion_pays_the_new_exercise_bonus(client):
    token = register_and_login(client, 'day1_newbie')
    keys = _daily_keys(client, token)

    first = _complete(client, token, keys[0])

    assert first['xp_awarded'] == 20
    assert first['acorns_awarded'] == 5
    assert first['completed_count'] == 1


def test_fifth_completion_adds_the_mission_bonuses(client):
    token = register_and_login(client, 'day1_finisher')
    keys = _daily_keys(client, token)
    for key in keys[:4]:
        _complete(client, token, key)

    fifth = _complete(client, token, keys[4])

    # 20 new-exercise + 25 mission_complete + 15 perfect_mission
    assert fifth['xp_awarded'] == 60
    assert fifth['acorns_awarded'] == 10
    assert fifth['completed_count'] == 5
    assert fifth['leveled_up'] is True
    assert fifth['new_level'] == 2
    assert fifth['level_title'] == 'Adventurer'


def test_repeating_the_same_key_today_is_idempotent(client):
    token = register_and_login(client, 'double_tapper')
    keys = _daily_keys(client, token)
    _complete(client, token, keys[0])

    again = _complete(client, token, keys[0])

    assert again['xp_awarded'] == 0
    assert again['acorns_awarded'] == 0
    assert again['completed_count'] == 1
    assert again['progress_events'] == []


# ── Day 2 onward: the shape that drove the reaction fix ─────────────────────

def test_returning_user_is_paid_for_every_tap(client):
    """Coming back and moving again is the behaviour the product exists to
    reinforce, so a repeat completion pays. See docs/reward-economy.md."""
    token = register_and_login(client, 'day2_returner')
    keys = _daily_keys(client, token)
    _seed_prior_day('day2_returner', keys)

    awards = [_complete(client, token, key) for key in keys]

    assert [a['xp_awarded'] for a in awards[:4]] == [5, 5, 5, 5]
    # No acorns for repeats: acorns have no sink yet and stay tied to notable
    # events, so the most frequent action in the app must not inflate them.
    assert [a['acorns_awarded'] for a in awards[:4]] == [0, 0, 0, 0]
    # 5 repeat + 25 mission_complete + 15 perfect_mission
    assert awards[4]['xp_awarded'] == 45
    assert awards[4]['acorns_awarded'] == 5


def test_finishing_the_mission_outweighs_the_taps_that_led_to_it(client):
    """The load-bearing constraint on the repeat value.

    Five repeat taps must be worth less than the bonus for completing the
    mission, or the product stops being about finishing. This is what pins the
    repeat reward below 8 XP — raise REPEAT_EXERCISE_XP past it and this fails.
    """
    import app as appmod

    five_taps = 5 * appmod.REPEAT_EXERCISE_XP
    finishing = appmod.MISSION_COMPLETE_XP + appmod.PERFECT_MISSION_XP
    assert five_taps < finishing, (
        f'{five_taps} XP of taps >= {finishing} XP for finishing — '
        'completing the mission is no longer the biggest beat of the day'
    )


def test_discovery_still_clearly_beats_a_repeat(client):
    """A new exercise should feel like an event, not a rounding difference."""
    import app as appmod

    assert appmod.NEW_EXERCISE_BONUS_XP >= 4 * appmod.REPEAT_EXERCISE_XP


def test_returning_user_still_builds_a_streak(client):
    token = register_and_login(client, 'day2_streaker')
    keys = _daily_keys(client, token)
    _seed_prior_day('day2_streaker', keys)

    for key in keys:
        _complete(client, token, key)

    me = client.get('/api/me', headers=auth_headers(token)).get_json()
    assert me['current_streak'] == 2
    assert me['best_streak'] == 2
    assert me['total_missions'] == 2


def test_a_genuinely_new_exercise_still_pays_for_a_returning_user(client):
    """Only the keys they have actually done before stop paying."""
    token = register_and_login(client, 'day2_explorer')
    keys = _daily_keys(client, token)
    _seed_prior_day('day2_explorer', keys[:4])  # 5th is still new to them

    awards = [_complete(client, token, key) for key in keys]

    assert [a['xp_awarded'] for a in awards[:4]] == [5, 5, 5, 5]
    assert awards[4]['xp_awarded'] == 60  # 20 new-exercise + 40 mission bonuses


# ── Level curve ────────────────────────────────────────────────────────────

def test_level_thresholds_follow_the_documented_curve(client):
    """25*(n-1)^2 + 75*(n-1): L1=0, L2=100, L3=250, L4=450, L5=700."""
    import app as appmod

    assert [appmod._level_threshold(n) for n in range(1, 6)] == [0, 100, 250, 450, 700]


def test_level_titles_top_out_at_legend(client):
    import app as appmod

    assert appmod.xp_to_level(0)['level_title'] == 'Explorer'
    assert appmod.xp_to_level(100)['level_title'] == 'Adventurer'
    # Anything past the named ceiling keeps the last title rather than going blank.
    top = appmod.xp_to_level(1_000_000)
    assert top['level_title'] == 'Legend'
    assert top['level'] > 8  # the number keeps climbing even though the title stops


# ── Mission generation: every exercise must be reachable ────────────────────

def test_every_exercise_in_every_level_can_actually_be_selected(client):
    """No drawn exercise may be unreachable.

    This caught a real defect: beginner's only high-fun exercises all live in
    `conditioning`, so an unconditional fun floor made the retry loop redraw
    until that category landed on one of the three high-fun options —
    permanently excluding the other three. Every beginner saw 27 of 30
    exercises for the life of their account, and three illustrations were dead.
    """
    import datetime as dt

    import app as appmod

    start = dt.date(2026, 1, 1)
    for skill, categories in appmod.EXERCISE_LIBRARY.items():
        expected = {ex['key'] for exs in categories.values() for ex in exs}
        seen = set()
        for user_id in range(9000, 9004):
            for offset in range(160):
                day = (start + dt.timedelta(days=offset)).isoformat()
                seen.update(e['key'] for e in appmod.get_daily_exercises(user_id, day, skill))
        missing = expected - seen
        assert not missing, f'{skill}: unreachable exercises {sorted(missing)}'


def test_most_days_still_include_something_high_energy(client):
    """The fun floor may be waived to keep content reachable, but not abandoned."""
    import datetime as dt

    import app as appmod

    start = dt.date(2026, 1, 1)
    days = high_fun = 0
    for user_id in range(9100, 9110):
        for offset in range(60):
            day = (start + dt.timedelta(days=offset)).isoformat()
            picks = appmod.get_daily_exercises(user_id, day, 'beginner')
            days += 1
            if any(e['fun_score'] == 'high' for e in picks):
                high_fun += 1
    assert high_fun / days >= 0.75, f'only {high_fun / days:.0%} of days have a high-fun exercise'


def test_a_mission_is_always_five_one_per_category(client):
    import datetime as dt

    import app as appmod

    start = dt.date(2026, 1, 1)
    for skill in appmod.EXERCISE_LIBRARY:
        for offset in range(30):
            day = (start + dt.timedelta(days=offset)).isoformat()
            picks = appmod.get_daily_exercises(7777, day, skill)
            assert len(picks) == 5
            assert [p['category'] for p in picks] == list(appmod._CATEGORIES)
