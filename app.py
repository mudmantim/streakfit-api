import os
import hashlib
import random
from datetime import datetime, date, timedelta
from flask import Flask, request, jsonify, abort
from flask_sqlalchemy import SQLAlchemy
from flask_migrate import Migrate
from werkzeug.security import generate_password_hash, check_password_hash
from flask_jwt_extended import JWTManager, create_access_token, jwt_required, get_jwt_identity
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address

app = Flask(__name__)

# Fallback to local SQLite only if Render's PostgreSQL URL isn't present
DATABASE_URL = os.environ.get('DATABASE_URL')
if DATABASE_URL and DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)

app.config['SQLALCHEMY_DATABASE_URI'] = DATABASE_URL or 'sqlite:///streakfit.db'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
_secret_key = os.environ.get('SECRET_KEY')
if not _secret_key:
    raise RuntimeError("SECRET_KEY environment variable is required but not set")
app.config['SECRET_KEY'] = _secret_key

_jwt_secret_key = os.environ.get('JWT_SECRET_KEY')
if not _jwt_secret_key:
    raise RuntimeError("JWT_SECRET_KEY environment variable is required but not set")
app.config['JWT_SECRET_KEY'] = _jwt_secret_key
app.config['JWT_ACCESS_TOKEN_EXPIRES'] = timedelta(hours=1)

db = SQLAlchemy(app)
migrate = Migrate(app, db)
jwt = JWTManager(app)
limiter = Limiter(
    get_remote_address,
    app=app,
    default_limits=[],
    storage_uri=os.environ.get("RATELIMIT_STORAGE_URI", "memory://"),
)

# --- Exercise Library ---

VALID_SKILL_LEVELS = {'beginner', 'intermediate', 'advanced', 'custom'}

