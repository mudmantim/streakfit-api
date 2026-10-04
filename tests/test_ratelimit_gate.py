"""The rate-limit gate's state machine, driven directly with a stub backend
and a fake clock (D7 round 2). The black-box behaviour is pinned in
test_ratelimit_outage_policy.py; this pins the transitions themselves:

  HEALTHY  --any backend exception-->  DEGRADED            (immediately)
  DEGRADED --probe, spaced >= PROBE_INTERVAL_S, single-flight-->
           success: successes += 1;  QUALIFY_SUCCESSES in a row -> HEALTHY
           failure: successes = 0
"""
import threading
import time

import pytest
import redis

import app as appmod
from app import _LocalWindows, _RateLimitGate

INTERVAL = _RateLimitGate.PROBE_INTERVAL_S
QUALIFY = _RateLimitGate.QUALIFY_SUCCESSES


class StubRedis:
    def __init__(self):
        self.data = {}
        self.fail = None
        self.calls = []
        self.gate_event = None       # set to an Event to make calls block

    def _io(self, op):
        self.calls.append(op)
        if self.gate_event is not None:
            self.gate_event.wait(5)
        if self.fail is not None:
            raise self.fail

    def incr(self, key, expiry, amount=1):
        self._io(("incr", key, amount))
        self.data[key] = self.data.get(key, 0) + amount
        return self.data[key]

    def get(self, key):
        self._io(("get", key))
        return self.data.get(key, 0)

    def get_expiry(self, key):
        self._io(("get_expiry", key))
        return time.time() + 30

    def clear(self, key):
        self._io(("clear", key))
        self.data.pop(key, None)

    def reset(self):
        self._io(("reset",))
        self.data.clear()


class Clock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


@pytest.fixture
def gate():
    g = _RateLimitGate("streakfit+redis://127.0.0.1:1",
                       **appmod._limiter_storage_options("redis://127.0.0.1:1"))
    g.redis = StubRedis()
    g.clock = Clock()
    return g


def _requests(g):
    return [c for c in g.redis.calls if c[1:2] != (appmod._RATELIMIT_PROBE_KEY,)]


def _probes(g):
    return [c for c in g.redis.calls if c[1:2] == (appmod._RATELIMIT_PROBE_KEY,)]


def _degrade(g):
    g.redis.fail = redis.ConnectionError("down")
    g.incr("trigger", 60)
    assert g.degraded


def _probe_success_at(g, t):
    g.clock.t = t
    g.incr("drive", 60)


# ── Healthy ─────────────────────────────────────────────────────────────────

def test_healthy_counts_in_the_backend_and_the_mirror(gate):
    assert [gate.incr("k", 60) for _ in range(3)] == [1, 2, 3]
    assert gate.redis.data["k"] == 3 and gate.local.get("k") == 3
    assert gate.get("k") == 3 and not gate.degraded and gate.check()


def test_healthy_answer_is_the_larger_of_the_two_counts(gate):
    gate.redis.data["k"] = 7          # hits from another process, or before a restart
    assert gate.incr("k", 60) == 8
    assert gate.get("k") == 8


def test_a_backend_that_lost_counts_is_brought_back_up_to_the_true_count(gate):
    for _ in range(4):
        gate.incr("k", 60)
    gate.redis.data.clear()           # a restarted Key Value instance
    assert gate.incr("k", 60) == 5
    assert gate.redis.data["k"] == 5


# ── HEALTHY -> DEGRADED ─────────────────────────────────────────────────────

@pytest.mark.parametrize("exc", [
    redis.ConnectionError("refused"), redis.TimeoutError("timed out"),
    redis.exceptions.ReadOnlyError("READONLY"), redis.ResponseError("OOM"),
    OSError("no route"), ValueError("garbage reply"), RuntimeError("anything"),
])
def test_the_first_failure_of_any_kind_degrades_and_never_raises(gate, exc):
    gate.incr("k", 60)
    gate.incr("k", 60)
    gate.redis.fail = exc
    assert gate.incr("k", 60) == 3            # carries on from the true count
    assert gate.degraded and not gate.check()
    assert gate.last_failure == type(exc).__name__
    assert gate.degrade_count == 1


@pytest.mark.parametrize("op", ["get", "get_expiry"])
def test_a_failing_read_degrades_too(gate, op):
    gate.incr("k", 60)
    gate.redis.fail = redis.TimeoutError("slow")
    result = getattr(gate, op)("k")
    assert gate.degraded
    if op == "get":
        assert result == 1


