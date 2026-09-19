#!/usr/bin/env python3
"""Run StreakFit locally with an ENFORCED dollar ceiling on Anthropic spend.

`coach_eval.py --max-calls` bounds the number of REQUESTS. That is not the same
thing as bounding the MONEY: a matrix whose prompts grow, a tool loop that runs
three rounds instead of one, or a model swap to a pricier tier all move the bill
without moving the call count. This wrapper meters the actual `usage` every
reply carries and refuses to place the next call once a real dollar ceiling is
crossed — the coach then returns its normal, already-tested 503 path, so the
eval degrades exactly the way a missing key would rather than crashing.

It does not modify app.py. It wraps `app._anthropic_lib` in-process, the same
passthrough shim used for the first single-call verification.

    python scripts/eval_spend_guard.py --budget 3.00 --port 5000

Writes a running total to scripts/../eval_spend.json so the ceiling survives a
restart within one evaluation.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import threading
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# $ per 1M tokens (input, output). Keep in step with the model coach() uses.
PRICES = {
    "claude-sonnet-5": (2.00, 10.00),
    "claude-opus-4-8": (5.00, 25.00),
    "claude-opus-5": (5.00, 25.00),
    "claude-haiku-4-5": (1.00, 5.00),
}
DEFAULT_PRICE = (5.00, 25.00)  # price an unknown model at the dearest tier


class BudgetExceeded(RuntimeError):
    pass


class SpendMeter:
    def __init__(self, budget: float, state_path: Path):
        self.budget = budget
        self.state_path = state_path
        self.lock = threading.Lock()
        self.spent = 0.0
        self.calls = 0
        self.input_tokens = 0
        self.output_tokens = 0
        if state_path.exists():
            try:
                prior = json.loads(state_path.read_text())
                self.spent = float(prior.get("spent_usd", 0.0))
                self.calls = int(prior.get("calls", 0))
                self.input_tokens = int(prior.get("input_tokens", 0))
                self.output_tokens = int(prior.get("output_tokens", 0))
                print(f"[budget] resuming from {state_path.name}: "
                      f"${self.spent:.4f} already spent over {self.calls} calls")
            except Exception:
                pass

    def check(self) -> None:
        """Refuse the NEXT call once the ceiling is crossed."""
        with self.lock:
            if self.spent >= self.budget:
                raise BudgetExceeded(
                    f"spend ceiling reached: ${self.spent:.4f} of ${self.budget:.2f}"
                )

    def record(self, model: str, usage) -> None:
        pin, pout = PRICES.get(model, DEFAULT_PRICE)
        cost = (usage.input_tokens / 1e6 * pin) + (usage.output_tokens / 1e6 * pout)
        with self.lock:
            self.calls += 1
            self.input_tokens += usage.input_tokens
            self.output_tokens += usage.output_tokens
            self.spent += cost
            self.state_path.write_text(json.dumps({
                "budget_usd": self.budget,
                "spent_usd": round(self.spent, 6),
                "calls": self.calls,
                "input_tokens": self.input_tokens,
                "output_tokens": self.output_tokens,
            }, indent=2))
            pct = 100 * self.spent / self.budget if self.budget else 0
            print(f"[budget] call {self.calls}: {model} "
                  f"{usage.input_tokens}in/{usage.output_tokens}out "
                  f"= ${cost:.5f} | total ${self.spent:.4f} "
                  f"of ${self.budget:.2f} ({pct:.1f}%)", flush=True)


def install(meter: SpendMeter):
    import app as appmod
    real_lib = appmod._anthropic_lib

    class _Messages:
        def __init__(self, real):
            self._real = real

        def create(self, **kwargs):
            meter.check()
            resp = self._real.messages.create(**kwargs)
            meter.record(getattr(resp, "model", kwargs.get("model", "?")), resp.usage)
            return resp

    class _Client:
        def __init__(self, real):
            self.messages = _Messages(real)

    class _LibShim:
        @staticmethod
        def Anthropic(**kwargs):
            return _Client(real_lib.Anthropic(**kwargs))

    appmod._anthropic_lib = _LibShim
    return appmod


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--budget", type=float, required=True,
                    help="hard ceiling in USD. The next call is refused once crossed.")
    ap.add_argument("--port", type=int, default=5000)
    ap.add_argument("--state", default=str(ROOT / "eval_spend.json"))
    ap.add_argument("--reset", action="store_true", help="zero the running total first")
    args = ap.parse_args()

    state = Path(args.state)
    if args.reset and state.exists():
        state.unlink()

    env_file = ROOT / ".env"
    if env_file.exists():
        for line in env_file.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip())

    if not os.environ.get("ANTHROPIC_API_KEY", "").strip():
        print("ANTHROPIC_API_KEY is not set (checked the environment and .env).")
        return 2

    meter = SpendMeter(args.budget, state)
    appmod = install(meter)
    with appmod.app.app_context():
        appmod.db.create_all()

    print(f"[budget] ENFORCED CEILING ${args.budget:.2f} — "
          f"the coach returns its normal 503 past this point")
    print(f"[budget] serving on http://localhost:{args.port}", flush=True)
    appmod.app.run(port=args.port, debug=False, use_reloader=False, threaded=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
