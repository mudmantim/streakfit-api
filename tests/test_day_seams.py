"""Step 0 of the D6/D50/D59 day-boundary work: one server clock, one streak walk.

Behaviour-preserving refactor. These tests pin that it *is* behaviour-preserving:
the shared helpers are checked against verbatim copies of the five walks that
existed at bb2c9b0, the consolidated SQL must render identically to the
queries it replaced, and the clock must steer every route that decides "today"
-- both when it is replaced directly and when a test patches `app.date` the way
existing tests already do.
"""
import ast
import datetime
import os
import random

import app as appmod
from app import DailyCompletion, User, db
from conftest import auth_headers, mission_json, register_and_login

APP_PY = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'app.py')


# ── Verbatim bb2c9b0 algorithms (the oracle) ─────────────────────────────────

def _old_current(date_set, today):
    # get_user_stats / get_daily5_streak / _witness_for_ids at bb2c9b0
    yesterday = today - datetime.timedelta(days=1)
    check = today if today in date_set else yesterday
    current = 0
    while check in date_set:
        current += 1
        check -= datetime.timedelta(days=1)
    return current


def _old_best(completed_dates):
    # get_user_stats / get_daily / _best_streak_crossing at bb2c9b0
    best = run = 0
    prev = None
    for d in completed_dates:
        run = run + 1 if (prev and (d - prev).days == 1) else 1
        best = max(best, run)
        prev = d
    return best


def _old_query(*filters):
    return (db.select(DailyCompletion.date).where(*filters)
            .group_by(DailyCompletion.date)
            .having(db.func.count(DailyCompletion.exercise_key) >= 5))


# ── The clock ────────────────────────────────────────────────────────────────

def test_date_today_is_never_read():
    """The server's day is the UTC date of its clock's one instant. The
    process-local `date.today()` -- which disagrees with UTC for hours a day
    outside a UTC process -- is not read anywhere in app.py."""
    tree = ast.parse(open(APP_PY).read())
    sites = [sub.lineno for sub in ast.walk(tree)
             if isinstance(sub, ast.Call) and isinstance(sub.func, ast.Attribute)
             and sub.func.attr == 'today' and isinstance(sub.func.value, ast.Name)
             and sub.func.value.id == 'date']
    assert sites == [], f'date.today() read at lines {sites}'


class _FixedClock:
    """One instant, at noon UTC on `day` (the clock is a single reading)."""

    def __init__(self, day):
        self.day = day

    def now(self):
        return datetime.datetime.combine(self.day, datetime.time(12, 0))


def test_replacing_the_clock_moves_the_mission_day(client, monkeypatch):
    token = register_and_login(client, 'seam_clock')
    h = auth_headers(token)
    day = datetime.date(2031, 2, 3)
    monkeypatch.setattr(appmod, 'server_clock', _FixedClock(day))

    daily = client.get('/api/daily', headers=h).get_json()
    assert daily['date'] == day.isoformat()
    for ex in daily['exercises']:
        assert client.post(f"/api/daily/{ex['key']}/complete", headers=h,
                           json=mission_json()).status_code == 200

    uid = db.session.execute(db.select(User.id).where(User.username == 'seam_clock')).scalar()
    stamped = set(db.session.execute(
        db.select(DailyCompletion.date).where(DailyCompletion.user_id == uid)).scalars())
    assert stamped == {day}
    assert appmod.get_user_stats(uid)['current_streak'] == 1

    # The next day the streak is still alive (yesterday counts) ...
    monkeypatch.setattr(appmod, 'server_clock', _FixedClock(day + datetime.timedelta(days=1)))
    assert appmod.get_user_stats(uid)['current_streak'] == 1
    # ... and the day after that it is not.
    monkeypatch.setattr(appmod, 'server_clock', _FixedClock(day + datetime.timedelta(days=2)))
    assert appmod.get_user_stats(uid)['current_streak'] == 0


def test_patching_app_datetime_still_steers_the_default_clock(monkeypatch):
    """Repro harnesses patch `app.datetime.utcnow`; the default clock reads it
    at call time, so that must keep working. (Since the Step 1 review the
    clock is one UTC instant: `app.date` no longer steers it.)"""
    target = datetime.datetime(2030, 1, 1, 23, 59, 59)

    class _Pinned(datetime.datetime):
        @classmethod
        def utcnow(cls):
            return target

    monkeypatch.setattr(appmod, 'datetime', _Pinned)
    assert appmod.server_today() == target.date()
    assert appmod.server_now() == target


def test_default_clock_is_the_utc_date_whatever_the_process_timezone():
    before = datetime.datetime.now(datetime.timezone.utc).date()
    day = appmod.server_today()
    after = datetime.datetime.now(datetime.timezone.utc).date()
    assert day in (before, after)


