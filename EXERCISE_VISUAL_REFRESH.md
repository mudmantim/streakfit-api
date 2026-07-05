# Exercise Visual Refresh

Started: July 2026
Status: Active
Progress: 85 / 90 visuals complete
Milestone: Advanced P5 complete.

## Overview

Replacing StreakFit's original monochrome stick-figure exercise illustrations with warm "Rickie-style" flat-color illustrations that match the app's raccoon mascot brand. Work proceeds tier by tier (Beginner → Intermediate → Advanced), in small approved batches: implement, verify locally (direct SVG + live Exercise Tips modal), commit, deploy, verify in production.

## Design Principles

### Depict the Hero Moment

Illustrations should represent the single most recognizable and coachable moment of an exercise rather than attempting to encode an entire movement sequence.

Examples:
- burpee → recognizable midpoint posture
- plank_to_downdog → transition destination with ghost cue
- worlds_greatest_stretch → fully opened position
- no_jump_burpee → compressed hero pose
- broad_jump → airborne/explosive instant
- tuck_jump_burpee (future) → one representative moment, not every phase

Goals:
- readability at 80px
- coaching-copy alignment
- reduced ambiguity
- preservation of the Rickie visual language

## Current Progress

| Tier         | Completed | Total  |
| ------------ | --------- | ------ |
| Beginner     | 30        | 30     |
| Intermediate | 30        | 30     |
| Advanced     | 25        | 30     |
| **Overall**  | **85**    | **90** |

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

## Advanced P4 Completion Summary (20 / 30)

**P4 (5)** — original-pose and adaptation hybrid batch

* wrist_prep
* sprint_intervals
* tuck_jump
* broad_jump
* bodyweight_jefferson_curl

Highlights:

- wrist_prep required a dedicated readability pass to differentiate it from bird_dog.
- First draft failed at 80px.
- Final version uses a flatter torso silhouette and enlarged wrist-circle cue.
- tuck_jump received an airborne-gap adjustment for small-scale readability.
- bodyweight_jefferson_curl received an increased fold depth for coaching-copy alignment.

Verification:

- 220px modal verification complete
- 80px inline verification complete
- wrist_prep vs bird_dog verified side-by-side at 80px
- coaching-copy alignment confirmed
- zero regressions
- production verified on streakfit.pro
- sw.js advanced to streakfit-v0725

## Advanced P5 Completion Summary (25 / 30)

**P5 (5)** — squat/lunge/jump/sprint family batch

* assisted_pistol_squat
* plyometric_lunge
* broad_jump_consecutive
* shuttle_run
* shrimp_squat

Highlights:

- assisted_pistol_squat introduces a support-pole prop matching the coaching copy's "doorframe" example.
- plyometric_lunge shows the airborne leg-switch instant, not the before/after lunges.
- broad_jump_consecutive reuses broad_jump's figure with a continuation shadow and arrow rather than a new pose.
- shuttle_run reuses sprint_intervals' pose with a bidirectional arrow replacing the one-way speed ticks.
- shrimp_squat required a dedicated readability pass: the first draft's tucked leg and standing leg silhouettes overlapped into an ambiguous cluster, especially against assisted_pistol_squat. Fixed by separating the tucked knee (low, near the floor) from the held foot (higher, meeting the hand).

Verification:

- 220px modal verification complete
- 80px inline verification complete
- assisted_pistol_squat vs shrimp_squat verified side-by-side at 80px
- coaching-copy alignment confirmed
- zero regressions
- production verified on streakfit.pro
- sw.js advanced to streakfit-v0726

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

**85 / 90 Rickie-style visuals complete and live**

* Beginner: **100% complete**
* Intermediate: **100% complete**
* Advanced: **83% complete**

## Next Steps

Advanced P6 planning only — the final batch.

Remaining Advanced exercises: 5

* wall_handstand_hold
* l_sit_hold
* straddle_v_up
* pancake_stretch
* tuck_jump_burpee

Advanced P6 is the "Original Pose Collection" — the first batch with no existing donor silhouette to adapt from. Should focus on:
- hero-moment readability
- new-pose design
- 80px verification first
- coaching-copy alignment
