#!/usr/bin/env python3
"""What one Ask Rickie reply actually costs, and what drives it.

MAKES NO API CALLS. Every number here is either read out of app.py or taken
from usage the provider actually reported during evaluations already run — the
files this repo wrote at `eval_spend.json`-style paths. Modelling a price on
estimates when measured usage exists is how an allowance ends up wrong in the
direction that costs money.

    python scripts/rickie_cost_audit.py
    python scripts/rickie_cost_audit.py --json      # machine-readable

The token→dollar step uses the Claude Sonnet 5 list price the coach runs on.
Change PRICE_IN/PRICE_OUT if the model or rate changes; everything downstream
is derived.
"""
from __future__ import annotations

import argparse
import ast
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# $/1M tokens for the model coach() uses (claude-sonnet-5).
PRICE_IN, PRICE_OUT = 2.00, 10.00

# Ground truth: usage the API reported during real runs this project made.
# (calls, input_tokens, output_tokens, what it was)
MEASURED = [
    (56, 211286, 5562, "56-prompt evaluation, prompt at 8.4k chars"),
    (19, 94586, 1956, "19-case retest, prompt at 12.7k chars"),
    (7, 31551, 624, "7-case retest, prompt at 11.3k chars"),
]


def _const(name):
    tree = ast.parse((ROOT / "app.py").read_text(encoding="utf-8"))
    for node in tree.body:
        if isinstance(node, ast.Assign):
            for t in node.targets:
                if isinstance(t, ast.Name) and t.id == name:
                    return ast.literal_eval(node.value)
    raise SystemExit(f"{name} not found in app.py")


