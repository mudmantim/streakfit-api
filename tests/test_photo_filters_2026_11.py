"""The 2026-11-12 photo filters and the Settings order fix.

Owner (2026-11-12): "start the branch with all of them" -- new looks, fitness
and streak frames, a seasonal filter, and the Rickie mode row moved up in
Settings because on a phone he found "Rickie's roaming" and never found Off.

What must hold:
- the catalog keeps its shape, and every filter that existed before keeps its
  key, its unlock rule and its price, in the same order;
- a streak frame is only usable by somebody who has actually had that streak
  (no "100-DAY STREAK" banner on a 3-day streak), and it is not taken away
  when the streak later ends (same rule as On Fire);
- the seasonal filter follows the server's mission day (UTC, as the rest of
  the app), one key, four looks;
- none of the new filters has Rickie in it, so all of them are in the Off
  picker unchanged;
- what somebody already bought or earned stays theirs.
"""
import datetime
import re
from pathlib import Path

import pytest

import app as appmod
from app import DailyCompletion, User, UserFilterUnlock, db
from conftest import auth_headers, register_and_login

ROOT = Path(__file__).resolve().parent.parent
APP_JS = (ROOT / 'static' / 'app.js').read_text(encoding='utf-8')
STYLE = (ROOT / 'static' / 'style.css').read_text(encoding='utf-8')
INDEX = (ROOT / 'static' / 'index.html').read_text(encoding='utf-8')

# The catalog as it was at 8099e91 (production): key -> unlock rule. Order too.
EXISTING = [
    ('none', {'type': 'free'}),
    ('rickie_peek', {'type': 'free'}),
    ('campfire_frame', {'type': 'free'}),
    ('day_stamp', {'type': 'free'}),
    ('mission_complete', {'type': 'missions', 'value': 1}),
    ('streak_fire', {'type': 'streak', 'value': 3}),
    ('acorn_shower', {'type': 'level', 'value': 3}),
    ('rickie_crew', {'type': 'level', 'value': 5}),
    ('team_challenge', {'type': 'missions', 'value': 5}),
    ('first_mission_gold', {'type': 'milestone', 'key': 'first_mission'}),
    ('golden_hour', {'type': 'acorns', 'cost': 20}),
    ('frosty', {'type': 'acorns', 'cost': 30}),
    ('sweat_mode', {'type': 'acorns', 'cost': 15}),
    ('night_owl', {'type': 'acorns', 'cost': 25}),
]

NEW = {
    'black_white': {'type': 'free'},
    'vintage': {'type': 'acorns', 'cost': 20},
    'bold_pop': {'type': 'acorns', 'cost': 15},
    'soft_glow': {'type': 'acorns', 'cost': 25},
    'personal_best': {'type': 'missions', 'value': 3},
    'rest_day': {'type': 'free'},
    'week_complete': {'type': 'missions', 'value': 7},
    'streak_7': {'type': 'streak', 'value': 7},
    'streak_30': {'type': 'streak', 'value': 30},
    'streak_100': {'type': 'streak', 'value': 100},
    'team_shot': {'type': 'free'},
    'seasonal': {'type': 'free'},
}

TODAY = datetime.date(2031, 2, 3)     # far from the real clock, on purpose


class _FixedClock:
    def __init__(self, day):
        self.day = day

    def now(self):
        return datetime.datetime.combine(self.day, datetime.time(12, 0))


@pytest.fixture()
def clock(monkeypatch):
    def set_day(day):
        monkeypatch.setattr(appmod, 'server_clock', _FixedClock(day))
    set_day(TODAY)
    return set_day


def _uid(username):
    return db.session.execute(db.select(User.id).where(User.username == username)).scalar()


def _seed_mission_days(username, days):
    """Mission days (five completions each) on the given dates."""
    uid = _uid(username)
    for day in days:
        for k in range(5):
            db.session.add(DailyCompletion(user_id=uid, date=day, exercise_key=f'seed_{k}'))
    db.session.commit()
    return uid


def _run(end, length):
    return [end - datetime.timedelta(days=i) for i in range(length)]


def _catalog(client, token):
    r = client.get('/api/photo-filters', headers=auth_headers(token))
    assert r.status_code == 200, r.get_json()
    return {f['key']: f for f in r.get_json()['filters']}


# ── Catalog shape ────────────────────────────────────────────────────────────

def test_every_existing_filter_keeps_its_key_rule_and_place():
    keys = [f['key'] for f in appmod.PHOTO_FILTERS]
    assert keys[:len(EXISTING)] == [k for k, _ in EXISTING]
    for key, rule in EXISTING:
        assert appmod.PHOTO_FILTERS_BY_KEY[key]['unlock'] == rule, key


