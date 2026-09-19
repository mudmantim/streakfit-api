"""The reward economy: what a completion actually pays, on day 1 and after.

The roadmap named this the highest-value missing test file — the award and
threshold logic was only ever exercised indirectly, by the campfire tests and
the end-to-end suite. It is also the logic that produced the day-2 silence bug,
so the day-2 shape is pinned here deliberately rather than left implicit.
"""
import datetime

import pytest

import app as appmod
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


def test_a_quiet_yesterday_is_not_treated_as_fatigue():
    """Recovery reads what was COMPLETED, so it caps a heavy day, not a quiet
    one. This is about yesterday specifically. Being away for a WHILE is a
    different thing and is handled separately — see the returning-user tests,
    where an absence eases the day ON PURPOSE.

    This test used to be called "a rest day is not something to recover from"
    and its comment said easing off after a missed day would be "the app
    quietly rewarding the miss". That reasoning was wrong and does not belong
    in this product: an absence is not a failure, and an easier return is not
    an undeserved prize. All it should ever have claimed is that one ordinary
    quiet day does not mean somebody is carrying fatigue.
    """
    after_quiet = appmod.get_daily_exercises(11, '2026-03-01', 'advanced',
                                             recent={'keys': set(), 'high_impact': 0})
    no_history = appmod.get_daily_exercises(11, '2026-03-01', 'advanced')
    assert [e['key'] for e in after_quiet] == [e['key'] for e in no_history]



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
    for effort in appmod.EFFORT_LEVELS:
        for ex in appmod.get_daily_exercises(9, '2026-02-01', 'beginner', effort=effort):
            source = appmod._EXERCISE_BY_KEY[ex['key']]
            assert ex['reps_or_duration'] == source['reps_or_duration']


# ── Consistency is not readiness ────────────────────────────────────────────
#
# Difficulty used to escalate automatically once someone had finished fourteen
# missions. Mission count measures how consistent a person is and says nothing
# about what their body is ready for — somebody can finish forty-five beginner
# missions precisely because beginner is the right level for them. These pin
# the four things that used to be conflated into one: recovery from yesterday,
# easing a return, capability demonstrated per movement, and willingness asked
# for out loud.

def test_nothing_about_a_history_can_make_the_day_harder_on_its_own():
    """The signature can no longer express the old behaviour: there is no
    input carrying a mission count, so there is nothing to escalate from."""
    import inspect as _inspect
    params = _inspect.signature(appmod.get_daily_exercises).parameters
    assert 'missions_completed' not in params
    for uid in range(1200, 1240):
        picked = appmod.get_daily_exercises(uid, '2026-05-01', 'beginner')
        assert not any(ex.get('from_next_tier') for ex in picked)


def test_a_harder_movement_appears_only_when_someone_asks_for_one():
    def share(effort):
        total = sum(1 for uid in range(800, 900)
                    for ex in appmod.get_daily_exercises(uid, '2026-04-01', 'beginner',
                                                         effort=effort)
                    if ex.get('from_next_tier'))
        return total / (100 * 5)

    assert share('usual') == 0.0
    assert share('easy') == 0.0
    assert 0 < share('more') < 0.30, "asking for a little more should stay little"


def test_an_easy_day_is_genuinely_easier_for_an_advanced_user():
    """Not merely 'the same exercises with fewer jumps'. Advanced conditioning
    is five explosive movements out of six, so an easy day has to be able to
    reach the level below or it cannot honour the request."""
    borrowed = 0
    for uid in range(900, 960):
        picked = appmod.get_daily_exercises(uid, '2026-04-01', 'advanced', effort='easy')
        assert all(ex['impact'] != 'high' for ex in picked), \
            "an easy day still handed out explosive movements"
        borrowed += sum(1 for ex in picked if ex.get('from_easier_tier'))
    assert borrowed > 0, "nothing was ever drawn from the gentler level"


def test_an_easy_day_is_still_allowed_to_be_fun():
    """Gentle and dull are different axes. Someone who asks for an easier day
    should not be handed the five most boring things in the library."""
    fun = sum(
        1 for uid in range(960, 1020)
        if any(ex['fun_score'] == 'high'
               for ex in appmod.get_daily_exercises(uid, '2026-04-01', 'intermediate',
                                                    effort='easy'))
    )
    assert fun > 25, f"only {fun}/60 easy days had anything energetic in them"


