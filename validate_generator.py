#!/usr/bin/env python3
"""
Validate Daily 5 generator constraints across a large sample of missions.

Run from the project root:
    python3 validate_generator.py

Reports per skill level:
  - Missions violating the fun floor  (no fun_score='high' exercise)
  - Missions violating impact balance (all impact='none')
  - Missions violating the high-impact cap (>2 impact='high' exercises)
  - fun_score distribution
  - impact distribution
  - fallback rate (missions that exhausted all retries)
"""

import ast
import hashlib
import random
from collections import Counter, defaultdict

# ---------------------------------------------------------------------------
# Load EXERCISE_LIBRARY and generator constants directly from app.py so this
# script always tests the live implementation, not a copy.
# ---------------------------------------------------------------------------
with open('app.py') as f:
    _src = f.read()

_tree = ast.parse(_src)
EXERCISE_LIBRARY = None
_MAX_RETRIES = None

for node in ast.walk(_tree):
    if isinstance(node, ast.Assign):
        for t in node.targets:
            if isinstance(t, ast.Name) and t.id == 'EXERCISE_LIBRARY':
                EXERCISE_LIBRARY = ast.literal_eval(node.value)
            if isinstance(t, ast.Name) and t.id == '_GENERATOR_MAX_RETRIES':
                _MAX_RETRIES = ast.literal_eval(node.value)

assert EXERCISE_LIBRARY, "Could not parse EXERCISE_LIBRARY from app.py"
assert _MAX_RETRIES is not None, "Could not parse _GENERATOR_MAX_RETRIES from app.py"

_CATEGORIES = ('upper_body', 'lower_body', 'core', 'mobility', 'conditioning')


# ---------------------------------------------------------------------------
# Replicate the generator exactly as it appears in app.py.
# We also return a (result, used_fallback) tuple for fallback tracking.
# ---------------------------------------------------------------------------
def _generate(user_id, date_str, skill_level):
    if skill_level not in EXERCISE_LIBRARY:
        skill_level = 'beginner'
    seed = int(hashlib.sha256(
        f"{user_id}:{date_str}:{skill_level}".encode()
    ).hexdigest(), 16) % (2 ** 32)
    rng  = random.Random(seed)
    pool = EXERCISE_LIBRARY[skill_level]

    level_has_high_fun = any(
        ex['fun_score'] == 'high' for exs in pool.values() for ex in exs
    )

    candidate = None
    for attempt in range(_MAX_RETRIES):
        candidate = [rng.choice(pool[cat]) for cat in _CATEGORIES]
        impacts   = [ex['impact'] for ex in candidate]
        fun_ok    = any(ex['fun_score'] == 'high' for ex in candidate) or not level_has_high_fun
        impact_ok = any(i in ('low', 'high') for i in impacts)
        cap_ok    = impacts.count('high') <= 2
        if fun_ok and impact_ok and cap_ok:
            return candidate, False  # passed constraints, no fallback used

    return candidate, True  # exhausted retries — fallback


# ---------------------------------------------------------------------------
# Sample set: 20 users × 336 dates × 3 levels = 20,160 per level / 60,480 total
# ---------------------------------------------------------------------------
SKILL_LEVELS = ['beginner', 'intermediate', 'advanced']
USER_IDS     = list(range(1, 21))
DATE_RANGE   = [
    f'2026-{m:02d}-{d:02d}'
    for m in range(1, 13)
    for d in range(1, 29)
]

n_per_level = len(USER_IDS) * len(DATE_RANGE)
total       = n_per_level * len(SKILL_LEVELS)

no_high_fun_count  = defaultdict(int)
all_static_count   = defaultdict(int)
over_cap_count     = defaultdict(int)
fallback_count     = defaultdict(int)
fun_dist           = defaultdict(Counter)
impact_dist        = defaultdict(Counter)

for level in SKILL_LEVELS:
    pool = EXERCISE_LIBRARY[level]
    level_has_high_fun = any(
        ex['fun_score'] == 'high' for exs in pool.values() for ex in exs
    )
    for uid in USER_IDS:
        for date_str in DATE_RANGE:
            exs, used_fallback = _generate(uid, date_str, level)

            impacts    = [ex['impact'] for ex in exs]
            fun_scores = [ex['fun_score'] for ex in exs]

            if level_has_high_fun and not any(f == 'high' for f in fun_scores):
                no_high_fun_count[level] += 1
            if not any(i in ('low', 'high') for i in impacts):
                all_static_count[level] += 1
            if impacts.count('high') > 2:
                over_cap_count[level] += 1
            if used_fallback:
                fallback_count[level] += 1

            for f in fun_scores:
                fun_dist[level][f] += 1
            for i in impacts:
                impact_dist[level][i] += 1


# ---------------------------------------------------------------------------
# Report
# ---------------------------------------------------------------------------
print(f"\nDaily 5 Generator Validation")
print(f"MAX_RETRIES = {_MAX_RETRIES}")
print(f"Sample: {len(USER_IDS)} users × {len(DATE_RANGE)} dates × {len(SKILL_LEVELS)} levels")
print(f"Total missions: {total:,}  ({n_per_level:,} per level)\n")

PASS = "\033[32m✓\033[0m"
FAIL = "\033[31m✗\033[0m"

for level in SKILL_LEVELS:
    n  = n_per_level
    pct = lambda k: f"{100 * k / n:.3f}%"

    nhf  = no_high_fun_count[level]
    ast_ = all_static_count[level]
    oc   = over_cap_count[level]
    fb   = fallback_count[level]

    print(f"{'─' * 52}")
    print(f"  {level.upper()}")
    print(f"{'─' * 52}")
    print(f"  {PASS if nhf  == 0 else FAIL} Fun floor violations  (no high-fun):   {nhf:>6,}  ({pct(nhf)})")
    print(f"  {PASS if ast_ == 0 else FAIL} Impact balance misses (all-static):    {ast_:>6,}  ({pct(ast_)})")
    print(f"  {PASS if oc   == 0 else FAIL} High-impact cap misses (>2 high):       {oc:>6,}  ({pct(oc)})")
    print(f"  {'—'} Fallback used (retries exhausted):      {fb:>6,}  ({pct(fb)})")
    total_slots = n * 5
    fd = fun_dist[level]
    id_ = impact_dist[level]
    print(f"  fun_score  low={fd['low']:>6,} ({100*fd['low']/total_slots:.1f}%)  "
          f"medium={fd['medium']:>6,} ({100*fd['medium']/total_slots:.1f}%)  "
          f"high={fd['high']:>6,} ({100*fd['high']/total_slots:.1f}%)")
    print(f"  impact     none={id_['none']:>6,} ({100*id_['none']/total_slots:.1f}%)  "
          f"low={id_['low']:>6,} ({100*id_['low']/total_slots:.1f}%)  "
          f"high={id_['high']:>6,} ({100*id_['high']/total_slots:.1f}%)")
    print()

any_failure = any(
    no_high_fun_count[l] or all_static_count[l] or over_cap_count[l]
    for l in SKILL_LEVELS
)
if any_failure:
    print("RESULT: Some constraint violations remain (see above).\n")
else:
    print("RESULT: All constraints satisfied across all sampled missions.\n")