# ── The consolidated walk equals every old copy ─────────────────────────────

def test_shared_walks_match_the_bb2c9b0_walks_on_random_histories():
    rng = random.Random(6)
    base = datetime.date(2026, 1, 1)
    for _ in range(3000):
        days = sorted({base + datetime.timedelta(days=rng.randrange(0, 60))
                       for _ in range(rng.randrange(0, 40))})
        today = base + datetime.timedelta(days=rng.randrange(-2, 64))
        assert appmod._current_streak(set(days), today) == _old_current(set(days), today)
        assert appmod._best_streak(days) == _old_best(days)
        without = [d for d in days if d != today]
        assert appmod._best_streak(without) == _old_best(without)


def test_consolidated_queries_render_the_same_sql(app):
    uid_filter = DailyCompletion.user_id == 7
    since = DailyCompletion.date >= datetime.date(2026, 1, 1)
    assert str(appmod._mission_dates_query(uid_filter)) == str(_old_query(uid_filter))
    assert str(appmod._mission_dates_query(uid_filter, since)) == str(_old_query(uid_filter, since))


def _seed(uid, day_counts):
    for d, n in day_counts.items():
        for i in range(n):
            db.session.add(DailyCompletion(user_id=uid, date=d, exercise_key=f'k{i}'))
    db.session.commit()


def test_every_streak_reader_agrees_with_the_oracle(client, monkeypatch):
    """get_user_stats, get_daily5_streak, the team witness, the best-streak
    crossing and /api/daily's rise-again all read the same history the same way."""
    rng = random.Random(11)
    today = datetime.date(2026, 10, 15)
    monkeypatch.setattr(appmod, 'server_clock', _FixedClock(today))
    for i in range(12):
        token = register_and_login(client, f'seam_oracle_{i}')
        uid = db.session.execute(db.select(User.id).where(User.username == f'seam_oracle_{i}')).scalar()
        counts = {}
        for k in range(30):
            r = rng.random()
            d = today - datetime.timedelta(days=k)
            if r < 0.6:
                counts[d] = 5
            elif r < 0.7:
                counts[d] = rng.choice([1, 4, 6])
        _seed(uid, counts)
        mission = sorted(d for d, n in counts.items() if n >= 5)
        cur, best = _old_current(set(mission), today), _old_best(mission)

        stats = appmod.get_user_stats(uid)
        assert (stats['current_streak'], stats['best_streak'], stats['total_missions']) == (cur, best, len(mission))
        assert appmod.get_daily5_streak(uid) == cur
        assert appmod._witness_for_ids([uid])[uid]['current_streak'] == cur
        after = _old_best(mission)
        assert appmod._best_streak_crossing(uid, today) == (after, after - _old_best([d for d in mission if d != today]))

        daily = client.get('/api/daily', headers=auth_headers(token)).get_json()
        yesterday = today - datetime.timedelta(days=1)
        expect_rise = (bool(mission) and counts.get(today, 0) < 5
                       and yesterday not in set(mission) and best >= 7)
        assert daily.get('rise_again', False) == expect_rise


# ── Review remediation: no calendar day outside the clock (AST guard) ───────
#
# Narrow, structural rules instead of text matching. Each rule names a way a
# calendar day can be computed from the real clock or an instant; the
# self-test below proves every rule fires, so the guard cannot go vacuous.

_CLOCK_OWNERS = {'ServerClock.now', '_clock_snapshot', 'server_today', 'utc_day_start',
                 'mission_day_ends_at'}
_DATE_PARTS = {'year', 'month', 'day'}


def _calendar_day_violations(source):
    """[(qualified function name, line, rule)] for day derivations outside
    the clock. Module-level code counts as '<module>'."""
    tree = ast.parse(source)
    found = []

    def visit(node, owner):
        for child in ast.iter_child_nodes(node):
            if isinstance(child, ast.ClassDef):
                visit(child, child.name)
                continue
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                name = f'{owner}.{child.name}' if owner and owner[0].isupper() else child.name
                visit(child, name)
                continue
            if isinstance(child, ast.Call):
                rule = _rule_for(child)
                if rule and owner not in _CLOCK_OWNERS:
                    found.append((owner or '<module>', child.lineno, rule))
            visit(child, owner)

    visit(tree, '')
    return found


