"""Season for photos: a person picks the Seasonal filter's season.

Chip (2026-11-12): the Seasonal filter assumes northern-hemisphere seasons by
the UTC date, which is wrong for anyone south of the equator. Tim approved a
small optional setting: Auto (the old behaviour) or Spring/Summer/Fall/Winter.

What must hold:
- Auto is the default and is exactly the old behaviour: no ?season, or any
  value that is not one of the four keys, gives the date's season;
- a chosen season changes only the Seasonal filter's name/blurb/render/season,
  never its key, unlock, or any other filter;
- the choice needs no unlock and does not affect uploads (key stays 'seasonal');
- Rickie Off is unaffected: same catalog with no Rickie, season honoured;
- no schema change: the choice lives on the device (like Rickie's roaming),
  per account, and is sent as ?season=;
- the Settings control is labelled, described, sits under the Rickie rows,
  stays visible under Off and is hidden for guests.
"""
import datetime
import re
from pathlib import Path

import pytest

import app as appmod
from app import User, db
from conftest import auth_headers, register_and_login

ROOT = Path(__file__).resolve().parent.parent
APP_JS = (ROOT / 'static' / 'app.js').read_text(encoding='utf-8')
STYLE = (ROOT / 'static' / 'style.css').read_text(encoding='utf-8')
INDEX = (ROOT / 'static' / 'index.html').read_text(encoding='utf-8')
SW = (ROOT / 'static' / 'sw.js').read_text(encoding='utf-8')

WINTER_DAY = datetime.date(2031, 1, 15)    # Auto says winter


class _FixedClock:
    def __init__(self, day):
        self.day = day

    def now(self):
        return datetime.datetime.combine(self.day, datetime.time(12, 0))


@pytest.fixture()
def clock(monkeypatch):
    def set_day(day):
        monkeypatch.setattr(appmod, 'server_clock', _FixedClock(day))
    set_day(WINTER_DAY)
    return set_day


def _catalog(client, token, query=''):
    r = client.get('/api/photo-filters' + query, headers=auth_headers(token))
    assert r.status_code == 200, r.get_json()
    return r.get_json()['filters']


def _by_key(filters):
    return {f['key']: f for f in filters}


# ── Server: Auto is the old behaviour ───────────────────────────────────────

AUTO_QUERIES = ['', '?season=', '?season=auto', '?season=Spring', '?season=monsoon',
                '?season=spring%20', '?season=__class__', '?season=render']


@pytest.mark.parametrize('n', range(len(AUTO_QUERIES)))
def test_auto_or_anything_unknown_is_the_dates_season(client, clock, n):
    query = AUTO_QUERIES[n]
    token = register_and_login(client, f'ps_auto_{n}')
    f = _by_key(_catalog(client, token, query))['seasonal']
    assert f['season'] == 'winter' and f['name'] == 'Winter'
    assert f['render'] == appmod.SEASONAL_LOOKS['winter']['render']


@pytest.mark.parametrize('season', ['spring', 'summer', 'fall', 'winter'])
def test_a_chosen_season_sets_the_seasonal_look(client, clock, season):
    token = register_and_login(client, 'ps_pick_' + season)
    f = _by_key(_catalog(client, token, '?season=' + season))['seasonal']
    look = appmod.SEASONAL_LOOKS[season]
    assert f['season'] == season
    assert f['name'] == look['name'] and f['blurb'] == look['blurb']
    assert f['render'] == look['render']
    assert f['unlocked'] is True and f['unlock_type'] == 'free'


def test_the_choice_wins_in_every_month(client, clock):
    token = register_and_login(client, 'ps_months')
    for month in range(1, 13):
        clock(datetime.date(2031, month, 10))
        assert _by_key(_catalog(client, token, '?season=summer'))['seasonal']['season'] == 'summer'


def test_a_choice_changes_nothing_but_the_seasonal_entry(client, clock):
    token = register_and_login(client, 'ps_only_seasonal')
    auto = _catalog(client, token)
    picked = _catalog(client, token, '?season=summer')
    assert [f['key'] for f in auto] == [f['key'] for f in picked]
    for a, p in zip(auto, picked):
        if a['key'] == 'seasonal':
            assert {k for k in a if a[k] != p.get(k)} == {'name', 'blurb', 'render', 'season'}
        else:
            assert a == p, a['key']


def test_the_shared_catalog_is_never_changed(client, clock):
    token = register_and_login(client, 'ps_shared')
    _catalog(client, token, '?season=fall')
    spec = appmod.PHOTO_FILTERS_BY_KEY['seasonal']
    assert spec['name'] == 'Seasonal' and spec['render'] == {} and 'season' not in spec


def test_chosen_season_accepts_only_the_four_keys():
    for s in ('spring', 'summer', 'fall', 'winter'):
        assert appmod.chosen_season(s) == s
    for bad in (None, '', 'auto', 'Winter', 'autumn', 1, ['spring'], {'spring': 1}):
        assert appmod.chosen_season(bad) is None