@pytest.mark.parametrize("exc", [SystemExit(1), KeyboardInterrupt()])
def test_a_worker_abort_is_never_swallowed(gate, exc):
    """gunicorn aborts a stuck worker with SystemExit; limits' own check()
    swallowed it with a bare except (D7 round 1)."""
    gate.redis.fail = exc
    with pytest.raises(type(exc)):
        gate.incr("k", 60)


# ── DEGRADED ────────────────────────────────────────────────────────────────

def test_degraded_hits_count_in_memory_and_leave_the_backend_alone(gate):
    _degrade(gate)
    calls = len(gate.redis.calls)
    gate.clock.t += INTERVAL - 0.01
    for _ in range(50):
        gate.incr("k", 60)
        gate.get("k")
        gate.get_expiry("k")
    assert len(gate.redis.calls) == calls
    assert gate.get("k") == 50


def test_one_probe_per_interval_however_much_traffic(gate):
    _degrade(gate)
    start = gate.clock.t
    for step in range(int(4 * INTERVAL * 10)):     # 4 intervals in 0.1 s steps
        gate.clock.t = start + step / 10
        gate.incr("k", 60)
    assert len(_probes(gate)) == 3, gate.redis.calls  # at +5, +10, +15
    assert _requests(gate) == [("incr", "trigger", 1)]


def test_recovery_needs_qualify_consecutive_successes(gate):
    _degrade(gate)
    gate.redis.fail = None
    t0 = gate.clock.t
    for i in range(1, QUALIFY):
        _probe_success_at(gate, t0 + i * INTERVAL)
        assert gate.degraded and gate.successes == i
    _probe_success_at(gate, t0 + QUALIFY * INTERVAL)
    assert not gate.degraded and gate.recover_count == 1


def test_a_failure_during_qualification_starts_it_again(gate):
    _degrade(gate)
    t0 = gate.clock.t
    gate.redis.fail = None
    _probe_success_at(gate, t0 + INTERVAL)
    _probe_success_at(gate, t0 + 2 * INTERVAL)
    assert gate.successes == 2
    gate.redis.fail = redis.ConnectionError("flap")
    _probe_success_at(gate, t0 + 3 * INTERVAL)
    assert gate.degraded and gate.successes == 0
    gate.redis.fail = None
    for i in range(4, 4 + QUALIFY - 1):
        _probe_success_at(gate, t0 + i * INTERVAL)
        assert gate.degraded
    _probe_success_at(gate, t0 + (4 + QUALIFY - 1) * INTERVAL)
    assert not gate.degraded


def test_recovery_cannot_be_rushed_by_traffic(gate):
    """The probes behind a recovery are at least PROBE_INTERVAL_S apart, so it
    takes (QUALIFY_SUCCESSES - 1) intervals of a working backend at the very
    least, and the first is an interval after the last failure."""
    _degrade(gate)
    failed_at = gate.clock.t
    gate.redis.fail = None
    while gate.degraded:
        gate.clock.t += 0.05
        gate.incr("k", 60)
    assert gate.clock.t - failed_at >= QUALIFY * INTERVAL - 1e-9
    assert len(_probes(gate)) == QUALIFY


def test_requests_after_recovery_answer_with_counts_from_the_outage(gate):
    for _ in range(2):
        gate.incr("k", 60)                     # 2 in the backend and mirror
    _degrade(gate)
    for _ in range(3):
        gate.incr("k", 60)                     # 3 more, mirror only
    assert gate.redis.data["k"] == 2
    gate.redis.fail = None
    t0 = gate.clock.t
    for i in range(1, QUALIFY + 1):
        gate.clock.t = t0 + i * INTERVAL
        gate.get("other")                      # probes without touching k
    assert not gate.degraded
    assert gate.get("k") == 5                  # not 2
    assert gate.incr("k", 60) == 6
    assert gate.redis.data["k"] == 6           # the backend caught up


def test_probes_are_single_flight(gate):
    _degrade(gate)
    gate.redis.fail = None
    gate.clock.t += INTERVAL
    blocker = threading.Event()
    gate.redis.gate_event = blocker
    inside = threading.Thread(target=gate.incr, args=("k", 60))
    inside.start()
    deadline = time.monotonic() + 2
    while not _probes(gate) and time.monotonic() < deadline:
        time.sleep(0.005)
    assert len(_probes(gate)) == 1
    # A second request while the probe is in flight: no second probe, no
    # backend I/O, answered from the mirror at once.
    t = time.monotonic()
    gate.redis.gate_event = None
    assert gate.incr("j", 60) == 1
    assert time.monotonic() - t < 0.5
    assert len(_probes(gate)) == 1
    blocker.set()
    inside.join(5)
    assert gate.successes == 1