def _rule_for(call):
    f = call.func
    if isinstance(f, ast.Attribute):
        if f.attr == 'today' and not call.args:
            return 'X.today()'                                    # date.today(), datetime.date.today()
        if f.attr == 'date' and not call.args and not call.keywords:
            return 'instant.date()'                                # utcnow().date(), now.date()
        if f.attr == 'now' and isinstance(f.value, (ast.Name, ast.Attribute)):
            return 'datetime.now()'
        if f.attr in ('fromtimestamp', 'utcfromtimestamp', 'localtime', 'gmtime'):
            return f'{f.attr}()'
        if f.attr in ('current_date', 'current_timestamp', 'localtimestamp'):
            return f'sql {f.attr}'
    if isinstance(f, ast.Name) and f.id in ('datetime', 'date'):
        if any(isinstance(a, ast.Attribute) and a.attr in _DATE_PARTS for a in call.args):
            return 'datetime(x.year, x.month, x.day)'
    return None


def test_no_calendar_day_is_derived_outside_the_clock():
    violations = _calendar_day_violations(open(APP_PY).read())
    assert violations == [], f'calendar day computed outside ServerClock/utc_day_start: {violations}'


def test_the_guard_catches_each_bypass_shape():
    shapes = {
        'date.today()': 'def f():\n    return date.today()\n',
        'datetime.date.today()': 'def f():\n    return datetime.date.today()\n',
        'utcnow().date()': 'def f():\n    return datetime.utcnow().date()\n',
        'two-step instant.date()': 'def f():\n    now = datetime.utcnow()\n    return now.date()\n',
        'clock instant .date()': 'def f():\n    return server_clock.utcnow().date()\n',
        'datetime.now()': 'def f():\n    return datetime.now()\n',
        'fromtimestamp': 'def f():\n    return date.fromtimestamp(time.time())\n',
        'midnight truncation': 'def f(now):\n    return datetime(now.year, now.month, now.day)\n',
        'sql current_date': 'def f():\n    return db.func.current_date()\n',
        'module level': 'X = date.today()\n',
        'in a non-clock class': 'class Other:\n    def today(self):\n        return date.today()\n',
    }
    for label, src in shapes.items():
        assert _calendar_day_violations(src), f'guard missed: {label}'
    allowed = ('class ServerClock:\n    def now(self):\n        return datetime.utcnow()\n'
               'def server_today():\n    return _clock_snapshot().date()\n'
               'def utc_day_start(i):\n    return datetime(i.year, i.month, i.day)\n')
    assert _calendar_day_violations(allowed) == []


# ── Review remediation: every day-deciding path follows the clock ───────────
#
# The clock is set years away from the real one, so a path that bypasses it
# cannot agree with it by accident. It is one instant: the day and every
# timestamp come from it.

_FAR_DAY = datetime.date(2031, 2, 3)
_FAR_INSTANT = datetime.datetime(2031, 2, 3, 10, 30)


class _DistantClock:
    def __init__(self, day=_FAR_DAY):
        self.day = day

    def now(self):
        return datetime.datetime.combine(self.day, _FAR_INSTANT.time())


def _uid(username):
    return db.session.execute(db.select(User.id).where(User.username == username)).scalar()


def test_brain_boost_answer_is_filed_under_the_clock_day(client, monkeypatch):
    token = register_and_login(client, 'seam_bb')
    monkeypatch.setattr(appmod, 'server_clock', _DistantClock())
    r = client.post('/api/brain-boost/answer', json=mission_json(selected_index=0), headers=auth_headers(token))
    assert r.status_code == 200
    stamped = db.session.execute(db.select(appmod.BrainBoostAnswer.date)
                                 .where(appmod.BrainBoostAnswer.user_id == _uid('seam_bb'))).scalars().all()
    assert stamped == [_FAR_DAY]


def test_effort_choice_is_filed_under_the_clock_day(client, monkeypatch):
    token = register_and_login(client, 'seam_effort')
    monkeypatch.setattr(appmod, 'server_clock', _DistantClock())
    r = client.put('/api/daily/effort', json={'level': 'easy'}, headers=auth_headers(token))
    assert r.status_code == 200
    stamped = db.session.execute(db.select(appmod.DailyEffort.date)
                                 .where(appmod.DailyEffort.user_id == _uid('seam_effort'))).scalars().all()
    assert stamped == [_FAR_DAY]


def test_side_quest_check_in_uses_the_clock_day(client, monkeypatch):
    token = register_and_login(client, 'seam_sq')
    h = auth_headers(token)
    cid = client.post('/api/challenges', json={'title': 'walk'}, headers=h).get_json()['challenge_id']
    monkeypatch.setattr(appmod, 'server_clock', _DistantClock())
    assert client.post(f'/api/challenges/{cid}/checkin', headers=h).status_code == 200
    assert db.session.get(appmod.Challenge, cid).last_check_in == _FAR_DAY
    # Same clock day again: "already checked in" rather than a second check-in.
    again = client.post(f'/api/challenges/{cid}/checkin', headers=h)
    assert db.session.get(appmod.Challenge, cid).current_streak == 1, again.get_json()