def test_all_the_new_filters_are_in_the_catalog_with_their_rules():
    for key, rule in NEW.items():
        assert appmod.PHOTO_FILTERS_BY_KEY[key]['unlock'] == rule, key
    assert len(appmod.PHOTO_FILTERS) == len(EXISTING) + len(NEW)
    assert len(set(f['key'] for f in appmod.PHOTO_FILTERS)) == len(appmod.PHOTO_FILTERS)
    # Keys fit the purchase table's column.
    assert all(len(f['key']) <= 40 for f in appmod.PHOTO_FILTERS)


def test_golden_hour_is_not_duplicated():
    names = [f['name'] for f in appmod.PHOTO_FILTERS]
    assert names.count('Golden Hour') == 1


def test_new_filters_have_no_rickie_picture_or_voice():
    for key in NEW:
        spec = appmod.PHOTO_FILTERS_BY_KEY[key]
        assert 'overlays' not in spec['render'], key
        assert 'rickie' not in (spec['name'] + spec['blurb']).lower(), key
        assert 'rickie' not in repr(spec).lower(), key
    for season, look in appmod.SEASONAL_LOOKS.items():
        assert 'rickie' not in repr(look).lower(), season
        assert 'overlays' not in look['render'], season


def test_streak_frames_say_exactly_the_streak_they_need():
    for n in (7, 30, 100):
        spec = appmod.PHOTO_FILTERS_BY_KEY[f'streak_{n}']
        assert spec['unlock'] == {'type': 'streak', 'value': n}
        assert spec['render']['ribbon']['text'] == f'{n}-DAY STREAK'
        # No live stat chip: "Day 3" next to "100-DAY STREAK" would read as a
        # contradiction after a streak ends.
        assert 'stat' not in spec['render']


def test_acorn_prices_sit_in_the_existing_range():
    prices = [f['unlock']['cost'] for f in appmod.PHOTO_FILTERS if f['unlock']['type'] == 'acorns']
    assert min(prices) >= 15 and max(prices) <= 30


# ── Unlock / offer rules: streak frames need the real streak ────────────────

def test_a_new_account_has_the_free_new_ones_and_none_of_the_earned_ones(client, clock):
    token = register_and_login(client, 'pf_new')
    cat = _catalog(client, token)
    for key in ('black_white', 'rest_day', 'team_shot', 'seasonal'):
        assert cat[key]['unlocked'] is True, key
    for key in ('personal_best', 'week_complete', 'streak_7', 'streak_30', 'streak_100',
                'vintage', 'bold_pop', 'soft_glow'):
        assert cat[key]['unlocked'] is False, key
    assert cat['streak_100']['requirement'] == 'Reach a 100-day streak'
    assert cat['week_complete']['requirement'] == 'Finish 7 missions'
    assert cat['vintage']['cost'] == 20


def test_a_three_day_streak_gets_no_bigger_streak_frame(client, clock):
    token = register_and_login(client, 'pf_three')
    uid = _seed_mission_days('pf_three', _run(TODAY, 3))
    cat = _catalog(client, token)
    assert cat['streak_fire']['unlocked'] is True
    assert cat['personal_best']['unlocked'] is True          # 3 missions
    for key in ('streak_7', 'streak_30', 'streak_100', 'week_complete'):
        assert cat[key]['unlocked'] is False, key
    user = db.session.get(User, uid)
    assert appmod._filter_is_usable(user, uid, 'streak_100') is False
    assert appmod._filter_is_usable(user, uid, 'streak_7') is False


def test_a_seven_day_streak_unlocks_the_seven_day_frame_only(client, clock):
    token = register_and_login(client, 'pf_seven')
    uid = _seed_mission_days('pf_seven', _run(TODAY, 7))
    cat = _catalog(client, token)
    assert cat['streak_7']['unlocked'] is True
    assert cat['week_complete']['unlocked'] is True
    assert cat['streak_30']['unlocked'] is False
    assert cat['streak_30']['progress'] == 7
    user = db.session.get(User, uid)
    assert appmod._filter_is_usable(user, uid, 'streak_7') is True
    assert appmod._filter_is_usable(user, uid, 'streak_30') is False


def test_seven_scattered_missions_are_a_week_complete_but_not_a_streak(client, clock):
    token = register_and_login(client, 'pf_scatter')
    _seed_mission_days('pf_scatter', [TODAY - datetime.timedelta(days=2 * i) for i in range(7)])
    cat = _catalog(client, token)
    assert cat['week_complete']['unlocked'] is True
    assert cat['streak_7']['unlocked'] is False


def test_a_thirty_day_streak_that_ended_keeps_its_frame(client, clock):
    """Earned once, kept: the banner states something that happened."""
    token = register_and_login(client, 'pf_thirty')
    _seed_mission_days('pf_thirty', _run(TODAY - datetime.timedelta(days=10), 30))
    cat = _catalog(client, token)
    assert cat['streak_30']['unlocked'] is True
    assert cat['streak_7']['unlocked'] is True
    assert cat['streak_100']['unlocked'] is False
    assert cat['streak_30']['progress'] == 0                  # current streak is over