def test_the_interval_is_claimed_before_the_probe_runs(gate):
    """A probe that takes its full timeout must not be followed straight away
    by another: the next slot is counted from the START of the probe."""
    _degrade(gate)
    t0 = gate.clock.t
    gate.clock.t = t0 + INTERVAL
    gate.redis.fail = redis.TimeoutError("slow")
    gate.incr("k", 60)
    assert gate.next_probe_at >= t0 + 2 * INTERVAL - 1e-9


# ── Self-check, clear and reset ─────────────────────────────────────────────

def test_the_self_check_probe_does_no_io_while_degraded(gate):
    _degrade(gate)
    calls = len(gate.redis.calls)
    gate.clock.t += 10 * INTERVAL
    assert gate.probe_now() is False
    assert len(gate.redis.calls) == calls
    assert "in-process authority" in gate.describe()


def test_the_self_check_probe_degrades_on_failure(gate):
    assert gate.probe_now() is True
    gate.redis.fail = redis.ConnectionError("down")
    assert gate.probe_now() is False and gate.degraded


def test_clear_and_reset_do_not_touch_a_degraded_backend(gate):
    gate.incr("k", 60)
    _degrade(gate)
    calls = len(gate.redis.calls)
    gate.clear("k")
    gate.reset()
    assert len(gate.redis.calls) == calls
    assert gate.local.get("k") == 0


def test_clear_and_reset_reach_a_healthy_backend(gate):
    gate.incr("k", 60)
    gate.clear("k")
    assert "k" not in gate.redis.data and gate.local.get("k") == 0
    gate.incr("k", 60)
    gate.reset()
    assert gate.redis.data == {} and gate.local.get("k") == 0


# ── The mirror ──────────────────────────────────────────────────────────────

def test_local_windows_are_fixed_windows_on_the_monotonic_clock():
    clock = Clock()
    w = _LocalWindows(clock=clock)
    assert w.incr("k", 60) == 1 and w.incr("k", 60) == 2
    clock.t += 59.9
    assert w.get("k") == 2
    clock.t += 0.2
    assert w.get("k") == 0
    assert w.incr("k", 60) == 1


def test_local_windows_sweep_expired_keys():
    clock = Clock()
    w = _LocalWindows(clock=clock)
    for i in range(500):
        w.incr(f"old{i}", 1)
    clock.t += 2
    for i in range(_LocalWindows.SWEEP_EVERY):
        w.incr(f"new{i % 10}", 60)
    assert len(w) == 10


def test_local_expiry_is_reported_on_the_wall_clock():
    clock = Clock()
    w = _LocalWindows(clock=clock)
    w.incr("k", 60)
    clock.t += 20
    assert abs(w.get_expiry("k") - (time.time() + 40)) < 1
    assert abs(w.get_expiry("absent") - time.time()) < 1


# ── Wiring ──────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("uri,gated", [
    ("redis://h:6379", True), ("rediss://h:6380/0", True),
    ("redis+unix:///tmp/r.sock", True), ("memory://", False),
    ("valkey://h:6379", False), ("redis+sentinel://h:26379/m", False),
])
def test_redis_uris_are_served_through_the_gate(uri, gated):
    wrapped = appmod._limiter_storage_uri(uri)
    assert wrapped.startswith("streakfit+") is gated
    assert wrapped.endswith(uri)


def test_the_gate_never_raises_a_storage_error_through_limits(gate):
    """limits wraps every Storage method to re-raise `base_exceptions`; the
    gate's is empty, so nothing it catches can escape that way."""
    assert gate.base_exceptions == ()


def test_outage_policy_self_check(client, monkeypatch):
    def policy():
        r = client.get("/api/verification/self")
        return {c["id"]: c for c in r.get_json()["checks"]}["ratelimit.outage_policy"]

    monkeypatch.setattr(appmod.limiter, "enabled", True)
    assert policy()["status"] == "PASS"              # memory:// in tests
    monkeypatch.setattr(appmod.limiter, "enabled", False)
    p = policy()
    assert p["status"] == "FAIL" and "switched off" in p["observed"]