def test_filter_for_day_default_is_unchanged():
    spec = appmod.PHOTO_FILTERS_BY_KEY['seasonal']
    assert appmod._filter_for_day(spec, WINTER_DAY)['season'] == 'winter'
    assert appmod._filter_for_day(spec, WINTER_DAY, 'summer')['season'] == 'summer'
    plain = appmod.PHOTO_FILTERS_BY_KEY['black_white']
    assert appmod._filter_for_day(plain, WINTER_DAY, 'summer') is plain


def test_uploads_are_unaffected(client, clock):
    """The key is 'seasonal' whatever the season, so the upload check is the
    same call as before and needs no season."""
    register_and_login(client, 'ps_upload')
    uid = db.session.execute(db.select(User.id).where(User.username == 'ps_upload')).scalar()
    assert appmod._filter_is_usable(db.session.get(User, uid), uid, 'seasonal') is True


def test_the_filters_list_still_needs_a_login(client):
    assert client.get('/api/photo-filters?season=summer').status_code == 401


# ── Rickie Off is unaffected ─────────────────────────────────────────────────

def test_off_honours_the_season_and_still_has_no_rickie(client, clock):
    token = register_and_login(client, 'ps_off')
    full = _by_key(_catalog(client, token, '?season=spring'))
    client.patch('/api/me', json={'rickie_mode': 'off'}, headers=auth_headers(token))
    off_auto = _by_key(_catalog(client, token))
    off = _by_key(_catalog(client, token, '?season=spring'))
    assert off['seasonal'] == full['seasonal']
    assert off['seasonal']['season'] == 'spring'
    assert off_auto['seasonal']['season'] == 'winter'
    assert not (appmod.RICKIE_ONLY_FILTERS & set(off))
    assert set(off) == set(off_auto)
    for f in off.values():
        assert 'rickie' not in (f['name'] + ' ' + f['blurb']).lower(), f['key']
        assert 'rickie' not in str(f.get('render')).lower(), f['key']


def test_no_schema_change():
    """Kept on the device: the User model gains no season column."""
    assert not [c for c in User.__table__.columns if 'season' in c.name]


# ── The page ─────────────────────────────────────────────────────────────────

def _code(text):
    text = re.sub(r'<!--.*?-->', '', text, flags=re.S)
    return re.sub(r'(?m)^\s*//.*$', '', text)


def test_settings_row_is_labelled_described_and_under_the_rickie_rows():
    html = _code(INDEX)
    menu = html.split('id="settings-menu"', 1)[1]
    rows = re.findall(r'class="settings-row" id="([\w-]+)"', menu)
    # The Rickie order fix stays as it was.
    assert rows[:3] == ['settings-row-skill', 'settings-row-rickie', 'settings-row-roam']
    assert rows[3] == 'settings-row-season'
    assert '<label class="settings-label" for="photo-season-select">Season for photos</label>' in html
    sel = re.search(r'<select id="photo-season-select"[^>]*>(.*?)</select>', html, re.S)
    assert 'aria-describedby="photo-season-help"' in sel.group(0)
    opts = re.findall(r'<option value="(\w+)"', sel.group(1))
    assert opts == ['auto', 'spring', 'summer', 'fall', 'winter']   # Auto first = default
    assert 'id="photo-season-help"' in html


def test_the_season_row_stays_visible_under_off():
    css = re.sub(r'/\*.*?\*/', '', STYLE, flags=re.S)
    assert 'settings-row-season' not in css and 'photo-season-help' not in css


def test_guests_do_not_see_the_season_row():
    guest = re.search(r'function setGuestUI\(guest\) \{(.*?)\n\}', _code(APP_JS), re.S).group(1)
    assert "'settings-row-season'" in guest and "'photo-season-help'" in guest


def test_the_client_keeps_it_per_account_and_sends_it_only_when_chosen():
    js = _code(APP_JS)
    assert "'sf_photo_season_' + currentUser.id" in js
    refresh = re.search(r'async function _refreshPhotoFilters\(\) \{(.*?)\n\}', js, re.S).group(1)
    assert "season === 'auto' ? ''" in refresh and "'?season='" in refresh
    pref = re.search(r'function _photoSeasonPref\(\) \{(.*?)\n\}', js, re.S).group(1)
    assert "return 'auto'" in pref and 'PHOTO_SEASONS.indexOf(v) >= 0' in pref
    assert 'try { v = localStorage.getItem(key); }' in pref
    assert re.findall(r"PHOTO_SEASONS = \[(.*?)\]", js) == ["'spring', 'summer', 'fall', 'winter'"]


def test_service_worker_cache_bumped():
    assert "const CACHE = 'streakfit-v1017';" in SW