def test_a_hundred_day_streak_unlocks_the_hundred_frame(client, clock):
    token = register_and_login(client, 'pf_hundred')
    uid = _seed_mission_days('pf_hundred', _run(TODAY, 100))
    cat = _catalog(client, token)
    assert all(cat[k]['unlocked'] for k in ('streak_7', 'streak_30', 'streak_100'))
    assert appmod._filter_is_usable(db.session.get(User, uid), uid, 'streak_100') is True


def test_reaching_seven_days_announces_the_seven_day_frame(client, clock):
    """The mission that makes day 7 reports the new frame, like other earned
    filters (filters_unlocked)."""
    token = register_and_login(client, 'pf_announce')
    _seed_mission_days('pf_announce', _run(TODAY - datetime.timedelta(days=1), 6))
    h = auth_headers(token)
    daily = client.get('/api/daily', headers=h).get_json()
    gained = []
    for ex in daily['exercises']:
        r = client.post(f"/api/daily/{ex['key']}/complete", headers=h,
                        json={'mission_date': TODAY.isoformat()})
        assert r.status_code == 200, r.get_json()
        gained += [f['key'] for f in r.get_json().get('filters_unlocked', [])]
    assert 'streak_7' in gained
    assert 'week_complete' in gained
    assert 'streak_30' not in gained


def test_streak_frames_cannot_be_bought(client, clock):
    token = register_and_login(client, 'pf_buy_streak')
    user = db.session.execute(db.select(User).where(User.username == 'pf_buy_streak')).scalar_one()
    user.acorns_total = 500
    db.session.commit()
    for key in ('streak_7', 'streak_100', 'personal_best', 'seasonal'):
        r = client.post(f'/api/photo-filters/{key}/unlock', headers=auth_headers(token))
        assert r.status_code == 400 and r.get_json()['error'] == 'not_purchasable', key


def test_a_new_look_can_be_bought_with_the_existing_two_step_price(client, clock):
    token = register_and_login(client, 'pf_buy_look')
    user = db.session.execute(db.select(User).where(User.username == 'pf_buy_look')).scalar_one()
    user.acorns_total = 40
    db.session.commit()
    r = client.post('/api/photo-filters/bold_pop/unlock', headers=auth_headers(token))
    assert r.status_code == 200
    assert r.get_json()['acorns_available'] == 25
    assert _catalog(client, token)['bold_pop']['unlocked'] is True


# ── Seasonal: one key, the look follows the server's mission day ───────────

@pytest.mark.parametrize('day,season,name', [
    (datetime.date(2031, 12, 1), 'winter', 'Winter'),
    (datetime.date(2032, 2, 29), 'winter', 'Winter'),
    (datetime.date(2031, 3, 1), 'spring', 'Spring'),
    (datetime.date(2031, 5, 31), 'spring', 'Spring'),
    (datetime.date(2031, 6, 1), 'summer', 'Summer'),
    (datetime.date(2031, 8, 31), 'summer', 'Summer'),
    (datetime.date(2031, 9, 1), 'fall', 'Fall'),
    (datetime.date(2031, 11, 30), 'fall', 'Fall'),
])
def test_the_seasonal_filter_follows_the_mission_day(client, clock, day, season, name):
    clock(day)
    token = register_and_login(client, f'pf_season_{day.isoformat()}')
    f = _catalog(client, token)['seasonal']
    assert f['season'] == season
    assert f['name'] == name
    assert f['render'] == appmod.SEASONAL_LOOKS[season]['render']
    assert f['unlocked'] is True


def test_every_month_has_exactly_one_season():
    assert sorted(appmod.SEASON_BY_MONTH) == list(range(1, 13))
    assert set(appmod.SEASON_BY_MONTH.values()) == set(appmod.SEASONAL_LOOKS)
    for look in appmod.SEASONAL_LOOKS.values():
        assert look['name'] and look['blurb'] and look['render']


def test_the_season_is_the_utc_mission_day_not_the_wall_clock():
    """Resolved from server_today() (UTC), the same day the mission uses."""
    src = Path(appmod.__file__).read_text(encoding='utf-8')
    body = re.search(r'def _filter_unlock_state\(.*?\n(?=def )', src, re.S).group(0)
    assert 'today = server_today()' in body
    assert '_filter_for_day(spec, today, season)' in body


def test_resolving_a_season_never_changes_the_shared_catalog(client, clock):
    clock(datetime.date(2031, 7, 4))
    token = register_and_login(client, 'pf_season_shared')
    _catalog(client, token)
    spec = appmod.PHOTO_FILTERS_BY_KEY['seasonal']
    assert spec['name'] == 'Seasonal' and spec['render'] == {} and 'season' not in spec