def test_a_borrowed_exercise_says_so_in_both_directions():
    """Neither a harder movement nor a gentler one arrives unexplained."""
    harder = [ex for uid in range(900, 960)
              for ex in appmod.get_daily_exercises(uid, '2026-04-01', 'beginner', effort='more')
              if ex.get('from_next_tier')]
    assert harder and all(ex['difficulty'] == 'intermediate' for ex in harder)
    gentler = [ex for uid in range(900, 960)
               for ex in appmod.get_daily_exercises(uid, '2026-04-01', 'advanced', effort='easy')
               if ex.get('from_easier_tier')]
    assert gentler and all(ex['difficulty'] == 'intermediate' for ex in gentler)


def test_the_top_and_bottom_tiers_have_nowhere_to_borrow_from():
    for ex in appmod.get_daily_exercises(1, '2026-04-01', 'advanced', effort='more'):
        assert not ex.get('from_next_tier')
    for ex in appmod.get_daily_exercises(1, '2026-04-01', 'beginner', effort='easy'):
        assert not ex.get('from_easier_tier')


def test_every_effort_level_is_worth_exactly_the_same():
    """Load-bearing. The moment a harder day pays more, the question stops
    being about what someone's body wants today and becomes something they are
    losing by not picking."""
    import inspect as _inspect
    for fn in (appmod.complete_daily_exercise, appmod.award_progress):
        src = _inspect.getsource(fn)
        assert 'effort' not in src.lower(), (
            f"{fn.__name__} reads the effort level — rewards must not depend on "
            "how hard someone asked today to be"
        )


def test_readiness_is_gated_on_asking_for_more_not_on_showing_up(app):
    u = User(username='readiness_user', password_hash='x')
    db.session.add(u)
    db.session.commit()
    assert appmod.tier_readiness(u.id, 'beginner') is None
    assert appmod.tier_readiness(u.id, 'advanced') is None       # nowhere to go

    base = datetime.date(2026, 1, 1)
    for d in range(40):
        for key in ('wall_push_up', 'bodyweight_squat', 'dead_bug',
                    'ankle_circles', 'marching_in_place'):
            db.session.add(DailyCompletion(user_id=u.id,
                                           date=base + datetime.timedelta(days=d),
                                           exercise_key=key))
    db.session.commit()
    assert appmod.tier_readiness(u.id, 'beginner') is None, \
        "forty finished missions is consistency, and must not be read as readiness"

    for d in range(appmod._READINESS_MORE_DAYS):
        db.session.add(appmod.DailyEffort(user_id=u.id,
                                          date=base + datetime.timedelta(days=d),
                                          level='more'))
    db.session.commit()
    ready = appmod.tier_readiness(u.id, 'beginner')
    assert ready and ready['next_level'] == 'intermediate'
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


# ── Coming back after a while ───────────────────────────────────────────────
#
# Before this, the selection function was never told how long someone had been
# away, so the first day back was identical to the day they stopped. Someone
# who had been training at advanced and had not moved for sixty days was handed
# 1.35 explosive exercises — their exact peak load — on the morning they
# returned. That is the defect. It is not that an easier day would have
# "rewarded the miss": an absence is not a failure, and nothing here takes
# anything away from anyone.

def _returning_user(username, tier, missions, days_away, keys):
    u = User(username=username, password_hash='x', skill_level=tier)
    db.session.add(u)
    db.session.commit()
    last = datetime.date.today() - datetime.timedelta(days=days_away + 1)
    for d in range(missions):
        day = last - datetime.timedelta(days=d)
        for key in keys:
            db.session.add(DailyCompletion(user_id=u.id, date=day, exercise_key=key))
    u.xp_total, u.acorns_total = 2400, 150
    db.session.commit()
    return u


_BEGINNER_FIVE = ('wall_push_up', 'bodyweight_squat', 'dead_bug',
                  'ankle_circles', 'marching_in_place')
_ADVANCED_FIVE = ('archer_push_up', 'pistol_squat_progression', 'hollow_body_hold',
                  'deep_squat_hold', 'burpee')


@pytest.mark.parametrize("days_away", [7, 30, 60])
@pytest.mark.parametrize("tier,keys", [("beginner", _BEGINNER_FIVE),
                                       ("advanced", _ADVANCED_FIVE)])