EXERCISE_LIBRARY = {
    'beginner': {
        'upper_body': [
            {'key': 'wall_push_up', 'name': 'Wall Push-Up', 'category': 'upper_body',
             'difficulty': 'beginner', 'reps_or_duration': '3 sets of 12 reps',
             'instructions': 'Stand arm\'s length from a wall with palms at shoulder height. Bend your elbows to bring your chest toward the wall, then push back to start.'},
            {'key': 'knee_push_up', 'name': 'Knee Push-Up', 'category': 'upper_body',
             'difficulty': 'beginner', 'reps_or_duration': '3 sets of 10 reps',
             'instructions': 'Start in a plank on your hands and knees with a flat back. Lower your chest toward the floor, then press back up.'},
            {'key': 'arm_circles', 'name': 'Arm Circles', 'category': 'upper_body',
             'difficulty': 'beginner', 'reps_or_duration': '3 sets of 20 reps each direction',
             'instructions': 'Stand with arms extended at shoulder height and make small continuous circles — 20 forward, then 20 backward.'},
            {'key': 'shoulder_tap', 'name': 'Shoulder Tap', 'category': 'upper_body',
             'difficulty': 'beginner', 'reps_or_duration': '3 sets of 10 reps each side',
             'instructions': 'Hold a high plank. Keeping your hips square, lift one hand to tap the opposite shoulder, then alternate sides.'},
            {'key': 'chest_opener', 'name': 'Standing Chest Opener', 'category': 'upper_body',
             'difficulty': 'beginner', 'reps_or_duration': '3 holds of 30 seconds',
             'instructions': 'Interlace your fingers behind your back, squeeze your shoulder blades together, and lift your chest. Hold for 30 seconds.'},
            {'key': 'chair_tricep_dip', 'name': 'Chair Tricep Dip', 'category': 'upper_body',
             'difficulty': 'beginner', 'reps_or_duration': '3 sets of 8 reps',
             'instructions': 'Sit on the edge of a sturdy chair with hands gripping the seat. Slide forward, lower by bending your elbows to 90 degrees, then press back up.'},
        ],
        'lower_body': [
            {'key': 'bodyweight_squat', 'name': 'Bodyweight Squat', 'category': 'lower_body',
             'difficulty': 'beginner', 'reps_or_duration': '3 sets of 12 reps',
             'instructions': 'Stand feet shoulder-width apart. Push hips back and bend knees until thighs are parallel to the floor, then return to standing.'},
            {'key': 'reverse_lunge', 'name': 'Reverse Lunge', 'category': 'lower_body',
             'difficulty': 'beginner', 'reps_or_duration': '3 sets of 8 reps each leg',
             'instructions': 'Step one foot back and lower the back knee toward the floor until both knees form 90-degree angles, then push through the front heel to return.'},
            {'key': 'glute_bridge', 'name': 'Glute Bridge', 'category': 'lower_body',
             'difficulty': 'beginner', 'reps_or_duration': '3 sets of 15 reps',
             'instructions': 'Lie on your back with knees bent. Drive through your heels to lift your hips until your body forms a straight line from shoulders to knees.'},
            {'key': 'calf_raise', 'name': 'Standing Calf Raise', 'category': 'lower_body',
             'difficulty': 'beginner', 'reps_or_duration': '3 sets of 20 reps',
             'instructions': 'Stand feet hip-width apart and rise onto the balls of your feet as high as possible, pause at the top, then slowly lower.'},
            {'key': 'wall_sit', 'name': 'Wall Sit', 'category': 'lower_body',
             'difficulty': 'beginner', 'reps_or_duration': '3 sets of 20 seconds',
             'instructions': 'Slide down a wall until your thighs are parallel to the floor and hold, keeping your knees directly over your ankles.'},
            {'key': 'step_up', 'name': 'Step-Up', 'category': 'lower_body',
             'difficulty': 'beginner', 'reps_or_duration': '3 sets of 10 reps each leg',
             'instructions': 'Step up onto a stair or low box with one foot, bring the other up, then step back down. Alternate the leading leg each set.'},
        ],
        'core': [
            {'key': 'dead_bug', 'name': 'Dead Bug', 'category': 'core',
             'difficulty': 'beginner', 'reps_or_duration': '3 sets of 8 reps each side',
             'instructions': 'Lie on your back with arms at the ceiling and knees at 90 degrees. Lower one arm and the opposite leg with your back pressed to the floor, then return and alternate.'},
            {'key': 'bird_dog', 'name': 'Bird Dog', 'category': 'core',
             'difficulty': 'beginner', 'reps_or_duration': '3 sets of 8 reps each side',
             'instructions': 'On hands and knees, extend one arm and the opposite leg until horizontal, hold briefly, then return and switch sides.'},
            {'key': 'knee_plank', 'name': 'Plank from Knees', 'category': 'core',
             'difficulty': 'beginner', 'reps_or_duration': '3 sets of 20 seconds',
             'instructions': 'Hold a forearm plank with knees on the floor, forming a straight line from head to knees. Keep your core tight and breathe steadily.'},
            {'key': 'crunch', 'name': 'Crunch', 'category': 'core',
             'difficulty': 'beginner', 'reps_or_duration': '3 sets of 15 reps',
             'instructions': 'Lie on your back with knees bent. Curl your shoulders off the floor by contracting your abs, then lower slowly.'},
            {'key': 'bent_knee_leg_raise', 'name': 'Bent-Knee Leg Raise', 'category': 'core',
             'difficulty': 'beginner', 'reps_or_duration': '3 sets of 12 reps',
             'instructions': 'Lie on your back with knees bent at 90 degrees and raised. Lower your feet toward the floor without touching, then lift back up. Keep your lower back pressed down.'},
            {'key': 'superman', 'name': 'Superman Hold', 'category': 'core',
             'difficulty': 'beginner', 'reps_or_duration': '3 sets of 10 reps',
             'instructions': 'Lie face down with arms extended overhead. Simultaneously lift your arms, chest, and legs off the floor, hold for a second, then lower.'},
        ],
        'mobility': [
            {'key': 'cat_cow', 'name': 'Cat-Cow Stretch', 'category': 'mobility',
             'difficulty': 'beginner', 'reps_or_duration': '3 sets of 8 breath cycles',
             'instructions': 'On hands and knees, inhale and arch your back with head up (cow), then exhale and round your spine toward the ceiling (cat). Move slowly with your breath.'},
            {'key': 'hip_flexor_kneeling', 'name': 'Kneeling Hip Flexor Stretch', 'category': 'mobility',
             'difficulty': 'beginner', 'reps_or_duration': '2 sets of 30 seconds each side',
             'instructions': 'Kneel with one foot forward. Shift your hips forward until you feel a stretch in the front of the kneeling-side hip. Keep your torso upright.'},
            {'key': 'standing_hamstring_stretch', 'name': 'Standing Hamstring Stretch', 'category': 'mobility',
             'difficulty': 'beginner', 'reps_or_duration': '2 sets of 30 seconds each leg',
             'instructions': 'Place one foot on a low surface and hinge forward at the hip with a flat back until you feel a stretch in the back of your thigh.'},
            {'key': 'childs_pose', 'name': "Child's Pose", 'category': 'mobility',
             'difficulty': 'beginner', 'reps_or_duration': '3 holds of 30 seconds',
             'instructions': 'Kneel and sit back on your heels, extend your arms forward on the floor, and rest your forehead down. Breathe deeply and let your hips sink.'},
            {'key': 'thoracic_rotation', 'name': 'Seated Thoracic Rotation', 'category': 'mobility',
             'difficulty': 'beginner', 'reps_or_duration': '3 sets of 8 reps each side',
             'instructions': 'Sit cross-legged with one hand behind your head. Rotate your upper body to bring that elbow back as far as comfortable, then return.'},
            {'key': 'ankle_circles', 'name': 'Ankle Circles', 'category': 'mobility',
             'difficulty': 'beginner', 'reps_or_duration': '3 sets of 10 circles each direction',
             'instructions': 'Lift one foot slightly and rotate the ankle in slow full circles. Complete all reps one direction then reverse, then switch feet.'},
        ],
        'conditioning': [
            {'key': 'marching_in_place', 'name': 'Marching in Place', 'category': 'conditioning',
             'difficulty': 'beginner', 'reps_or_duration': '3 sets of 30 seconds',
             'instructions': 'March in place lifting your knees to hip height with each step. Pump your arms in opposition and maintain an upright posture.'},
            {'key': 'jumping_jack', 'name': 'Jumping Jack', 'category': 'conditioning',
             'difficulty': 'beginner', 'reps_or_duration': '3 sets of 20 reps',
             'instructions': 'Jump your feet out wide while raising your arms overhead, then jump back to start. Land softly with each rep.'},
            {'key': 'step_touch', 'name': 'Side Step Touch', 'category': 'conditioning',
             'difficulty': 'beginner', 'reps_or_duration': '3 sets of 30 seconds',
             'instructions': 'Step one foot out to the side then bring the other foot to meet it. Continue side to side at a brisk rhythmic pace.'},
            {'key': 'standing_bicycle', 'name': 'Standing Bicycle Kick', 'category': 'conditioning',
             'difficulty': 'beginner', 'reps_or_duration': '3 sets of 20 reps',
             'instructions': 'Stand with hands behind your head. Lift one knee while twisting the opposite elbow toward it, then alternate sides in a smooth motion.'},
            {'key': 'low_skip', 'name': 'Low-Impact Skip', 'category': 'conditioning',
             'difficulty': 'beginner', 'reps_or_duration': '3 sets of 30 seconds',
             'instructions': 'Skip in place with a low controlled hop on each foot. Keep the impact light and swing your arms comfortably.'},
            {'key': 'boxer_shuffle', 'name': 'Boxer Shuffle', 'category': 'conditioning',
             'difficulty': 'beginner', 'reps_or_duration': '3 sets of 30 seconds',
             'instructions': 'Bounce lightly from foot to foot with knees slightly bent, keeping the movement small and rhythmic as if skipping rope without a rope.'},
        ],
    },
    'intermediate': {
        'upper_body': [
            {'key': 'push_up', 'name': 'Push-Up', 'category': 'upper_body',
             'difficulty': 'intermediate', 'reps_or_duration': '4 sets of 12 reps',
             'instructions': 'Start in a high plank with hands shoulder-width apart. Lower your chest to just above the floor keeping elbows at 45 degrees, then press back up with full arm extension.'},
            {'key': 'diamond_push_up', 'name': 'Diamond Push-Up', 'category': 'upper_body',
             'difficulty': 'intermediate', 'reps_or_duration': '3 sets of 10 reps',
             'instructions': 'Form a diamond with your thumbs and index fingers on the floor beneath your chest. Perform a push-up keeping your elbows close to your body.'},
            {'key': 'pike_push_up', 'name': 'Pike Push-Up', 'category': 'upper_body',
             'difficulty': 'intermediate', 'reps_or_duration': '3 sets of 10 reps',
             'instructions': 'Start in downward dog with hips high. Bend your elbows to lower the crown of your head toward the floor, then press back up. This targets the shoulders.'},
            {'key': 'decline_push_up', 'name': 'Decline Push-Up', 'category': 'upper_body',
             'difficulty': 'intermediate', 'reps_or_duration': '3 sets of 10 reps',
             'instructions': 'Place your feet on an elevated surface and hands on the floor. Perform a push-up keeping your body in a straight line throughout.'},
            {'key': 'wide_push_up', 'name': 'Wide-Grip Push-Up', 'category': 'upper_body',
             'difficulty': 'intermediate', 'reps_or_duration': '3 sets of 12 reps',
             'instructions': 'Place your hands wider than shoulder-width and perform a push-up, allowing elbows to flare to the sides to emphasise the chest.'},
            {'key': 'parallel_dip', 'name': 'Parallel Bar Dip', 'category': 'upper_body',
             'difficulty': 'intermediate', 'reps_or_duration': '3 sets of 10 reps',
             'instructions': 'Grip parallel bars and hold yourself up on straight arms. Lower until upper arms are parallel to the floor, then press back up.'},
        ],
        'lower_body': [
            {'key': 'jump_squat', 'name': 'Jump Squat', 'category': 'lower_body',
             'difficulty': 'intermediate', 'reps_or_duration': '4 sets of 10 reps',
             'instructions': 'Lower into a squat, then explode upward into a jump. Land softly with knees slightly bent and immediately sink into the next squat.'},
            {'key': 'walking_lunge', 'name': 'Walking Lunge', 'category': 'lower_body',
             'difficulty': 'intermediate', 'reps_or_duration': '3 sets of 12 reps each leg',
             'instructions': 'Step forward into a lunge lowering the back knee toward the floor, then push through the front heel and step the rear foot forward into the next rep.'},
            {'key': 'single_leg_glute_bridge', 'name': 'Single-Leg Glute Bridge', 'category': 'lower_body',
             'difficulty': 'intermediate', 'reps_or_duration': '3 sets of 12 reps each leg',
             'instructions': 'Lie on your back with one knee bent and the other leg extended. Drive through the planted heel to raise your hips until your body forms a straight diagonal.'},
            {'key': 'lateral_lunge', 'name': 'Lateral Lunge', 'category': 'lower_body',
             'difficulty': 'intermediate', 'reps_or_duration': '3 sets of 10 reps each side',
             'instructions': 'Step wide to one side, bend that knee and push the hip back while keeping the other leg straight, then push back to standing.'},
            {'key': 'sumo_squat', 'name': 'Sumo Squat', 'category': 'lower_body',
             'difficulty': 'intermediate', 'reps_or_duration': '4 sets of 15 reps',
             'instructions': 'Stand with feet wider than shoulder-width and toes out. Squat deep keeping your torso upright and knees tracking over your toes.'},
            {'key': 'nordic_curl', 'name': 'Nordic Hamstring Curl', 'category': 'lower_body',
             'difficulty': 'intermediate', 'reps_or_duration': '3 sets of 6 reps',
             'instructions': 'Anchor your feet and kneel upright. Slowly lower your torso toward the floor by letting your knees extend, controlling the descent entirely with your hamstrings.'},
        ],
        'core': [
            {'key': 'plank', 'name': 'Plank', 'category': 'core',
             'difficulty': 'intermediate', 'reps_or_duration': '3 sets of 45 seconds',
             'instructions': 'Hold a push-up position with a straight line from head to heels. Engage your abs, glutes, and quads without letting your hips sag or rise.'},
            {'key': 'hollow_body_hold', 'name': 'Hollow Body Hold', 'category': 'core',
             'difficulty': 'intermediate', 'reps_or_duration': '3 sets of 20 seconds',
             'instructions': 'Lie on your back, press your lower back firmly to the floor, and lift arms overhead and legs a few inches. Hold this curved dish shape.'},
            {'key': 'russian_twist', 'name': 'Russian Twist', 'category': 'core',
             'difficulty': 'intermediate', 'reps_or_duration': '3 sets of 20 reps',
             'instructions': 'Sit with knees bent and lean back slightly. Clasp your hands and rotate your torso left and right, touching the floor on each side.'},
            {'key': 'bicycle_crunch', 'name': 'Bicycle Crunch', 'category': 'core',
             'difficulty': 'intermediate', 'reps_or_duration': '3 sets of 20 reps',
             'instructions': 'On your back with hands behind your head, bring one knee to your chest while rotating the opposite elbow toward it. Alternate in a pedalling motion.'},
            {'key': 'straight_leg_raise', 'name': 'Straight-Leg Raise', 'category': 'core',
             'difficulty': 'intermediate', 'reps_or_duration': '3 sets of 15 reps',
             'instructions': 'Lie on your back with legs straight. Lift both legs to 90 degrees, then lower slowly without letting them touch the floor.'},
            {'key': 'side_plank', 'name': 'Side Plank', 'category': 'core',
             'difficulty': 'intermediate', 'reps_or_duration': '3 sets of 30 seconds each side',
             'instructions': 'Push up onto your forearm and the edge of your foot. Keep your body in a straight line with hips lifted, then switch sides.'},
        ],
        'mobility': [
            {'key': 'worlds_greatest_stretch', 'name': "World's Greatest Stretch", 'category': 'mobility',
             'difficulty': 'intermediate', 'reps_or_duration': '3 sets of 5 reps each side',
             'instructions': 'Step into a deep lunge, place the same-side hand on the floor, then rotate the top arm toward the ceiling. Shift into a hamstring stretch, then repeat on the other side.'},
            {'key': 'deep_squat_hold', 'name': 'Deep Squat Hold', 'category': 'mobility',
             'difficulty': 'intermediate', 'reps_or_duration': '3 holds of 45 seconds',
             'instructions': 'Squat with feet shoulder-width apart and heels on the floor. Use your elbows to gently push your knees out and hold a tall, upright torso.'},
            {'key': 'pigeon_pose', 'name': 'Pigeon Pose', 'category': 'mobility',
             'difficulty': 'intermediate', 'reps_or_duration': '2 holds of 60 seconds each side',
             'instructions': 'From a plank, bring one knee toward your wrist and let the shin rest at an angle. Lower your hips and walk your hands forward to deepen the stretch.'},
            {'key': 'spinal_twist', 'name': 'Supine Spinal Twist', 'category': 'mobility',
             'difficulty': 'intermediate', 'reps_or_duration': '2 holds of 45 seconds each side',
             'instructions': 'Lie on your back, draw one knee to your chest, then guide it across your body to the floor while extending the opposite arm out.'},
            {'key': 'doorway_pec_stretch', 'name': 'Doorway Chest Stretch', 'category': 'mobility',
             'difficulty': 'intermediate', 'reps_or_duration': '3 holds of 30 seconds',
             'instructions': 'Place your forearms on a doorframe at shoulder height and lean gently forward until you feel a stretch across your chest and shoulders.'},
            {'key': 'downdog_calf_stretch', 'name': 'Downward Dog Calf Stretch', 'category': 'mobility',
             'difficulty': 'intermediate', 'reps_or_duration': '3 sets of 5 reps each leg',
             'instructions': 'In downward dog, press one heel toward the floor and hold 2 seconds, then alternate feet in a gentle pedalling motion for the full count.'},
        ],
        'conditioning': [
            {'key': 'no_jump_burpee', 'name': 'No-Jump Burpee', 'category': 'conditioning',
             'difficulty': 'intermediate', 'reps_or_duration': '3 sets of 10 reps',
             'instructions': 'From standing, place hands on the floor, step feet back to a plank, do a push-up, step feet forward, and stand back up. No jump at the top.'},
            {'key': 'mountain_climber', 'name': 'Mountain Climber', 'category': 'conditioning',
             'difficulty': 'intermediate', 'reps_or_duration': '3 sets of 20 reps',
             'instructions': 'In a high plank, drive one knee toward your chest then quickly switch legs. Continue alternating at a fast pace while keeping your hips level.'},
            {'key': 'high_knees', 'name': 'High Knees', 'category': 'conditioning',
             'difficulty': 'intermediate', 'reps_or_duration': '3 sets of 30 seconds',
             'instructions': 'Run in place driving your knees up to hip height. Pump your arms and land on the balls of your feet at a fast rhythmic pace.'},
            {'key': 'skater_jump', 'name': 'Skater Jump', 'category': 'conditioning',
             'difficulty': 'intermediate', 'reps_or_duration': '3 sets of 15 reps each side',
             'instructions': 'Leap laterally from one foot to the other, landing softly with a slight knee bend. Swing your arms for momentum like a speed skater.'},
            {'key': 'plank_to_downdog', 'name': 'Plank to Downward Dog', 'category': 'conditioning',
             'difficulty': 'intermediate', 'reps_or_duration': '3 sets of 10 reps',
             'instructions': 'From a high plank, push your hips up and back into downward dog, hold briefly, then flow back to plank. Coordinate each movement with your breath.'},
            {'key': 'speed_squat', 'name': 'Speed Squat', 'category': 'conditioning',
             'difficulty': 'intermediate', 'reps_or_duration': '4 sets of 15 reps',
             'instructions': 'Perform bodyweight squats as quickly as possible while maintaining good form. Reach at least parallel on every rep and fully extend at the top.'},
        ],
    },
    'advanced': {
        'upper_body': [
            {'key': 'archer_push_up', 'name': 'Archer Push-Up', 'category': 'upper_body',
             'difficulty': 'advanced', 'reps_or_duration': '4 sets of 8 reps each side',
             'instructions': 'In a wide push-up stance, lower toward one hand while extending the other arm straight out to the side. Push up and repeat on the opposite side.'},
            {'key': 'pseudo_planche_push_up', 'name': 'Pseudo Planche Push-Up', 'category': 'upper_body',
             'difficulty': 'advanced', 'reps_or_duration': '4 sets of 8 reps',
             'instructions': 'Place hands facing backward at hip level and lean your shoulders forward past your wrists. Perform a push-up maintaining this extreme forward lean.'},
            {'key': 'typewriter_push_up', 'name': 'Typewriter Push-Up', 'category': 'upper_body',
             'difficulty': 'advanced', 'reps_or_duration': '3 sets of 6 reps each side',
             'instructions': 'Lower into the bottom of a wide push-up, then shift your weight horizontally across to one side before pressing up on that arm. Alternate sides each rep.'},
            {'key': 'plyometric_push_up', 'name': 'Plyometric Push-Up', 'category': 'upper_body',
             'difficulty': 'advanced', 'reps_or_duration': '4 sets of 8 reps',
             'instructions': 'Perform a push-up with enough force to launch your hands off the floor. Land with soft elbows and immediately lower into the next rep.'},
            {'key': 'wall_handstand_hold', 'name': 'Wall Handstand Hold', 'category': 'upper_body',
             'difficulty': 'advanced', 'reps_or_duration': '3 holds of 20 seconds',
             'instructions': 'Kick up into a handstand against a wall. Stack wrists, elbows, and shoulders vertically. Engage your core and glutes, pressing the floor away with your fingertips.'},
            {'key': 'assisted_one_arm_push_up', 'name': 'Assisted One-Arm Push-Up', 'category': 'upper_body',
             'difficulty': 'advanced', 'reps_or_duration': '3 sets of 5 reps each side',
             'instructions': 'Perform a push-up on one hand while resting the other on a low support. Lower with control keeping your body square, then press back up.'},
        ],
        'lower_body': [
            {'key': 'assisted_pistol_squat', 'name': 'Assisted Pistol Squat', 'category': 'lower_body',
             'difficulty': 'advanced', 'reps_or_duration': '3 sets of 6 reps each leg',
             'instructions': 'Hold a support for balance and stand on one leg with the other extended forward. Slowly squat as deep as possible on the standing leg, then drive back up.'},
            {'key': 'plyometric_lunge', 'name': 'Plyometric Lunge', 'category': 'lower_body',
             'difficulty': 'advanced', 'reps_or_duration': '4 sets of 10 reps each leg',
             'instructions': 'Lower into a lunge, then explode off both feet to switch leg positions in mid-air. Land softly in a lunge with the opposite leg forward and continue.'},
            {'key': 'nordic_hamstring_advanced', 'name': 'Nordic Hamstring Curl', 'category': 'lower_body',
             'difficulty': 'advanced', 'reps_or_duration': '4 sets of 8 reps',
             'instructions': 'Anchor your feet and lower your torso toward the floor by extending your knees. Your hamstrings control the entire descent — use hands to catch yourself if needed.'},
            {'key': 'shrimp_squat', 'name': 'Shrimp Squat', 'category': 'lower_body',
             'difficulty': 'advanced', 'reps_or_duration': '3 sets of 5 reps each leg',
             'instructions': 'Stand on one leg and hold the other foot behind you. Slowly lower your back knee toward the floor in a controlled single-leg squat, then drive back up.'},
            {'key': 'broad_jump', 'name': 'Broad Jump', 'category': 'lower_body',
             'difficulty': 'advanced', 'reps_or_duration': '4 sets of 5 reps',
             'instructions': 'Swing your arms back, bend your knees, then explode forward as far as possible. Land with soft knees and absorb the impact through a full squat.'},
            {'key': 'sprint_intervals', 'name': 'Sprint Intervals', 'category': 'lower_body',
             'difficulty': 'advanced', 'reps_or_duration': '8 rounds of 20 seconds on / 10 seconds off',
             'instructions': 'Sprint at maximum effort for 20 seconds then rest 10 seconds. This is a Tabata protocol — complete all 8 rounds without reducing intensity.'},
        ],
        'core': [
            {'key': 'dragon_flag', 'name': 'Dragon Flag', 'category': 'core',
             'difficulty': 'advanced', 'reps_or_duration': '3 sets of 5 reps',
             'instructions': 'Grip a bench above your head and raise your body to vertical supported by your shoulders. Lower it as a rigid unit stopping just before contact — do not let your hips bend.'},
            {'key': 'l_sit_hold', 'name': 'L-Sit Hold', 'category': 'core',
             'difficulty': 'advanced', 'reps_or_duration': '3 holds of 10 seconds',
             'instructions': 'Support yourself on parallel bars or chairs. Press down hard, raise your legs to horizontal, and hold your entire body off the surface with legs straight and toes pointed.'},
            {'key': 'ab_wheel_rollout', 'name': 'Ab Wheel Rollout', 'category': 'core',
             'difficulty': 'advanced', 'reps_or_duration': '3 sets of 8 reps',
             'instructions': 'Kneel with an ab wheel in both hands. Roll forward until your body is nearly parallel to the floor, then pull back using your abs. Do not let your hips sag.'},
            {'key': 'hanging_leg_raise', 'name': 'Hanging Leg Raise', 'category': 'core',
             'difficulty': 'advanced', 'reps_or_duration': '4 sets of 10 reps',
             'instructions': 'Hang from a bar and raise your legs to 90 degrees without swinging, then lower with full control. Do not use momentum.'},
            {'key': 'hollow_body_rock', 'name': 'Hollow Body Rock', 'category': 'core',
             'difficulty': 'advanced', 'reps_or_duration': '3 sets of 30 seconds',
             'instructions': 'Hold a hollow body position and rock forward and backward in a controlled arc. Your lower back must stay rounded throughout — any arch means you have lost the position.'},
            {'key': 'planche_lean', 'name': 'Planche Lean', 'category': 'core',
             'difficulty': 'advanced', 'reps_or_duration': '3 holds of 20 seconds',
             'instructions': 'Start in a plank on straight arms. Gradually shift your weight forward over your wrists keeping your body completely rigid. The further forward, the harder.'},
        ],
        'mobility': [
            {'key': 'pancake_stretch', 'name': 'Pancake Stretch', 'category': 'mobility',
             'difficulty': 'advanced', 'reps_or_duration': '3 holds of 60 seconds',
             'instructions': 'Sit in a wide straddle and hinge forward from the hips with a flat back, walking your hands along the floor. Relax and breathe deeply into the stretch — do not force it.'},
            {'key': 'jefferson_curl', 'name': 'Jefferson Curl', 'category': 'mobility',
             'difficulty': 'advanced', 'reps_or_duration': '3 sets of 8 slow reps',
             'instructions': 'Stand on an elevated surface holding light weight. Starting from the neck, curl each vertebra forward until fully hanging, then uncurl slowly from the base of the spine up.'},
            {'key': 'front_split_prep', 'name': 'Front Split Progression', 'category': 'mobility',
             'difficulty': 'advanced', 'reps_or_duration': '3 holds of 60 seconds each side',
             'instructions': 'Kneel in a low lunge and slide your front foot forward, using blocks for hand support. Sink as deep as your flexibility allows and breathe steadily.'},
            {'key': 'cossack_squat', 'name': 'Cossack Squat', 'category': 'mobility',
             'difficulty': 'advanced', 'reps_or_duration': '3 sets of 8 reps each side',
             'instructions': 'Stand in a wide stance, shift your weight to one leg and squat deep while extending the other leg straight to the side. Alternate sides with full control.'},
            {'key': 'shoulder_dislocate', 'name': 'Shoulder Dislocate with Band', 'category': 'mobility',
             'difficulty': 'advanced', 'reps_or_duration': '3 sets of 10 reps',
             'instructions': 'Hold a resistance band wide in front and arc it overhead and behind your body with straight arms. Widen your grip if you feel any strain in the shoulders.'},
            {'key': 'wrist_prep', 'name': 'Wrist Mobility Routine', 'category': 'mobility',
             'difficulty': 'advanced', 'reps_or_duration': '3 sets of 10 reps each movement',
             'instructions': 'On all fours, perform: wrist circles both directions, forward and backward finger circles, and loaded wrist stretches. Essential prep for handstand and planche work.'},
        ],
        'conditioning': [
            {'key': 'full_burpee', 'name': 'Full Burpee', 'category': 'conditioning',
             'difficulty': 'advanced', 'reps_or_duration': '4 sets of 15 reps',
             'instructions': 'From standing, drop to a squat, kick back to a plank, do a push-up, jump feet to hands, then explode upward with arms overhead. Land softly and repeat immediately.'},
            {'key': 'tabata_mountain_climber', 'name': 'Tabata Mountain Climbers', 'category': 'conditioning',
             'difficulty': 'advanced', 'reps_or_duration': '8 rounds of 20 seconds on / 10 seconds off',
             'instructions': 'Perform mountain climbers at maximum speed for 20 seconds, then rest exactly 10 seconds. Complete all 8 rounds for a full 4-minute Tabata protocol.'},
            {'key': 'box_jump', 'name': 'Box Jump', 'category': 'conditioning',
             'difficulty': 'advanced', 'reps_or_duration': '4 sets of 8 reps',
             'instructions': 'Dip into a quarter squat and explode upward, landing softly in a squat on top of a sturdy box. Stand fully, then step down carefully — never jump down.'},
            {'key': 'devil_press', 'name': 'Devil Press', 'category': 'conditioning',
             'difficulty': 'advanced', 'reps_or_duration': '3 sets of 8 reps',
             'instructions': 'Hold dumbbells and drop into a burpee with weights on the floor. At the top, swing both dumbbells overhead in one powerful movement instead of jumping.'},
            {'key': 'broad_jump_consecutive', 'name': 'Consecutive Broad Jumps', 'category': 'conditioning',
             'difficulty': 'advanced', 'reps_or_duration': '4 sets of 5 jumps',
             'instructions': 'Perform 5 broad jumps in sequence without pausing between them. Land and immediately load into the next jump, maintaining maximum power throughout.'},
            {'key': 'shuttle_run', 'name': 'Shuttle Run', 'category': 'conditioning',
             'difficulty': 'advanced', 'reps_or_duration': '5 rounds of 10m x 4 lengths',
             'instructions': 'Sprint 10 meters to a marker, touch it, sprint back, and repeat for 4 lengths per round. Rest 45 seconds between rounds. Focus on explosive direction changes.'},
        ],
    },
}


