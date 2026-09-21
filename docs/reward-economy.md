# The reward economy

What a StreakFit action pays, and why it pays that. Every number here was
chosen against a constraint, not picked because it looked round — if you change
one, check it still satisfies the constraints in *Sizing a repeat completion*
below.

## The ladder

| Event | XP | Acorns | When |
|---|---|---|---|
| `brain_boost_attempt` | 3 | 0 | any Brain Boost answer |
| `repeat_exercise` | **5** | 0 | completing an exercise you have done before |
| `brain_boost_correct` | 10 | 1 | on top of the attempt, if right |
| `perfect_mission` | 15 | 2 | at 5/5 |
| `new_exercise` | 20 | 5 | first time ever completing that exercise key |
| `mission_complete` | 25 | 3 | at 5/5 |

`mission_complete` and `perfect_mission` always fire together — there is only
one way to reach 5/5 — so finishing the day is effectively a single **40 XP /
5 acorn** beat.

Levels are never stored. `xp_to_level()` derives them from lifetime XP with
`threshold(n) = 25(n-1)² + 75(n-1)`, so each level costs `50n + 50` more XP
than the last (L1→L2 = 100, L2→L3 = 150, L7→L8 = 400, L15→L16 = 800). The
curve keeps stretching forever, which is what stops any per-day income from
eventually trivialising it.

## Sizing a repeat completion

Until 2026-09-18 a repeat completion paid **nothing**. `new_exercise` pays once
ever and the mission bonuses only land on the 5th, so from day 2 a returning
user earned 0 XP on four of their five taps. That punished exactly the behaviour
the product is trying to build — coming back and moving again.

The measured shape of the problem, simulated over 365 days with the real
`get_daily_exercises()` selection across 40 users (beginner, full mission plus a
correct Brain Boost):

| repeat XP | day 1 | day 2 | day 7 | day 14 | day 30 | steady |
|---|---|---|---|---|---|---|
| 0 (old) | 153 | 132 | 80 | 62 | 54 | **53** |
| 3 | 153 | 135 | 91 | 76 | 68 | 68 |
| **5** | 153 | 137 | 98 | 85 | 78 | **78** |
| 8 | 153 | 140 | 109 | 98 | 93 | 93 |
| 10 | 153 | 142 | 116 | 108 | 103 | 103 |

Discovery decays fast and is the reason for the cliff: a user sees ~3.9 new
exercises on day 2, 1.5 by day 7, 0.4 by day 14 and essentially none after day
30 (30 exercises per skill level, one per category per day).

**5 XP** is the answer three independent constraints agree on:

1. **Finishing must stay the biggest beat of the day.** Five repeat taps must
   total less than the 40 XP mission bonus, so `5 × repeat < 40` ⟹ repeat < 8.
   At 8 they tie; at 10 the taps outweigh completing the mission, which would
   invert the product's central message.
2. **Discovery must stay an event.** At 5, a new exercise is worth **4×** a
   repeat. At 10 it is only 2× and finding something new stops feeling special.
3. **The bar has to visibly move.** At 3 XP a tap is ~1% of a mid-game level.
   At 5 it is a quarter of a discovery and half a correct Brain Boost — it
   slots cleanly into the existing 3 < 5 < 10 < 15 < 20 < 25 ladder.

It removes the cliff without flattening the curve: day 1 still pays 153 against
a steady 78, so a first session is still visibly special, and the long game
stays paced (L20 at ~day 129 rather than ~day 187 — faster, still months).

**Why no acorns.** Acorns currently have **no sink** — nothing in the app spends
them. Paying them for the most frequent action in the product would inflate a
currency that may later get a use, so acorns stay attached to notable events
(discovery, finishing, a correct answer). If a sink is ever added, revisit this
line first.

## Things deliberately not done

- **No streak multiplier.** Scaling rewards by streak length means a broken
  streak silently cuts income, which is a punishment for the person who came
  back — the one thing the design rules forbid.
- **No escalating within-day curve** (1st tap 3, 5th tap 7). More machinery than
  the problem needs, and it makes the toast numbers hard to predict.
- **`FAMILY_SESSION_XP = 30`** is defined and never awarded by any route. It is
  dead until a family-session feature exists.
