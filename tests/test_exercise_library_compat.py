"""Deployment compatibility: exercise keys are stored, so they are forever.

Since Step 1 a person's day is stored as five exercise keys, and completions
have always been stored by key. A deploy that renames or removes a key no
longer crashes anyone's day (app._retired_exercise / _repair_missing_slots
handle it), but it does turn a completed exercise into a nameless "retired"
row with no picture, for that person forever. So removing a key is a
decision, made here on purpose: every key below must stay in the library or
be listed in RETIRED with the reason and the replacement, if any.
"""
import app as appmod

# Every key the exercise library has shipped (generated 2026-10-12 from the
# library at bb2c9b0 + Step 1). Add new keys here when they ship.
SHIPPED = frozenset([
    'ankle_circles', 'archer_push_up', 'arm_circles', 'assisted_one_arm_push_up',
    'assisted_pistol_squat', 'bent_knee_leg_raise', 'bicycle_crunch', 'bird_dog',
    'bodyweight_good_morning', 'bodyweight_jefferson_curl', 'bodyweight_squat',
    'boxer_shuffle', 'broad_jump', 'broad_jump_consecutive', 'calf_raise', 'cat_cow',
    'chest_opener', 'childs_pose', 'cossack_squat', 'crunch', 'dead_bug', 'decline_push_up',
    'deep_squat_hold', 'diamond_push_up', 'doorway_pec_stretch', 'downdog_calf_stretch',
    'floor_tricep_dip', 'front_split_prep', 'full_burpee', 'glute_bridge', 'high_knees',
    'hip_flexor_kneeling', 'hollow_body_hold', 'hollow_body_rock', 'jump_squat',
    'jumping_jack', 'knee_plank', 'knee_push_up', 'l_sit_hold', 'lateral_lunge', 'low_skip',
    'marching_in_place', 'mountain_climber', 'no_jump_burpee', 'pancake_stretch',
    'pigeon_pose', 'pike_push_up', 'pike_walk_out', 'planche_lean', 'plank',
    'plank_to_downdog', 'plyometric_lunge', 'plyometric_push_up', 'pseudo_planche_push_up',
    'push_up', 'reverse_lunge', 'russian_twist', 'shoulder_cars', 'shoulder_tap',
    'shrimp_squat', 'shuttle_run', 'side_plank', 'single_leg_glute_bridge',
    'single_leg_good_morning', 'skater_jump', 'speed_squat', 'sphinx_push_up',
    'spinal_twist', 'sprint_intervals', 'standing_bicycle', 'standing_hamstring_stretch',
    'step_touch', 'step_up', 'straddle_v_up', 'straight_leg_raise', 'sumo_squat',
    'superman', 'tabata_mountain_climber', 'thoracic_rotation', 'tuck_jump',
    'tuck_jump_burpee', 'tuck_to_straight_leg_raise', 'typewriter_push_up', 'walking_lunge',
    'wall_handstand_hold', 'wall_push_up', 'wall_sit', 'wide_push_up',
    'worlds_greatest_stretch', 'wrist_prep',
])

# key -> why it left the library (and what replaced it, if anything).
RETIRED = {}


def test_no_shipped_exercise_key_disappears_without_being_retired_on_purpose():
    current = set(appmod._EXERCISE_BY_KEY)
    missing = sorted(SHIPPED - current - set(RETIRED))
    assert not missing, (
        f"exercise keys removed from the library: {missing}. Stored missions and "
        "completion history reference them. Keep the key (rename only the display "
        "name), or list it in RETIRED with a reason.")


def test_every_library_key_is_known_here():
    """A new exercise is added to SHIPPED in the same change, so the list
    above stays the complete record of what people may have stored."""
    unknown = sorted(set(appmod._EXERCISE_BY_KEY) - SHIPPED)
    assert not unknown, f"add these new exercise keys to SHIPPED: {unknown}"


def test_a_retired_key_is_really_gone():
    stale = sorted(k for k in RETIRED if k in appmod._EXERCISE_BY_KEY)
    assert not stale, f"listed as retired but still in the library: {stale}"