def audit():
    prompt = _const("_COACH_SYSTEM_PROMPT")
    tool = _const("_WEATHER_TOOL")
    window = _const("_COACH_MEMORY_WINDOW")
    turn_len = _const("_COACH_TURN_PROMPT_LEN")
    max_out = 768  # coach() max_tokens

    # Calibrate chars->tokens against the measured runs rather than assuming 4.
    # The system prompt dominates input, so its char count and the reported
    # input tokens pin the ratio closely enough for planning.
    calib = []
    for calls, tin, _tout, label in MEASURED:
        calib.append((tin / calls, label))
    per_call_in_measured = sum(t for t, _ in calib) / len(calib)

    # Composition of a single request, in characters.
    parts = {
        "system prompt (personality + features + rules)": len(prompt),
        "weather tool schema": len(str(tool)),
        "user context block (name, streak, level)": 400,
        "coach notes block (when present)": 300,
        f"conversation history ({window} turns x {turn_len} chars)": window * turn_len,
        "the user's own message (cap 500)": 500,
    }
    total_chars = sum(parts.values())

    out = {
        "price": {"input_per_mtok": PRICE_IN, "output_per_mtok": PRICE_OUT,
                  "model": "claude-sonnet-5"},
        "composition_chars": parts,
        "composition_total_chars": total_chars,
        "measured": [],
        "per_reply": {},
        "profiles": {},
    }

    print("RICKIE COST AUDIT — no API calls made\n")
    print("What goes into one request (characters):")
    for k, v in sorted(parts.items(), key=lambda kv: -kv[1]):
        print(f"  {v:7,d}  {v / total_chars:5.1%}  {k}")
    print(f"  {total_chars:7,d}  100.0%  TOTAL (full history worst case)\n")

    print("Measured usage from runs already performed:")
    for calls, tin, tout, label in MEASURED:
        cost = tin / 1e6 * PRICE_IN + tout / 1e6 * PRICE_OUT
        print(f"  {calls:3d} calls  {tin / calls:6.0f} in / {tout / calls:4.0f} out per call"
              f"  ${cost / calls:.5f}/reply   {label}")
        out["measured"].append({"calls": calls, "input_per_call": round(tin / calls),
                                "output_per_call": round(tout / calls),
                                "cost_per_reply": round(cost / calls, 6), "label": label})

    # A reply with a FULL 10-turn history costs more than the averages above,
    # because most eval prompts were single-turn. Scale by the history share.
    hist_chars = window * turn_len
    empty_chars = total_chars - hist_chars
    full_in = per_call_in_measured * (total_chars / empty_chars)
    cheap = per_call_in_measured / 1e6 * PRICE_IN + 110 / 1e6 * PRICE_OUT
    dear = full_in / 1e6 * PRICE_IN + max_out / 1e6 * PRICE_OUT
    typical = (cheap + dear) / 2

    print("\nPer reply:")
    print(f"  typical (short history, ~110 output tokens) : ${cheap:.5f}")
    print(f"  worst   (full {window}-turn history, {max_out} output cap) : ${dear:.5f}")
    print(f"  planning figure (midpoint)                  : ${typical:.5f}")
    out["per_reply"] = {"typical": round(cheap, 6), "worst": round(dear, 6),
                        "planning": round(typical, 6)}

    print("\nMonthly cost per user, at the planning figure:")
    profiles = {"light (5 replies)": 5, "typical (30)": 30, "engaged (100)": 100,
                "heavy (300)": 300, "extreme (1000)": 1000,
                "rate-limit ceiling (10/day = 310)": 310}
    for label, n in profiles.items():
        print(f"  {label:36s} ${n * typical:7.3f}")
        out["profiles"][label] = round(n * typical, 4)

    print("\nAgainst revenue:")
    plus, sponsored = 4.99, 0.99
    for label, price in (("Plus $4.99", plus), ("Sponsored $0.99", sponsored)):
        budget20 = price * 0.20
        print(f"  {label:18s} 20% of revenue = ${budget20:.3f}/mo"
              f"  -> {int(budget20 / typical):4d} replies")
    print(f"\n  A sponsor at the cap: 1 x $4.99 + 5 x $0.99 = "
          f"${plus + 5 * sponsored:.2f}/mo for 6 accounts")

    # ── The lever worth knowing about before setting any price ─────────────
    #
    # 63% of every request is the system prompt, and it is byte-identical on
    # every call. Prompt caching prices a cache READ at roughly a tenth of
    # input, so caching the stable prefix is the one change that moves cost by
    # a large multiple rather than a few percent.
    #
    # The catch, stated plainly because it decides whether this is worth doing:
    # a cache entry lives ~5 minutes by default (1 hour on the paid tier), and
    # a WRITE costs ~1.25x input. At StreakFit's current traffic most requests
    # would arrive with the cache cold, pay the write premium, and cost MORE.
    # Caching pays only once requests arrive closer together than the TTL.
    stable_chars = len(prompt) + len(str(tool))
    chars_per_token = total_chars / (per_call_in_measured * (total_chars / empty_chars))
    stable_tokens = stable_chars / chars_per_token
    volatile_tokens = max(per_call_in_measured - stable_tokens, 0)
    cached = (volatile_tokens / 1e6 * PRICE_IN
              + stable_tokens / 1e6 * PRICE_IN * 0.10
              + 110 / 1e6 * PRICE_OUT)
    cold = (volatile_tokens / 1e6 * PRICE_IN
            + stable_tokens / 1e6 * PRICE_IN * 1.25
            + 110 / 1e6 * PRICE_OUT)
    print("\nPrompt caching (the stable prefix is "
          f"{stable_chars:,} chars ~ {stable_tokens:,.0f} tokens):")
    print(f"  cache HIT  : ${cached:.5f}/reply  ({(1 - cached / cheap):.0%} cheaper)")
    print(f"  cache MISS : ${cold:.5f}/reply  ({(cold / cheap - 1):+.0%} vs today)")
    print(f"  breakeven  : hits must exceed "
          f"{(cold - cheap) / (cold - cached):.0%} of requests")
    print("  NOT free: at low traffic most requests miss and pay the write premium.")
    out["caching"] = {"stable_tokens": round(stable_tokens),
                      "hit_cost": round(cached, 6), "miss_cost": round(cold, 6),
                      "breakeven_hit_rate": round((cold - cheap) / (cold - cached), 4)}

    print("\nPrompt growth is a cost decision:")
    print("  8.4k chars -> $0.00854/reply   (56-prompt run)")
    print(" 12.7k chars -> $0.01099/reply   (+29% per reply, same model)")
    print(f" {len(prompt) / 1000:.1f}k chars -> today. Every rule added to Rickie is")
    print("  charged on every reply every user ever sends.")
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()
    data = audit()
    if a.json:
        print("\n" + json.dumps(data, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
