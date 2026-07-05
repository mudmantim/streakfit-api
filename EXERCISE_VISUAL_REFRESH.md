# Exercise Visual Refresh

Started: July 2026
Status: Active
Progress: 75 / 90 visuals complete
Milestone: Advanced P1-P3 complete.

## Overview

Replacing StreakFit's original monochrome stick-figure exercise illustrations with warm "Rickie-style" flat-color illustrations that match the app's raccoon mascot brand. Work proceeds tier by tier (Beginner → Intermediate → Advanced), in small approved batches: implement, verify locally (direct SVG + live Exercise Tips modal), commit, deploy, verify in production.

## Current Progress

| Tier         | Completed | Total  |
| ------------ | --------- | ------ |
| Beginner     | 30        | 30     |
| Intermediate | 30        | 30     |
| Advanced     | 15        | 30     |
| **Overall**  | **75**    | **90** |

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

## Advanced Completion Summary (15 / 30)

**P1 (5)** — near-exact reuse and single-edit adaptations of existing Beginner/Intermediate assets:

* hollow_body_rock — `hollow_body_hold` unchanged, bold double-headed rocking arrow added beneath the torso
* tabata_mountain_climber — `mountain_climber`'s motion arrow emboldened (solid instead of thin dashed) plus small fast-pace tick marks
* shoulder_cars — `arm_circles` adapted so one arm rests at the side while the other traces a full circle, matching the coaching copy
* cossack_squat — `lateral_lunge`'s hip dropped and the bent leg's angle sharpened for a deeper squat, torso/arms/head shifted down to match
* full_burpee — `no_jump_burpee`'s hip-drive cue extended into a taller, bolder explosive arrow plus a faint ghost circle above suggesting the jump apex

**P2 (5)** — same methodology, first two coaching-copy catches:

* tuck_to_straight_leg_raise — `straight_leg_raise`'s leg replaced with a tucked position, bold vertical cycle arrow; one arm repositioned to reach overhead along the floor
* pike_walk_out — `downdog_calf_stretch`'s inverted-V reused unchanged, heel-press cue replaced with small bold step-tick marks
* single_leg_good_morning — `bodyweight_good_morning`'s second planted leg replaced with a straight leg lifted behind the body
* front_split_prep — `worlds_greatest_stretch`'s deep lunge legs extended further, the one-arm-up/one-arm-down reach replaced with both hands reaching down near the floor
* planche_lean — `plank`'s shoulder shifted forward and up past the fixed hand position for a noticeably steeper forward lean

**P3 (5)** — the push family, two more readability catches:

* pseudo_planche_push_up — `planche_lean`'s forward lean preserved, elbow bend exaggerated outward for a real push-up-style bend
* archer_push_up — `push_up`'s bent support arm kept, a second fully straight arm added reaching wide to the side
* assisted_one_arm_push_up — `push_up`'s bent arm kept dominant, a short secondary arm added resting on a small "book" prop
* typewriter_push_up — `wide_push_up`'s shoulder shifted to one side with a faint ghost torso at the opposite position and a bold double-headed lateral arrow
* plyometric_push_up — `push_up` unchanged, faint ghost circle above the hand plus a bold explosive upward arrow

## Coaching-Copy and Readability Catches

Every batch since Intermediate P4 has caught at least one real issue before it shipped — this has become a reliable part of the process, not an exception:

* **bicycle_crunch** (Intermediate P4) — subtle twist cue replaced with a bold arrow treatment
* **bodyweight_good_morning** (Intermediate P4) — arm repositioned upward to match "hands lightly behind your head"
* **russian_twist** (Intermediate P5, caught in production verification) — bold twist arrow was still unreadable at 80px in its original crowded position; repositioned to open space
* **shoulder_cars** (Advanced P1) — initial symmetric both-arms-circling version didn't match "hold one arm firmly against your side"; redesigned to one still arm + one circling arm
* **tuck_to_straight_leg_raise** (Advanced P2) — arm didn't match "arms stretched overhead, pressing into the floor"; repositioned overhead
* **front_split_prep** (Advanced P2) — support arm reached only to the thigh, not "hands on the floor for support"; extended to the floor
* **assisted_one_arm_push_up** (Advanced P3) — without a visual prop, the assisting arm was indistinguishable from a normal push-up at 80px; added a small "book" prop matching the coaching copy's own example
* **typewriter_push_up** (Advanced P3) — initial shoulder shift and lateral arrow were too subtle, read as a plain wide push-up at 80px; widened the shoulder offset and emboldened the arrow

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

**Advanced P1-P3 complete. 75 / 90 Rickie-style visuals complete and live.**

* Beginner: **100% complete**
* Intermediate: **100% complete**
* Advanced: **50% complete**

The Advanced tier has proven that the adaptation methodology developed in Beginner/Intermediate carries over cleanly — every P1-P3 exercise reused or adapted an existing asset, with zero brand-new silhouette families required so far. The remaining 15 Advanced exercises include the genuinely novel poses (wall handstand, L-sit, straddle V-up) that will need original design work.

## Next Steps

* Advanced P4 (planning candidate): tuck_jump, sprint_intervals, broad_jump, assisted_pistol_squat, bodyweight_jefferson_curl — highest remaining adaptation potential
* Advanced P5 (planning candidate): wall_handstand_hold, l_sit_hold, straddle_v_up, pancake_stretch, wrist_prep — genuinely new pose work
* Advanced P6 (planning candidate): shuttle_run, broad_jump_consecutive, tuck_jump_burpee — blocked on other Advanced exercises existing first
* Do not implement any further batch without explicit approval
