"""The mission subtitle names the mission's own day and its real refresh time.

Found 2026-11-08 in the real-user journey: the subtitle printed the DEVICE's
date and "Refreshes tomorrow". The mission day is the server's (UTC until the
Step 2 local day ships), so:

  * in UTC+14 at 03:00 the page said "Monday, Nov 9" over Sunday's mission;
  * in UTC-5 the mission changes at 7 PM the same evening, so "Refreshes
    tomorrow" was false every afternoon, and after 7 PM the label named Sunday
    over Monday's mission.

The fix takes both facts from /api/daily (`mission_date`, `day_ends_in_s`).
These tests pin that: the static check always runs; the behavioural check runs
the real function under node when node is installed (CI has none; the browser
version of the same check is uicheck's `check_mission_day_label_is_the_servers`).
"""
import json
import pathlib
import re
import shutil
import subprocess

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
APP_JS = (ROOT / 'static' / 'app.js').read_text(encoding='utf-8')


def _function(name):
    m = re.search(r'function ' + name + r'\(.*?\n}\n', APP_JS, flags=re.S)
    assert m, f'{name} not found in app.js'
    return m.group(0)


def test_subtitle_is_built_from_the_servers_mission_day():
    render = APP_JS[APP_JS.index("getElementById('daily-mission-date')"):][:600]
    assert 'missionDayLabels(daily' in render
    assert 'Refreshes tomorrow' not in render, 'the refresh time must come from the server'
    helper = _function('missionDayLabels')
    assert 'mission_date' in helper and 'day_ends_in_s' in helper
    assert "timeZone: 'UTC'" in helper, 'the calendar date must not shift with the device zone'


CASES = [
    # (device TZ, now UTC, mission_date, seconds left, expected day, expected refresh)
    ('Pacific/Kiritimati', '2026-11-08T13:05:00Z', '2026-11-08', 39300,
     'Sunday, Nov 8', 'New mission at 2:00 PM'),
    ('America/New_York', '2026-11-08T22:00:00Z', '2026-11-08', 7200,
     'Sunday, Nov 8', 'New mission at 7:00 PM'),
    ('America/New_York', '2026-11-09T01:00:00Z', '2026-11-09', 82800,
     'Monday, Nov 9', 'New mission tomorrow at 7:00 PM'),
    ('Pacific/Pago_Pago', '2026-11-08T13:05:00Z', '2026-11-08', 39300,
     'Sunday, Nov 8', 'New mission at 1:00 PM'),
]


@pytest.mark.skipif(shutil.which('node') is None, reason='node not installed')
@pytest.mark.parametrize('tz,now,mission,left,day,refresh', CASES)
def test_labels_in_far_apart_zones(tz, now, mission, left, day, refresh):
    script = (_function('missionDayLabels')
              + f"console.log(JSON.stringify(missionDayLabels("
              f"{{mission_date:{json.dumps(mission)},day_ends_in_s:{left}}}, Date.parse({json.dumps(now)}))));")
    out = subprocess.run(['node', '-e', script], capture_output=True, text=True, timeout=30,
                         env={'TZ': tz, 'PATH': '/usr/bin:/bin:/usr/local/bin'}, check=True)
    got = json.loads(out.stdout)
    assert got == {'day': day, 'refresh': refresh}


@pytest.mark.skipif(shutil.which('node') is None, reason='node not installed')
def test_without_server_fields_it_falls_back_honestly():
    script = (_function('missionDayLabels')
              + "console.log(JSON.stringify(missionDayLabels({}, Date.parse('2026-11-08T13:00:00Z'))));")
    out = subprocess.run(['node', '-e', script], capture_output=True, text=True, timeout=30,
                         env={'TZ': 'UTC', 'PATH': '/usr/bin:/bin:/usr/local/bin'}, check=True)
    assert json.loads(out.stdout) == {'day': 'Sunday, Nov 8', 'refresh': 'Refreshes tomorrow'}
