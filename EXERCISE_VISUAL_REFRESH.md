# Exercise Visual Refresh

Started: July 2026
Status: Active
Progress: 52 / 90 visuals complete

## Overview

Replacing StreakFit's original monochrome stick-figure exercise illustrations with warm "Rickie-style" flat-color illustrations that match the app's raccoon mascot brand. Work proceeds tier by tier (Beginner → Intermediate → Advanced), in small approved batches: implement, verify locally (direct SVG + live Exercise Tips modal), commit, deploy, verify in production.

## Current Progress

| Tier         | Completed | Total  |
| ------------ | --------- | ------ |
| Beginner     | 30        | 30     |
| Intermediate | 22        | 30     |
| Advanced     | 0         | 30     |
| **Overall**  | **52**    | **90** |

## Beginner Completion Summary (30 / 30)

Completed across five phases:

* P1 — Reuse (8)
* P2 — Light adapts (3)
* P3 — Moderate adapts (11)
* P4 — New simple illustrations (4)
* P5 — New moderate illustrations (4)

All Beginner Exercise Tips visuals are Rickie-style, verified in production.

## Intermediate Completion Summary (22 / 30)

**P1 (4)**

* walking_lunge
* plank
* side_plank
* mountain_climber

**P2 (5)**

* jump_squat
* sumo_squat
* high_knees
* push_up
* single_leg_glute_bridge

**P3 (5)**

* diamond_push_up
* wide_push_up
* decline_push_up
* speed_squat
* straight_leg_raise

**P4 (4)**

* bicycle_crunch
* deep_squat_hold
* bodyweight_good_morning
* sphinx_push_up

**P5 (4)**

* spinal_twist
* russian_twist
* doorway_pec_stretch
* pigeon_pose

## Quality Improvements

### bicycle_crunch

* Replaced subtle twist cue with a bold arrow treatment
* Confirmed readable at real 80px inline scale

### bodyweight_good_morning

* Arm repositioned upward
* Matches coaching copy: "hands lightly behind your head"
* Hip hinge remains visually clear

Both fixes verified on production.

## Verification Standard

Each batch follows the same pipeline:

1. SVG inspection
2. Local Exercise Tips modal review
3. Production deployment
4. Service worker cache bump
5. Live modal validation
6. 80px inline readability check
7. Console inspection
8. Regression pass

Zero regressions have been introduced through all visual batches.

## Remaining Intermediate Exercises (8)

Require genuinely new pose work rather than adaptation:

* pike_push_up
* downdog_calf_stretch
* lateral_lunge
* skater_jump
* worlds_greatest_stretch
* no_jump_burpee
* plank_to_downdog
* hollow_body_hold *(depending on final adaptation decision)*

These represent the transition from **asset adaptation** to **new illustration design**.

## Current Milestone

**52 / 90 Rickie-style visuals complete and live**

* Beginner: **100% complete**
* Intermediate: **73% complete**
* Advanced: **0% complete**

Intermediate adaptation methodology has been proven successful at scale. Remaining Intermediate work is primarily original pose creation rather than reuse. Advanced tier (30 exercises) has not yet been started.

## Next Steps

* Decide whether `hollow_body_hold` can still be adapted (from `superman`, inverted/mirrored) or needs original pose work
* Plan and approve a batch approach for the remaining new-pose Intermediate exercises
* Once Intermediate reaches 30/30, produce an Intermediate Visual Completion report and a rollout recommendation for Advanced
