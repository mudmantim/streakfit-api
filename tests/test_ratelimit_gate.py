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
        self.ttls = {}

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

    def prefixed_key(self, key):
        return f"LIMITS:{key}"

    def get_connection(self, readonly=False):
        stub = self

        class Conn:
            def expire(self, name, seconds):
                stub._io(("expire", name, seconds))
                stub.ttls[name] = seconds
                return True
        return Conn()


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


def test_the_answer_never_drops_below_the_mirror_even_if_the_backend_resets_mid_hit(gate):
    """The catch-up write can land in a key that expired a moment earlier and
    so count less than the mirror; the mirror's count still wins."""
    class Forgetful(StubRedis):
        def incr(self, key, expiry, amount=1):
            self._io(("incr", key, amount))
            return amount                 # every write lands in a fresh window
    gate.redis = Forgetful()
    gate.incr("k", 60)
    gate.incr("k", 60)
    assert gate.incr("k", 60) == 3


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
    # Even once the NEXT interval is due, a probe still in flight blocks
    # another: a probe is bounded by the socket timeouts, but DNS is not.
    gate.clock.t += 2 * INTERVAL
    assert gate.incr("j", 60) == 2
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


def test_a_probe_that_overruns_its_bounds_spaces_the_next_one(gate):
    """DNS is not bounded by the socket timeouts: a probe stuck 8 s in the
    resolver must not be followed by another 5 s later (62% of the worker,
    measured by review), but ~72 s later (10%)."""
    _degrade(gate)
    gate.clock.t += INTERVAL

    class StuckResolver(StubRedis):
        def incr(self, key, expiry, amount=1):
            self._io(("incr", key, amount))
            gate.clock.t += 8
            raise redis.ConnectionError("Temporary failure in name resolution")
    gate.redis = StuckResolver()
    end_of_probe = gate.clock.t + 8
    gate.incr("k", 60)
    assert gate.next_probe_at >= end_of_probe + 9 * 8 - 1e-9
    held, span = 8, gate.next_probe_at - (end_of_probe - 8)
    assert held / span <= 0.11


def test_a_bounded_probe_keeps_the_ordinary_interval(gate):
    _degrade(gate)
    gate.clock.t += INTERVAL

    class Timeout(StubRedis):
        def incr(self, key, expiry, amount=1):
            self._io(("incr", key, amount))
            gate.clock.t += 0.5
            raise redis.TimeoutError("timed out")
    gate.redis = Timeout()
    start = gate.clock.t
    gate.incr("k", 60)
    assert abs(gate.next_probe_at - (start + 0.5 + INTERVAL)) < 1e-9


def test_catching_up_keeps_the_hits_in_their_own_window(gate):
    """Review m-1: the catch-up landed in a NEW Redis key whose TTL started at
    the push, blocking for up to one extra window (measured ~55 s on a 60 s
    limit, up to a day on 10/day). The key now expires with the mirror's
    window."""
    for _ in range(3):
        gate.incr("k", 60)
    gate.clock.t += 40                       # 20 s left in the mirror window
    gate.redis.data.clear()                  # Redis lost the key
    assert gate.incr("k", 60) == 4
    assert gate.redis.ttls["LIMITS:k"] in (20, 21)


def test_a_probe_that_began_before_a_failure_does_not_count(gate):
    """Review n-1 (needs a second thread): a success from a probe in flight
    while a request saw a failure must not advance qualification."""
    _degrade(gate)
    gate.redis.fail = None
    gate.clock.t += INTERVAL
    blocker = threading.Event()
    gate.redis.gate_event = blocker
    probe = threading.Thread(target=gate.incr, args=("k", 60))
    probe.start()
    deadline = time.monotonic() + 2
    while not _probes(gate) and time.monotonic() < deadline:
        time.sleep(0.005)
    gate._failed(redis.ConnectionError("seen elsewhere"))
    blocker.set()
    probe.join(5)
    assert gate.successes == 0 and gate.degraded


def test_a_successful_probe_also_waits_a_full_interval_from_its_end(gate):
    _degrade(gate)
    gate.redis.fail = None
    gate.clock.t += INTERVAL

    class Slowish(StubRedis):
        def incr(self, key, expiry, amount=1):
            r = super().incr(key, expiry, amount)
            gate.clock.t += 0.4
            return r
    gate.redis = Slowish()
    start = gate.clock.t
    gate.incr("k", 60)
    assert gate.successes == 1
    assert gate.next_probe_at >= start + 0.4 + INTERVAL - 1e-9


def test_the_mirror_windows_run_on_the_gates_clock(gate):
    """The mirror must not use the wall clock: a wall-clock step would end
    windows early. Driven through the gate's own (fake) clock."""
    _degrade(gate)
    gate.incr("k", 60)
    gate.clock.t += 59
    assert gate.get("k") == 1                # still live after 59 s
    gate.clock.t += 2
    assert gate.local.get("k") == 0


def test_a_write_that_does_not_count_fails_the_self_check_probe_and_degrades(gate):
    class Zero(StubRedis):
        def incr(self, key, expiry, amount=1):
            self._io(("incr", key, amount))
            return 0
    gate.redis = Zero()
    assert gate.probe_now() is False and gate.degraded


def test_flask_limiters_own_fallback_counts_as_degraded(client, monkeypatch):
    """Review m-2: if anything ever escaped the gate, Flask-Limiter would
    switch to a fallback that counts from zero. That must read as an outage
    (lookup refuses, login strict) and fail the self-check."""
    monkeypatch.setattr(appmod.limiter, "enabled", True)
    monkeypatch.setattr(appmod.limiter, "_storage_dead", True)
    # Flask-Limiter re-checks its storage at the start of a request (with a
    # backoff whose state earlier tests advance) and clears the flag when the
    # check passes; keep the storage failing so the state holds.
    monkeypatch.setattr(appmod.limiter.storage, "check", lambda: False)
    assert appmod._rate_limit_degraded() is True
    r = client.get("/api/verification/self")
    p = {c["id"]: c for c in r.get_json()["checks"]}["ratelimit.outage_policy"]
    assert p["status"] == "FAIL" and "from zero" in p["observed"]


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
    # The sweep ran part-way through; live windows kept every count.
    assert w.get("new0") == len(range(0, _LocalWindows.SWEEP_EVERY, 10))


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