def get_daily_exercises(user_id, date_str, skill_level):
    if skill_level not in EXERCISE_LIBRARY:
        skill_level = 'beginner'
    seed = int(hashlib.sha256(
        f"{user_id}:{date_str}:{skill_level}".encode()
    ).hexdigest(), 16) % (2 ** 32)
    rng = random.Random(seed)
    result = []
    for category in ('upper_body', 'lower_body', 'core', 'mobility', 'conditioning'):
        result.append(rng.choice(EXERCISE_LIBRARY[skill_level][category]))
    return result


# --- Database Models ---

class User(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False)
    password_hash = db.Column(db.String(256), nullable=False)
    skill_level = db.Column(db.String(20), nullable=False, default='beginner')
    challenges = db.relationship('Challenge', backref='owner', lazy=True)

class Challenge(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    title = db.Column(db.String(100), nullable=False)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    current_streak = db.Column(db.Integer, default=0)
    longest_streak = db.Column(db.Integer, default=0)
    last_check_in = db.Column(db.Date, nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

class DailyCompletion(db.Model):
    __tablename__ = 'daily_completion'
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    date = db.Column(db.Date, nullable=False)
    exercise_key = db.Column(db.String(100), nullable=False)
    completed_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)

    __table_args__ = (
        db.UniqueConstraint('user_id', 'date', 'exercise_key', name='uq_daily_completion'),
        db.Index('ix_daily_completion_user_date', 'user_id', 'date'),
    )


# --- Frontend ---

@app.route('/')
def frontend():
    return app.send_static_file('index.html')


# --- Health Check ---

@app.route('/health', methods=['GET'])
def health():
    return jsonify({"status": "ok"}), 200


# --- API Routes ---

@app.route('/api/register', methods=['POST'])
@limiter.limit("5 per minute")
def register():
    data = request.get_json()
    if not data or not data.get('username') or not data.get('password'):
        return jsonify({"error": "Missing credentials"}), 400

    if User.query.filter_by(username=data['username']).first():
        return jsonify({"error": "Username already exists"}), 400

    hashed_pw = generate_password_hash(data['password'], method='pbkdf2:sha256')
    new_user = User(username=data['username'], password_hash=hashed_pw)
    db.session.add(new_user)
    db.session.commit()
    return jsonify({"message": "User registered successfully"}), 201

@app.route('/api/login', methods=['POST'])
@limiter.limit("10 per minute")
def login():
    data = request.get_json()
    if not data or not data.get('username') or not data.get('password'):
        return jsonify({"error": "Missing credentials"}), 400

    user = User.query.filter_by(username=data['username']).first()
    if not user or not check_password_hash(user.password_hash, data['password']):
        return jsonify({"error": "Invalid credentials"}), 401

    access_token = create_access_token(identity=str(user.id))
    return jsonify({"access_token": access_token}), 200

@app.route('/api/me', methods=['GET'])
@jwt_required()
def get_me():
    user_id = int(get_jwt_identity())
    user = db.session.get(User, user_id)
    if user is None:
        abort(404)
    return jsonify({"id": user.id, "username": user.username, "skill_level": user.skill_level}), 200

@app.route('/api/me', methods=['PATCH'])
@jwt_required()
def update_me():
    data = request.get_json()
    if not data or 'skill_level' not in data:
        return jsonify({"error": "skill_level is required"}), 400

    skill_level = data['skill_level']
    if skill_level not in VALID_SKILL_LEVELS:
        return jsonify({"error": "Invalid skill_level. Must be one of: beginner, intermediate, advanced, custom"}), 400

    user_id = int(get_jwt_identity())
    user = db.session.get(User, user_id)
    if user is None:
        abort(404)

    user.skill_level = skill_level
    db.session.commit()
    return jsonify({"id": user.id, "username": user.username, "skill_level": user.skill_level}), 200

@app.route('/api/challenges', methods=['POST'])
@jwt_required()
def create_challenge():
    data = request.get_json()
    if not data or not data.get('title'):
        return jsonify({"error": "Invalid data"}), 400

    user_id = int(get_jwt_identity())
    new_challenge = Challenge(title=data['title'], user_id=user_id)
    db.session.add(new_challenge)
    db.session.commit()
    return jsonify({"message": "Challenge created", "challenge_id": new_challenge.id}), 201

@app.route('/api/challenges', methods=['GET'])
@jwt_required()
def get_challenges():
    user_id = int(get_jwt_identity())
    challenges = db.session.execute(
        db.select(Challenge).where(Challenge.user_id == user_id)
    ).scalars().all()
    return jsonify([{
        "id": c.id,
        "title": c.title,
        "current_streak": c.current_streak,
        "longest_streak": c.longest_streak,
        "last_check_in": c.last_check_in.isoformat() if c.last_check_in else None,
        "created_at": c.created_at.isoformat()
    } for c in challenges]), 200

@app.route('/api/challenges/<int:challenge_id>', methods=['GET'])
@jwt_required()
def get_challenge(challenge_id):
    user_id = int(get_jwt_identity())
    challenge = db.session.execute(
        db.select(Challenge).where(
            Challenge.id == challenge_id,
            Challenge.user_id == user_id
        )
    ).scalar_one_or_none()
    if challenge is None:
        abort(404)
    return jsonify({
        "id": challenge.id,
        "title": challenge.title,
        "current_streak": challenge.current_streak,
        "longest_streak": challenge.longest_streak,
        "last_check_in": challenge.last_check_in.isoformat() if challenge.last_check_in else None,
        "created_at": challenge.created_at.isoformat()
    }), 200

@app.route('/api/challenges/<int:challenge_id>/checkin', methods=['POST'])
@jwt_required()
def check_in(challenge_id):
    current_user_id = get_jwt_identity()
    challenge = db.session.execute(
        db.select(Challenge).where(Challenge.id == challenge_id).with_for_update()
    ).scalar_one_or_none()
    if challenge is None:
        abort(404)

    if challenge.user_id != int(current_user_id):
        return jsonify({"error": "Forbidden"}), 403

    today = date.today()

    if challenge.last_check_in == today:
        return jsonify({"message": "Already checked in today", "streak": challenge.current_streak}), 200

    if challenge.last_check_in == today - timedelta(days=1):
        challenge.current_streak += 1
    elif challenge.last_check_in is None:
        challenge.current_streak = 1
    else:
        challenge.current_streak = 1

    new_record = challenge.current_streak > challenge.longest_streak
    if new_record:
        challenge.longest_streak = challenge.current_streak

    challenge.last_check_in = today
    db.session.commit()

    return jsonify({
        "message": "Check-in successful",
        "current_streak": challenge.current_streak,
        "longest_streak": challenge.longest_streak,
        "new_record": new_record
    }), 200

@app.route('/api/daily', methods=['GET'])
@jwt_required()
def get_daily():
    user_id = int(get_jwt_identity())
    user = db.session.get(User, user_id)
    if user is None:
        abort(404)

    today = date.today()
    today_str = today.isoformat()
    exercises = get_daily_exercises(user_id, today_str, user.skill_level)

    completed_keys = set(db.session.execute(
        db.select(DailyCompletion.exercise_key).where(
            DailyCompletion.user_id == user_id,
            DailyCompletion.date == today
        )
    ).scalars().all())

    return jsonify({
        "date": today_str,
        "skill_level": user.skill_level,
        "completed_count": len(completed_keys),
        "exercises": [
            {
                "key": ex['key'],
                "name": ex['name'],
                "category": ex['category'],
                "difficulty": ex['difficulty'],
                "reps_or_duration": ex['reps_or_duration'],
                "instructions": ex['instructions'],
                "completed": ex['key'] in completed_keys
            }
            for ex in exercises
        ]
    }), 200

@app.route('/api/daily/<string:exercise_key>/complete', methods=['POST'])
@jwt_required()
def complete_daily_exercise(exercise_key):
    user_id = int(get_jwt_identity())
    user = db.session.get(User, user_id)
    if user is None:
        abort(404)

    today = date.today()
    today_str = today.isoformat()

    valid_keys = {ex['key'] for ex in get_daily_exercises(user_id, today_str, user.skill_level)}
    if exercise_key not in valid_keys:
        return jsonify({"error": "Exercise not in today's daily list"}), 400

    existing = db.session.execute(
        db.select(DailyCompletion).where(
            DailyCompletion.user_id == user_id,
            DailyCompletion.date == today,
            DailyCompletion.exercise_key == exercise_key
        )
    ).scalar_one_or_none()

    if not existing:
        db.session.add(DailyCompletion(
            user_id=user_id,
            date=today,
            exercise_key=exercise_key
        ))
        db.session.commit()

    completed_count = db.session.execute(
        db.select(db.func.count(DailyCompletion.id)).where(
            DailyCompletion.user_id == user_id,
            DailyCompletion.date == today
        )
    ).scalar()

    return jsonify({
        "message": "Exercise completed",
        "exercise_key": exercise_key,
        "completed_count": completed_count
    }), 200


# --- JWT Error Handlers ---

@jwt.unauthorized_loader
def missing_token_callback(reason):
    return jsonify({"error": "Missing or invalid token"}), 401

@jwt.invalid_token_loader
def invalid_token_callback(error_string):
    return jsonify({"error": "Invalid token"}), 422

@jwt.expired_token_loader
def expired_token_callback(jwt_header, jwt_payload):
    return jsonify({"error": "Token has expired"}), 401


# --- Error Handlers ---

@app.errorhandler(429)
def ratelimit_exceeded(e):
    return jsonify({"error": "Too many requests. Please try again later."}), 429

@app.errorhandler(400)
def bad_request(e):
    return jsonify({"error": "Bad request"}), 400

@app.errorhandler(404)
def not_found(e):
    return jsonify({"error": "Not found"}), 404

@app.errorhandler(500)
def internal_error(e):
    return jsonify({"error": "Internal server error"}), 500


if __name__ == '__main__':
    app.run(host='0.0.0.0', port=int(os.environ.get('PORT', 5000)))
