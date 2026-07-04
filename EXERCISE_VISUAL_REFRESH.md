# Exercise Visual Refresh

Started: July 2026
Status: Active
Progress: 60 / 90 visuals complete
Milestone: Intermediate visual refresh complete.

## Overview

Replacing StreakFit's original monochrome stick-figure exercise illustrations with warm "Rickie-style" flat-color illustrations that match the app's raccoon mascot brand. Work proceeds tier by tier (Beginner → Intermediate → Advanced), in small approved batches: implement, verify locally (direct SVG + live Exercise Tips modal), commit, deploy, verify in production.

## Current Progress

| Tier         | Completed | Total  |
| ------------ | --------- | ------ |
| Beginner     | 30        | 30     |
| Intermediate | 30        | 30     |
| Advanced     | 0         | 30     |
| **Overall**  | **60**    | **90** |

## Beginner Completion Summary (30 / 30)

Completed across five phases:

* P1 — Reuse (8)
* P2 — Light adapts (3)
* P3 — Moderate adapts (11)
* P4 — New simple illustrations (4)
* P5 — New moderate illustrations (4)

All Beginner Exercise Tips visuals are Rickie-style, verified in production.

## Intermediate Completion Summary (30 / 30) — COMPLETE

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

**hollow_body_hold (1)** — adapted from `superman`, flipped from a prone back-extension arch to a supine "banana hold": both legs together and both arms together (instead of one-limb-extended/one-tucked), a much shallower curve, bold lift cues at both tips.

**P6 (3)** — first genuinely new pose in this project: an inverted-V silhouette (hips as the peak, straight line to a planted heel, straight line through the shoulder to a planted hand), designed fresh in `downdog_calf_stretch` and copied/adapted into the other two. No shared template files were added — each SVG still maps 1:1 to its `EXERCISE_LIBRARY` key.

* downdog_calf_stretch — base pose, bold downward heel-press cue
* pike_push_up — same base pose, bold press double-arrow near the arm
* plank_to_downdog — same base pose, faint ghost-plank silhouette + upward transition arrow

**P7 (4)** — final Intermediate batch, sideways-stance and transition-pose originals:

* lateral_lunge — new "sideways stance" base pose (one knee bent tracking over the foot, other leg straight and extended to the side, pelvis shifted)
* skater_jump — reuses `lateral_lunge`'s stance with the straight leg replaced by a lifted trailing leg crossing behind, plus a bold lateral motion arrow
* worlds_greatest_stretch — deep runner's lunge, one arm planted near the front foot, the other reaching straight up, torso rotated open; treated as a hero pose, no extra arrows needed
* no_jump_burpee — compressed to one recognizable midpoint posture (crouched, hands near the floor, hips elevated) rather than encoding all four burpee phases, with a bold vertical hip-drive cue

## Quality Improvements

### bicycle_crunch

* Replaced subtle twist cue with a bold arrow treatment
* Confirmed readable at real 80px inline scale

### bodyweight_good_morning

* Arm repositioned upward
* Matches coaching copy: "hands lightly behind your head"
* Hip hinge remains visually clear

### russian_twist (caught during P5 production verification)

* Initial bold twist-arrow reposition still wasn't visible at 80px because it sat in a crowded area near the torso/arms
* Repositioned to open space near the raised arm — now reads clearly at 80px

All three fixes verified on production.

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

## Current Milestone

**Intermediate visual refresh complete. 60 / 90 Rickie-style visuals complete and live.**

* Beginner: **100% complete**
* Intermediate: **100% complete**
* Advanced: **0% complete**

The inverted-V (P6) and sideways-stance/transition-pose (P7) batches proved that genuinely original poses can work within the Rickie system, not just adaptations of existing exercise art. That confidence now carries into Advanced, which is a fresh chapter rather than an extension of this one — none of its 30 exercises have adaptable Beginner/Intermediate bases the way most of Intermediate did.

## Next Steps

* Advanced tier (30 exercises) has not yet been started — needs its own audit: which exercises have any reusable base at all, and which are original-pose work from the start
* Plan and approve an Advanced P1 batch before implementing anything