def test_seasonal_filter_is_usable_at_upload(client, clock):
    register_and_login(client, 'pf_season_upload')
    uid = _uid('pf_season_upload')
    assert appmod._filter_is_usable(db.session.get(User, uid), uid, 'seasonal') is True


# ── Rickie Off still has no Rickie, and keeps every new filter ──────────────

def test_off_keeps_every_new_filter_unchanged(client, clock):
    token = register_and_login(client, 'pf_off')
    full = _catalog(client, token)
    client.patch('/api/me', json={'rickie_mode': 'off'}, headers=auth_headers(token))
    off = _catalog(client, token)
    for key in NEW:
        assert off[key] == full[key], key
    assert not (appmod.RICKIE_ONLY_FILTERS & set(off))
    art = [o.get('src') for f in off.values()
           for o in (f.get('render') or {}).get('overlays', [])
           if 'rickie' in str(o.get('src', '')).lower()]
    assert art == []
    for f in off.values():
        assert 'rickie' not in (f['name'] + ' ' + f['blurb']).lower(), f['key']


# ── What somebody already has stays theirs ───────────────────────────────────

def test_an_existing_purchase_and_earned_filter_survive(client, clock):
    token = register_and_login(client, 'pf_keep')
    uid = _seed_mission_days('pf_keep', _run(TODAY, 3))
    user = db.session.get(User, uid)
    user.acorns_total = 60
    db.session.commit()
    assert client.post('/api/photo-filters/frosty/unlock',
                       headers=auth_headers(token)).status_code == 200
    cat = _catalog(client, token)
    assert cat['frosty']['unlocked'] is True
    assert cat['streak_fire']['unlocked'] is True
    assert cat['mission_complete']['unlocked'] is True
    rows = db.session.execute(
        db.select(UserFilterUnlock).where(UserFilterUnlock.user_id == uid)).scalars().all()
    assert [r.filter_key for r in rows] == ['frosty']
    assert db.session.get(User, uid).acorns_spent == 30
    assert _catalog(client, token)['frosty']['cost'] == 30


# ── The page: primitives exist, Settings order ──────────────────────────────

def _code(text):
    text = re.sub(r'<!--.*?-->', '', text, flags=re.S)
    return re.sub(r'(?m)^\s*//.*$', '', text)


def test_the_compositor_implements_the_new_primitives():
    js = _code(APP_JS)
    body = re.search(r'async function renderPhotoToCanvas\(canvas, img, spec\) \{(.*?)\n\}', js, re.S).group(1)
    assert 'if (spec.mono)' in body and "'saturation'" in body
    assert 'if (spec.wash)' in body
    assert 'if (spec.glow)' in body and '_canvasFilterWorks(ctx)' in body
    # A long banner squeezes instead of clipping on a narrow photo.
    assert "fillText(r.text || '', w / 2, bandY + bandH / 2, w * 0.92)" in body


def test_settings_put_rickie_mode_directly_under_difficulty():
    html = _code(INDEX)
    menu = html.split('id="settings-menu"', 1)[1]
    rows = re.findall(r'class="settings-row" id="([\w-]+)"', menu)
    assert rows[:3] == ['settings-row-skill', 'settings-row-rickie', 'settings-row-roam']
    assert rows.index('settings-row-rickie') < rows.index('settings-row-name')


def test_the_roaming_row_points_at_off_and_is_described_by_it():
    html = _code(INDEX)
    toggle = re.search(r'<button id="rickie-roam-toggle"[^>]*>', html, re.S).group(0)
    assert 'aria-describedby="rickie-roam-help"' in toggle
    hint = ' '.join(re.search(r'id="rickie-roam-help">(.*?)</p>', html, re.S).group(1).split())
    assert 'turn Rickie off completely' in hint and 'Off' in hint
    # The mode select keeps its label and help.
    assert '<label class="settings-label" for="rickie-mode-select">Rickie</label>' in html
    assert 'aria-describedby="rickie-mode-help"' in html


def test_the_roaming_hint_hides_with_the_roaming_row_and_for_guests():
    rule = re.search(r'((?:body\.rickie-off [^,{]+,\s*)*body\.rickie-off [^,{]+)\{\s*display:\s*none !important;',
                     re.sub(r'/\*.*?\*/', '', STYLE, flags=re.S)).group(1)
    hidden = {s.strip().replace('body.rickie-off ', '') for s in rule.split(',')}
    assert {'#settings-row-roam', '#rickie-roam-help'} <= hidden
    guest = re.search(r'function setGuestUI\(guest\) \{(.*?)\n\}', _code(APP_JS), re.S).group(1)
    assert "'rickie-roam-help'" in guest and "'rickie-mode-help'" in guest