def test_coming_back_is_eased_not_resumed_at_full_load(app, tier, keys, days_away):
    u = _returning_user(f"ret_{tier}_{days_away}", tier, 40, days_away, keys)
    mission = appmod.todays_mission(u.id, tier, datetime.date.today())

    assert mission['days_away'] == days_away
    assert mission['effort'] == 'easy', "a long absence should pre-select a gentler day"
    assert mission['effort_chosen'] is False, "suggested, not chosen for them"
    assert all(ex['impact'] != 'high' for ex in mission['exercises']), \
        "handed explosive movements on the first day back"
    assert mission['returning_note'], "eased the day without saying why"


@pytest.mark.parametrize("days_away", [7, 30, 60])
def test_nothing_earned_is_lost_by_being_away(app, days_away):
    """An absence must not cost anyone their record. What it changes is what
    today asks of their body, which is a different fact about them."""
    u = _returning_user(f"keep_{days_away}", 'advanced', 40, days_away, _ADVANCED_FIVE)
    stats = appmod.get_user_stats(u.id)
    assert u.xp_total == 2400
    assert u.acorns_total == 150
    assert stats['total_missions'] == 40
    assert stats['best_streak'] == 40
    # Milestones are all cumulative metrics, so none of them un-earn either.
    for m in appmod._MILESTONE_DEFINITIONS:
        assert m['metric'] in ('missions_completed', 'exercises_completed',
                               'brain_boosts_answered', 'xp_total', 'acorns_total',
                               'level'), \
            f"{m['key']} is measured by {m['metric']}, which may reset on an absence"


def test_the_returning_note_never_mentions_the_absence(app):
    note = appmod.returning_note(45)
    assert note
    low = note.lower()
    for scolding in ('45', 'been a while', 'where have you', 'missed', 'haven\'t',
                     'welcome back', 'long time', 'lost'):
        assert scolding not in low, f"the returning line says {scolding!r}: {note!r}"


def test_a_short_gap_is_just_a_gap(app):
    """Two or three days off is ordinary life, not a return."""
    u = _returning_user("shortgap", 'advanced', 20, 3, _ADVANCED_FIVE)
    mission = appmod.todays_mission(u.id, 'advanced', datetime.date.today())
    assert mission['effort'] == appmod.EFFORT_DEFAULT
    assert mission['returning_note'] is None


def test_the_gentler_window_ends_once_they_are_going_again(app):
    """Easing the return is care on the day, not a lasting judgement about
    what someone can do."""
    u = _returning_user("settled", 'advanced', 20, 30, _ADVANCED_FIVE)
    # Three missions since coming back.
    for d in range(appmod.RETURN_WINDOW_MISSIONS):
        day = datetime.date.today() - datetime.timedelta(days=d)
        for key in _ADVANCED_FIVE:
            db.session.add(DailyCompletion(user_id=u.id, date=day, exercise_key=key))
    db.session.commit()
    assert appmod.suggested_effort(30, appmod.RETURN_WINDOW_MISSIONS) == appmod.EFFORT_DEFAULT


def test_a_brand_new_person_is_not_returning_from_anywhere(app):
    u = User(username='first_day_ever', password_hash='x')
    db.session.add(u)
    db.session.commit()
    mission = appmod.todays_mission(u.id, 'beginner', datetime.date.today())
    assert mission['days_away'] is None
    assert mission['effort'] == appmod.EFFORT_DEFAULT
    assert mission['returning_note'] is None


# ── The choice itself ───────────────────────────────────────────────────────

def test_easing_off_is_always_allowed_mid_mission(client):
    token = register_and_login(client, 'ease_off')
    keys = _daily_keys(client, token)
    _complete(client, token, keys[0])
    resp = client.put('/api/daily/effort', json={'level': 'easy'},
                      headers=auth_headers(token))
    assert resp.status_code == 200
    assert client.get('/api/daily', headers=auth_headers(token)).get_json()['effort']['level'] == 'easy'


def test_asking_for_more_is_refused_once_the_mission_has_started(client):
    """Not to punish anyone — the five exercises would change underneath
    completions that already exist, and "I've done three, let me swap to the
    harder set" is the one version of this that is about the score."""
    token = register_and_login(client, 'ask_more_late')
    keys = _daily_keys(client, token)
    _complete(client, token, keys[0])
    resp = client.put('/api/daily/effort', json={'level': 'more'},
                      headers=auth_headers(token))
    assert resp.status_code == 409
    body = resp.get_json()
    assert 'tomorrow' in body['message'].lower()
    for blaming in ('cannot', 'not allowed', 'too late', 'failed'):
        assert blaming not in body['message'].lower(), body['message']


