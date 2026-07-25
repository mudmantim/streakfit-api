"""In-process weather cache: geocode (~permanent) + forecast (10 min).

The caches cut StreakFit's own outbound provider calls. These tests count real
outbound calls by monkeypatching _http_get_json, and reset caches via the autouse
fixture in conftest. They assert the exact provider-call behavior the cache is
supposed to produce — and that a provider failure still degrades safely.
"""
import app as appmod


def _install_counting_http(monkeypatch, fail=False, city_coords=None):
    """Replace _http_get_json with a counter + canned geocode/forecast responses.
    Returns a dict tracking geocode/forecast call counts."""
    calls = {"geocode": 0, "forecast": 0}
    coords = city_coords or {}

    def fake(url, timeout=6):
        if fail:
            raise OSError("provider unreachable")
        if "geocoding-api" in url:
            calls["geocode"] += 1
            # infer which city from the query for the multi-city test
            name, lat, lon = "Denver", 39.7392, -104.9847
            for cname, (cl, co) in coords.items():
                if cname.lower() in url.lower():
                    name, lat, lon = cname, cl, co
                    break
            return {"results": [{"name": name, "admin1": "", "country": "",
                                 "latitude": lat, "longitude": lon}]}
        calls["forecast"] += 1
        return {"current": {"temperature_2m": 70.0, "weather_code": 3}}

    monkeypatch.setattr(appmod, "_http_get_json", fake)
    return calls


def test_repeated_city_uses_cached_geocode(monkeypatch):
    calls = _install_counting_http(monkeypatch)
    c1, e1 = appmod._weather_tool_result("Denver")
    c2, e2 = appmod._weather_tool_result("Denver")
    assert not e1 and not e2
    assert calls["geocode"] == 1, "second Denver lookup must reuse the cached geocode"


def test_repeated_forecast_within_ttl_avoids_provider_call(monkeypatch):
    calls = _install_counting_http(monkeypatch)
    appmod._weather_tool_result("Denver")   # geocode #1 + forecast #1
    appmod._weather_tool_result("Denver")   # both served from cache
    assert calls["geocode"] == 1 and calls["forecast"] == 1, \
        "within the forecast TTL, no second provider call should be made"


def test_expired_forecast_entry_refreshes(monkeypatch):
    calls = _install_counting_http(monkeypatch)
    appmod._weather_tool_result("Denver")   # forecast #1 cached
    # force-expire the forecast entry (geocode stays valid)
    for k, (v, _exp) in list(appmod._FORECAST_CACHE.items()):
        appmod._FORECAST_CACHE[k] = (v, appmod.datetime.utcnow() - appmod.timedelta(seconds=1))
    appmod._weather_tool_result("Denver")   # forecast refetched
    assert calls["forecast"] == 2, "an expired forecast must trigger a fresh provider call"
    assert calls["geocode"] == 1, "geocode should still be cached across the refresh"


def test_different_cities_do_not_share_cache(monkeypatch):
    calls = _install_counting_http(monkeypatch, city_coords={
        "Denver": (39.7392, -104.9847),
        "Seattle": (47.6062, -122.3321),
    })
    d, ed = appmod._weather_tool_result("Denver")
    s, es = appmod._weather_tool_result("Seattle")
    assert not ed and not es
    assert "Denver" in d and "Seattle" in s
    assert "denver" in appmod._GEOCODE_CACHE and "seattle" in appmod._GEOCODE_CACHE
    assert len(appmod._FORECAST_CACHE) == 2, "each city's coords get their own forecast entry"
    assert calls["geocode"] == 2, "two distinct cities require two geocodes"


def test_provider_failure_still_degrades_safely(monkeypatch):
    _install_counting_http(monkeypatch, fail=True)
    content, is_err = appmod._weather_tool_result("Denver")
    assert is_err is True
    assert "couldn't reach the weather" in content.lower()
    # a failed lookup caches nothing
    assert not appmod._GEOCODE_CACHE and not appmod._FORECAST_CACHE
