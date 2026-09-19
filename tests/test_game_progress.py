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


def _seed_prior_day(username, keys, days_ago=3):
    """Give this user a history in which they have already done these exercises.

    This is what makes a user a *returning* one: `new_exercise` pays once ever,
    so an earlier completion is precisely what stops today's from paying.

    Three days back rather than one, by default, because today's mission now
    actively avoids repeating YESTERDAY — seeding the same keys into yesterday
    would change the mission and the keys under test would no longer be in it.
    Tests that specifically need yesterday pass days_ago=1 and re-read the
    mission afterwards.
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
    _seed_prior_day('day2_streaker', _daily_keys(client, token), days_ago=1)
    # Re-read: yesterday's completions change what today's mission offers.
    keys = _daily_keys(client, token)

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


# ── Milestones announce themselves ─────────────────────────────────────────

def test_first_mission_milestone_is_announced_when_it_happens(client):
    token = register_and_login(client, 'milestone_first')
    keys = _daily_keys(client, token)
    for key in keys[:4]:
        payload = _complete(client, token, key)
        assert payload['milestones_unlocked'] == []   # nothing crossed yet

    fifth = _complete(client, token, keys[4])

    unlocked = {m['key'] for m in fifth['milestones_unlocked']}
    assert 'first_mission' in unlocked


def test_a_milestone_is_announced_once_not_every_day_after(client):
    """The crossing is the event, not the state of being past it."""
    token = register_and_login(client, 'milestone_once')
    keys = _daily_keys(client, token)
    for key in keys:
        _complete(client, token, key)

    # A second day: still past first_mission, but it must not re-announce.
    _seed_prior_day('milestone_once', keys, days_ago=2)
    later = client.get('/api/daily', headers=auth_headers(token)).get_json()
    assert later['completed_count'] == 5   # today is already done
    repeat = _complete(client, token, keys[0])
    assert repeat['milestones_unlocked'] == []


def test_an_idempotent_tap_announces_nothing(client):
    token = register_and_login(client, 'milestone_noop')
    keys = _daily_keys(client, token)
    _complete(client, token, keys[0])

    again = _complete(client, token, keys[0])

    assert again['progress_events'] == []
    assert again['milestones_unlocked'] == []


def test_milestones_crossed_only_fires_on_the_crossing_step():
    import app as appmod

    # 99 -> 100 crosses.
    assert [m['key'] for m in appmod._milestones_crossed(
        {'exercises_completed': 100}, {'exercises_completed': 1})] == ['exercises_100']
    # 100 -> 101 does not.
    assert appmod._milestones_crossed(
        {'exercises_completed': 101}, {'exercises_completed': 1}) == []
    # A metric that did not move is never checked, even when already past it.
    assert appmod._milestones_crossed({'exercises_completed': 500}, {}) == []


def test_a_single_jump_past_a_target_still_counts():
    """A big award must not step over a milestone without announcing it."""
    import app as appmod

    crossed = appmod._milestones_crossed({'xp_total': 1040}, {'xp_total': 60})
    assert [m['key'] for m in crossed] == ['xp_1000']


def test_award_progress_reports_which_event_it_was(client):
    token = register_and_login(client, 'event_typed')
    keys = _daily_keys(client, token)
    for key in keys[:4]:
        _complete(client, token, key)

    fifth = _complete(client, token, keys[4])

    types = [e['event_type'] for e in fifth['progress_events']]
    assert 'mission_complete' in types
    assert 'perfect_mission' in types


# ── The mission remembers: progression, recovery, and the tier ramp ─────────
#
# Added after a 40-user simulation over 30/60/90 days found the daily mission
# had no memory at all: reps were fixed strings forever, one slot in five
# repeated yesterday, an advanced user got two explosive days back to back one
# week in five, and changing tier swapped 100% of the pool in a single step.

import app as appmod


def _days(n, start=datetime.date(2026, 1, 1)):
    return [(start + datetime.timedelta(days=i)).isoformat() for i in range(n)]


def test_yesterdays_exercises_are_avoided_when_they_can_be():
    """18-20% of slots used to repeat yesterday, with nothing watching."""
    repeats = slots = 0
    for uid in range(500, 540):
        prev = set()
        for day in _days(30):
            picked = appmod.get_daily_exercises(uid, day, 'intermediate',
                                                recent={'keys': prev, 'high_impact': 0})
            keys = {ex['key'] for ex in picked}
            repeats += len(keys & prev)
            slots += 5
            prev = keys
    share = repeats / slots
    assert share < 0.05, f"{share:.1%} of slots repeat yesterday (was 20%)"


def test_a_heavy_day_is_not_followed_by_another_one():
    """Advanced users got two explosive days in a row 20% of the time."""
    for uid in range(600, 640):
        for day in _days(20):
            picked = appmod.get_daily_exercises(
                uid, day, 'advanced', recent={'keys': set(), 'high_impact': 2})
            high = sum(1 for ex in picked if ex['impact'] == 'high')
            assert high <= 1, f"{high} explosive exercises the day after a heavy one"


def test_an_ordinary_day_still_allows_a_hard_one():
    """The recovery rule must not quietly become a permanent cap — that would
    make every day easier, which is not what was wrong."""
    seen_two = any(
        sum(1 for ex in appmod.get_daily_exercises(
            uid, day, 'advanced', recent={'keys': set(), 'high_impact': 0})
            if ex['impact'] == 'high') == 2
        for uid in range(700, 720) for day in _days(10)
    )
    assert seen_two, "no day ever reaches two high-impact exercises any more"


def test_a_rest_day_is_not_something_to_recover_from():
    """Recovery reads what was actually COMPLETED. Someone who did nothing
    yesterday is not carrying fatigue, and must not be given an easier day for
    having missed one — that would be the app quietly rewarding the miss."""
    with_rest = appmod.get_daily_exercises(11, '2026-03-01', 'advanced',
                                           recent={'keys': set(), 'high_impact': 0})
    no_history = appmod.get_daily_exercises(11, '2026-03-01', 'advanced')
    assert [e['key'] for e in with_rest] == [e['key'] for e in no_history]


def test_the_step_up_is_earned_and_stops_growing():
    base = '3 sets of 12 reps'
    assert appmod.step_up_for(base, 0) is None       # not earned yet
    assert appmod.step_up_for(base, 3) is None
    assert appmod.step_up_for(base, 4) == '3 sets of 14 reps'
    assert appmod.step_up_for(base, 10) == '3 sets of 16 reps'
    assert appmod.step_up_for(base, 25) == '3 sets of 18 reps'
    assert appmod.step_up_for(base, 5000) == '3 sets of 18 reps'   # capped at +50%


def test_the_step_up_declines_wordings_it_cannot_safely_change():
    """Silence is the correct answer for anything ambiguous. '30 seconds on /
    30 seconds off' has a second number whose relationship to the first we'd
    only be guessing at."""
    for odd in ['2 rounds of 30 seconds on / 30 seconds off',
                '3 sets of 8 breath cycles', 'As many as feel good', '']:
        assert appmod.step_up_for(odd, 100) is None, odd


def test_the_prescription_itself_never_changes_underneath_anyone():
    """The whole design decision: a number that goes up on its own turns a
    daily habit into a target, and the first day you can't hit it becomes a
    failure. The larger version is offered beside the prescription."""
    early = appmod.get_daily_exercises(9, '2026-02-01', 'beginner', missions_completed=0)
    later = appmod.get_daily_exercises(9, '2026-02-01', 'beginner', missions_completed=400)
    base = {e['key']: e['reps_or_duration'] for e in early}
    for ex in later:
        if ex['key'] in base:
            assert ex['reps_or_duration'] == base[ex['key']]


def test_the_tier_ramp_starts_at_nothing_and_rises_gradually():
    def share(missions):
        total = sum(1 for uid in range(800, 900)
                    for ex in appmod.get_daily_exercises(
                        uid, '2026-04-01', 'beginner', missions_completed=missions)
                    if ex.get('from_next_tier'))
        return total / (100 * 5)

    assert share(0) == 0.0
    assert share(appmod._RAMP_START - 1) == 0.0
    mid, full = share(30), share(appmod._RAMP_FULL)
    assert 0 < mid < full, f"not gradual: {mid:.1%} then {full:.1%}"
    assert full < 0.25, f"{full:.0%} of a beginner's day comes from the level above"


def test_a_borrowed_exercise_says_so():
    """It is a shift in emphasis the person can see, not content that appears
    unannounced and harder than they signed up for."""
    borrowed = [ex for uid in range(900, 940)
                for ex in appmod.get_daily_exercises(uid, '2026-04-01', 'beginner',
                                                     missions_completed=90)
                if ex.get('from_next_tier')]
    assert borrowed
    assert all(ex['difficulty'] == 'intermediate' for ex in borrowed)


def test_the_top_tier_borrows_from_nothing():
    for ex in appmod.get_daily_exercises(1, '2026-04-01', 'advanced', missions_completed=500):
        assert not ex.get('from_next_tier')


def test_readiness_is_an_offer_and_only_after_real_practice():
    assert appmod.tier_readiness('beginner', 10) is None
    assert appmod.tier_readiness('advanced', 500) is None       # nowhere to go
    ready = appmod.tier_readiness('beginner', appmod._RAMP_FULL)
    assert ready['next_level'] == 'intermediate'
    low = ready['message'].lower()
    for pushy in ('should', 'need to', 'time to move on', 'ready to graduate', 'too easy'):
        assert pushy not in low, f"readiness message pressures the user: {ready['message']!r}"


def test_tier_hopping_no_longer_farms_the_discovery_bonus(client):
    """The exploit: the three tiers share no exercise keys, so switching tier
    mid-day used to re-open five fresh first-evers. beginner -> intermediate ->
    advanced paid 340 XP on day one instead of 140, reaching level 3."""
    token = register_and_login(client, 'tier_hopper')
    total = 0
    for tier in ('beginner', 'intermediate', 'advanced'):
        client.patch('/api/me', json={'skill_level': tier}, headers=auth_headers(token))
        for key in _daily_keys(client, token):
            total += _complete(client, token, key).get('xp_awarded', 0)

    assert total < 200, f"{total} XP from tier-hopping on day one"
    me = client.get('/api/me', headers=auth_headers(token)).get_json()
    assert me['total_missions'] == 1        # these were always honest
    assert me['current_streak'] == 1


def test_extra_movement_beyond_the_mission_still_counts_and_still_pays(client):
    """Closing the exploit must not turn into punishing someone for moving
    more. The sixth exercise of a day pays the repeat rate — which is what a
    sixth exercise is — rather than nothing."""
    token = register_and_login(client, 'tier_extra')
    for key in _daily_keys(client, token):
        _complete(client, token, key)
    client.patch('/api/me', json={'skill_level': 'intermediate'},
                 headers=auth_headers(token))
    sixth = _complete(client, token, _daily_keys(client, token)[0])
    assert sixth['xp_awarded'] == appmod.REPEAT_EXERCISE_XP