def test_the_choice_is_free_before_anything_is_completed(client):
    token = register_and_login(client, 'free_choice')
    for level in ('more', 'easy', 'usual', 'more'):
        assert client.put('/api/daily/effort', json={'level': level},
                          headers=auth_headers(token)).status_code == 200


def test_an_unknown_effort_level_is_rejected(client):
    token = register_and_login(client, 'bad_effort')
    resp = client.put('/api/daily/effort', json={'level': 'brutal'},
                      headers=auth_headers(token))
    assert resp.status_code == 400
    assert set(resp.get_json()['allowed']) == set(appmod.EFFORT_LEVELS)


def test_the_mission_shown_is_the_mission_accepted(client):
    """Whatever effort is in play, /api/daily and the completion route must
    agree about which five exercises exist — they used to build the mission
    separately, which is the shape of bug where the exercise on screen is
    rejected as 'not in today's list'."""
    token = register_and_login(client, 'agreement')
    for level in ('easy', 'usual', 'more'):
        client.put('/api/daily/effort', json={'level': level}, headers=auth_headers(token))
        for key in _daily_keys(client, token):
            resp = client.post(f'/api/daily/{key}/complete', headers=auth_headers(token))
            assert resp.status_code == 200, (level, key, resp.get_json())
        client.put('/api/daily/effort', json={'level': 'easy'}, headers=auth_headers(token))


def test_overruling_the_suggestion_still_keeps_a_floor_under_the_first_day_back(app):
    """Someone who has been away is free to say "my usual" — and is given it.
    What survives the override is one modest cap: at most one explosive
    movement on the day they come back, rather than the two a normal day
    allows.

    This is the only reason the selection function is told about the absence at
    all. When the suggestion is accepted, `effort='easy'` already caps
    explosive work at zero and the absence changes nothing — so removing the
    days_away argument passed every test until this one existed, which is a
    fair warning about how easy it is to write a redundant parameter and
    believe it is doing something.
    """
    away, back = 45, 0
    for effort in ('usual', 'more'):
        for uid in range(1300, 1340):
            fresh = appmod.get_daily_exercises(uid, '2026-06-01', 'advanced',
                                               effort=effort, days_away=back)
            returning = appmod.get_daily_exercises(uid, '2026-06-01', 'advanced',
                                                   effort=effort, days_away=away)
            assert sum(1 for e in returning if e['impact'] == 'high') <= 1, \
                f"{effort}: a first day back reached the full explosive load"
            del fresh  # only here to show the comparison was available

    # And the cap lifts again once they are going: nothing is permanent.
    reached_two = any(
        sum(1 for e in appmod.get_daily_exercises(uid, '2026-06-01', 'advanced',
                                                  effort='usual', days_away=0)
            if e['impact'] == 'high') == 2
        for uid in range(1300, 1360)
    )
    assert reached_two, "the ordinary advanced day never reaches two any more"


def test_a_returning_person_still_gets_what_they_asked_for(app):
    """The floor is a floor, not a veto. Asking for a bit more on the way back
    still brings movements from the level above."""
    borrowed = sum(
        1 for uid in range(1400, 1460)
        for ex in appmod.get_daily_exercises(uid, '2026-06-01', 'beginner',
                                             effort='more', days_away=45)
        if ex.get('from_next_tier')
    )
    assert borrowed > 0, "a returning person who asked for more was quietly denied it"


def test_the_route_actually_passes_the_absence_along(app):
    """Covers the CALL SITE, not just the function.

    The previous test exercises get_daily_exercises directly, so hard-coding
    days_away=0 where todays_mission calls it passed everything — the
    suggestion is 'easy' on a return, and an easy day caps explosive work at
    zero all by itself, which hid the fact that the argument was never
    arriving. This goes through todays_mission with the suggestion overruled,
    which is the one path where the absence has to do the work itself.
    """
    u = _returning_user('route_absence', 'advanced', 30, 45, _ADVANCED_FIVE)
    db.session.add(appmod.DailyEffort(user_id=u.id, date=datetime.date.today(),
                                      level='usual'))
    db.session.commit()

    mission = appmod.todays_mission(u.id, 'advanced', datetime.date.today())
    assert mission['effort'] == 'usual', "their choice was overridden"
    assert mission['days_away'] == 45
    high = sum(1 for ex in mission['exercises'] if ex['impact'] == 'high')
    assert high <= 1, f"{high} explosive exercises on a first day back at 'my usual'"