def test_week_strip_ends_on_the_clock_day(client, monkeypatch):
    register_and_login(client, 'seam_week')
    uid = _uid('seam_week')
    _seed(uid, {_FAR_DAY: 5, _FAR_DAY - datetime.timedelta(days=2): 5})
    monkeypatch.setattr(appmod, 'server_clock', _DistantClock())
    week = appmod.get_recent_week(uid)
    assert [w['date'] for w in week][-1] == _FAR_DAY.isoformat()
    assert [w['is_today'] for w in week] == [False] * 6 + [True]
    assert [w['done'] for w in week] == [False, False, False, False, True, False, True]


def test_demo_mission_is_the_clock_day(client, monkeypatch):
    monkeypatch.setattr(appmod, 'server_clock', _DistantClock())
    body = client.get('/api/demo/daily').get_json()
    assert body['date'] == _FAR_DAY.isoformat()
    expected = {e['key'] for e in appmod.get_daily_exercises('demo', _FAR_DAY.isoformat(), 'beginner')}
    assert {e['key'] for e in body['exercises']} == expected


def test_team_witness_judges_by_the_clock_day(client, monkeypatch):
    register_and_login(client, 'seam_wit')
    uid = _uid('seam_wit')
    _seed(uid, {_FAR_DAY: 5, _FAR_DAY - datetime.timedelta(days=1): 5})
    monkeypatch.setattr(appmod, 'server_clock', _DistantClock())
    w = appmod._witness_for_ids([uid])[uid]
    assert (w['completed_today'], w['completed_today_count'], w['current_streak']) == (True, 5, 2)


def test_milestone_fallbacks_use_the_clock_day(client, monkeypatch):
    """A team-challenge completion calls _completion_milestones without a day;
    both fallbacks must then ask the clock."""
    register_and_login(client, 'seam_ms')
    uid = _uid('seam_ms')
    user = db.session.get(User, uid)
    seen = {}
    monkeypatch.setattr(appmod, '_best_streak_crossing',
                        lambda u, d: seen.setdefault('best', d) and (0, 0))
    monkeypatch.setattr(appmod, '_days_active_crossing',
                        lambda u, d: seen.setdefault('active', d) and (None, 0))
    monkeypatch.setattr(appmod, 'server_clock', _DistantClock())
    events = [{'event_type': 'mission_complete', 'xp_awarded': 0, 'acorns_awarded': 0}]
    appmod._completion_milestones(uid, user, 1, events, awarded=True, daily_activity=True, today=None)
    assert seen == {'best': _FAR_DAY, 'active': _FAR_DAY}


def test_coach_insight_matches_the_clock_days_insight(client, monkeypatch):
    register_and_login(client, 'seam_coach')
    uid = _uid('seam_coach')
    # A clock day whose insight is one the coach offers "tell me more" on.
    day = next(_FAR_DAY + datetime.timedelta(days=i) for i in range(60)
               if appmod.get_daily_insight((_FAR_DAY + datetime.timedelta(days=i)).isoformat(), uid)
               .get('type') in appmod._COACH_TELL_MORE_TYPES)
    item = appmod.get_daily_insight(day.isoformat(), uid)
    monkeypatch.setattr(appmod, 'server_clock', _DistantClock(day=day))
    assert appmod._coach_insight_for(uid, item['text'].strip()) == item
    # The day after, the same text is still recognised as "yesterday's".
    monkeypatch.setattr(appmod, 'server_clock', _DistantClock(day=day + datetime.timedelta(days=1)))
    assert appmod._coach_insight_for(uid, item['text'].strip()) == item


def test_admin_days_follow_the_clock(client, monkeypatch):
    """Admin's completion day, event window and generated_at all come from
    the request's one instant; a bypass of any of them shows against a clock
    years from the real one."""
    monkeypatch.setenv('ADMIN_SECRET', 'seam-admin-secret')
    register_and_login(client, 'seam_admin')
    uid = _uid('seam_admin')
    _seed(uid, {_FAR_DAY: 2})
    db.session.add(appmod.AnalyticsEvent(event_name='account_created',
                                         created_at=_FAR_INSTANT.replace(hour=0, minute=5)))
    db.session.commit()
    monkeypatch.setattr(appmod, 'server_clock', _DistantClock())
    body = client.get('/api/admin/stats', headers={'X-Admin-Secret': 'seam-admin-secret'}).get_json()
    assert body['generated_at'] == _FAR_INSTANT.isoformat() + 'Z'
    assert body['mission_completions']['today'] == 2
    assert body['active_users']['today_by_completion'] == 1
    assert body['events']['account_created']['today'] == 1
