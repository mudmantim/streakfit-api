import os
import hashlib
import json
import random
import string
import subprocess
import threading
from datetime import datetime, date, timedelta
from flask import Flask, request, jsonify, abort
from flask_sqlalchemy import SQLAlchemy
from flask_migrate import Migrate
from werkzeug.security import generate_password_hash, check_password_hash
from flask_jwt_extended import JWTManager, create_access_token, jwt_required, get_jwt_identity
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
import anthropic as _anthropic_lib

from scripts.verify_all import run_suite
from scripts.verification._client import WsgiClient
from scripts.verification import VERIFICATION_SUITE_VERSION

app = Flask(__name__)

# StreakFit Control / Mission Control (R3.0): a real proxy for "last
# deployment" -- each Render deploy starts a fresh process, so this
# process's own boot time is an honest stand-in for deploy time rather
# than an invented value.
_PROCESS_STARTED_AT = datetime.utcnow()

# Fallback to local SQLite only if Render's PostgreSQL URL isn't present
DATABASE_URL = os.environ.get('DATABASE_URL')
if DATABASE_URL and DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)

app.config['SQLALCHEMY_DATABASE_URI'] = DATABASE_URL or 'sqlite:///streakfit.db'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
# pool_pre_ping checks each connection before use so a connection the DB has
# silently closed (e.g. Neon's serverless auto-suspend) is replaced instead of
# failing the request; pool_recycle retires connections before that can happen.
app.config['SQLALCHEMY_ENGINE_OPTIONS'] = {
    'pool_pre_ping': True,
    'pool_recycle': 280,
}
_secret_key = os.environ.get('SECRET_KEY')
if not _secret_key:
    raise RuntimeError("SECRET_KEY environment variable is required but not set")
app.config['SECRET_KEY'] = _secret_key

_jwt_secret_key = os.environ.get('JWT_SECRET_KEY')
if not _jwt_secret_key:
    raise RuntimeError("JWT_SECRET_KEY environment variable is required but not set")
app.config['JWT_SECRET_KEY'] = _jwt_secret_key
app.config['JWT_ACCESS_TOKEN_EXPIRES'] = timedelta(hours=1)

_anthropic_api_key = os.environ.get('ANTHROPIC_API_KEY')

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

VALID_SKILL_LEVELS  = {'beginner', 'intermediate', 'advanced', 'custom'}
VALID_DISPLAY_MODES = {'classic', 'bright', 'game'}
VALID_RICKIE_MODES  = {'full', 'quiet', 'minimal'}

EXERCISE_LIBRARY = {
    'beginner': {
        'upper_body': [
            {'key': 'wall_push_up', 'name': 'Wall Push-Up', 'category': 'upper_body',
             'difficulty': 'beginner', 'reps_or_duration': '3 sets of 12 reps',
             'instructions': 'Stand arm\'s length from a wall with palms at shoulder height. Bend your elbows to bring your chest toward the wall, then push back to start.',
             'equipment': False, 'impact': 'low', 'space': 'small', 'family_friendly': True, 'fun_score': 'medium', 'requires_structure': True},
            {'key': 'knee_push_up', 'name': 'Knee Push-Up', 'category': 'upper_body',
             'difficulty': 'beginner', 'reps_or_duration': '3 sets of 10 reps',
             'instructions': 'Start in a plank on your hands and knees with a flat back. Lower your chest toward the floor, then press back up.',
             'equipment': False, 'impact': 'low', 'space': 'small', 'family_friendly': True, 'fun_score': 'medium', 'requires_structure': False},
            {'key': 'arm_circles', 'name': 'Arm Circles', 'category': 'upper_body',
             'difficulty': 'beginner', 'reps_or_duration': '3 sets of 20 reps each direction',
             'instructions': 'Stand with arms extended at shoulder height and make small continuous circles — 20 forward, then 20 backward.',
             'equipment': False, 'impact': 'none', 'space': 'small', 'family_friendly': True, 'fun_score': 'medium', 'requires_structure': False},
            {'key': 'shoulder_tap', 'name': 'Shoulder Tap', 'category': 'upper_body',
             'difficulty': 'beginner', 'reps_or_duration': '3 sets of 10 reps each side',
             'instructions': 'Hold a high plank. Keeping your hips square, lift one hand to tap the opposite shoulder, then alternate sides.',
             'equipment': False, 'impact': 'low', 'space': 'small', 'family_friendly': True, 'fun_score': 'medium', 'requires_structure': False},
            {'key': 'chest_opener', 'name': 'Standing Chest Opener', 'category': 'upper_body',
             'difficulty': 'beginner', 'reps_or_duration': '3 holds of 30 seconds',
             'instructions': 'Interlace your fingers behind your back, squeeze your shoulder blades together, and lift your chest. Hold for 30 seconds.',
             'equipment': False, 'impact': 'none', 'space': 'small', 'family_friendly': True, 'fun_score': 'low', 'requires_structure': False},
            {'key': 'floor_tricep_dip', 'name': 'Floor Tricep Dip', 'category': 'upper_body',
             'difficulty': 'beginner', 'reps_or_duration': '3 sets of 8 reps',
             'instructions': 'Sit on the floor with knees bent and feet flat. Place your hands on the floor beside your hips, fingers pointing forward. Lift your hips, then bend your elbows to lower them toward the floor and press back up.',
             'equipment': False, 'impact': 'low', 'space': 'small', 'family_friendly': True, 'fun_score': 'low', 'requires_structure': False},
        ],
        'lower_body': [
            {'key': 'bodyweight_squat', 'name': 'Bodyweight Squat', 'category': 'lower_body',
             'difficulty': 'beginner', 'reps_or_duration': '3 sets of 12 reps',
             'instructions': 'Stand feet shoulder-width apart. Push hips back and bend knees until thighs are parallel to the floor, then return to standing.',
             'equipment': False, 'impact': 'low', 'space': 'small', 'family_friendly': True, 'fun_score': 'medium', 'requires_structure': False},
            {'key': 'reverse_lunge', 'name': 'Reverse Lunge', 'category': 'lower_body',
             'difficulty': 'beginner', 'reps_or_duration': '3 sets of 8 reps each leg',
             'instructions': 'Step one foot back and lower the back knee toward the floor until both knees form 90-degree angles, then push through the front heel to return.',
             'equipment': False, 'impact': 'low', 'space': 'small', 'family_friendly': True, 'fun_score': 'medium', 'requires_structure': False},
            {'key': 'glute_bridge', 'name': 'Glute Bridge', 'category': 'lower_body',
             'difficulty': 'beginner', 'reps_or_duration': '3 sets of 15 reps',
             'instructions': 'Lie on your back with knees bent. Drive through your heels to lift your hips until your body forms a straight line from shoulders to knees.',
             'equipment': False, 'impact': 'none', 'space': 'small', 'family_friendly': True, 'fun_score': 'low', 'requires_structure': False},
            {'key': 'calf_raise', 'name': 'Standing Calf Raise', 'category': 'lower_body',
             'difficulty': 'beginner', 'reps_or_duration': '3 sets of 20 reps',
             'instructions': 'Stand feet hip-width apart and rise onto the balls of your feet as high as possible, pause at the top, then slowly lower.',
             'equipment': False, 'impact': 'low', 'space': 'small', 'family_friendly': True, 'fun_score': 'low', 'requires_structure': False},
            {'key': 'wall_sit', 'name': 'Wall Sit', 'category': 'lower_body',
             'difficulty': 'beginner', 'reps_or_duration': '3 sets of 20 seconds',
             'instructions': 'Slide down a wall until your thighs are parallel to the floor and hold, keeping your knees directly over your ankles.',
             'equipment': False, 'impact': 'none', 'space': 'small', 'family_friendly': True, 'fun_score': 'medium', 'requires_structure': True},
            {'key': 'step_up', 'name': 'Step-Up', 'category': 'lower_body',
             'difficulty': 'beginner', 'reps_or_duration': '3 sets of 10 reps each leg',
             'instructions': 'Step up onto a stair with one foot, bring the other foot up to meet it, then step back down. Alternate the leading leg each set.',
             'equipment': False, 'impact': 'low', 'space': 'small', 'family_friendly': True, 'fun_score': 'medium', 'requires_structure': True},
        ],
        'core': [
            {'key': 'dead_bug', 'name': 'Dead Bug', 'category': 'core',
             'difficulty': 'beginner', 'reps_or_duration': '3 sets of 8 reps each side',
             'instructions': 'Lie on your back with arms at the ceiling and knees at 90 degrees. Lower one arm and the opposite leg with your back pressed to the floor, then return and alternate.',
             'equipment': False, 'impact': 'none', 'space': 'small', 'family_friendly': True, 'fun_score': 'low', 'requires_structure': False},
            {'key': 'bird_dog', 'name': 'Bird Dog', 'category': 'core',
             'difficulty': 'beginner', 'reps_or_duration': '3 sets of 8 reps each side',
             'instructions': 'On hands and knees, extend one arm and the opposite leg until horizontal, hold briefly, then return and switch sides.',
             'equipment': False, 'impact': 'none', 'space': 'small', 'family_friendly': True, 'fun_score': 'low', 'requires_structure': False},
            {'key': 'knee_plank', 'name': 'Plank from Knees', 'category': 'core',
             'difficulty': 'beginner', 'reps_or_duration': '3 sets of 20 seconds',
             'instructions': 'Hold a forearm plank with knees on the floor, forming a straight line from head to knees. Keep your core tight and breathe steadily.',
             'equipment': False, 'impact': 'none', 'space': 'small', 'family_friendly': True, 'fun_score': 'low', 'requires_structure': False},
            {'key': 'crunch', 'name': 'Crunch', 'category': 'core',
             'difficulty': 'beginner', 'reps_or_duration': '3 sets of 15 reps',
             'instructions': 'Lie on your back with knees bent. Curl your shoulders off the floor by contracting your abs, then lower slowly.',
             'equipment': False, 'impact': 'none', 'space': 'small', 'family_friendly': True, 'fun_score': 'low', 'requires_structure': False},
            {'key': 'bent_knee_leg_raise', 'name': 'Bent-Knee Leg Raise', 'category': 'core',
             'difficulty': 'beginner', 'reps_or_duration': '3 sets of 12 reps',
             'instructions': 'Lie on your back with knees bent at 90 degrees and raised. Lower your feet toward the floor without touching, then lift back up. Keep your lower back pressed down.',
             'equipment': False, 'impact': 'none', 'space': 'small', 'family_friendly': True, 'fun_score': 'low', 'requires_structure': False},
            {'key': 'superman', 'name': 'Superman Hold', 'category': 'core',
             'difficulty': 'beginner', 'reps_or_duration': '3 sets of 10 reps',
             'instructions': 'Lie face down with arms extended overhead. Simultaneously lift your arms, chest, and legs off the floor, hold for a second, then lower.',
             'equipment': False, 'impact': 'none', 'space': 'small', 'family_friendly': True, 'fun_score': 'medium', 'requires_structure': False},
        ],
        'mobility': [
            {'key': 'cat_cow', 'name': 'Cat-Cow Stretch', 'category': 'mobility',
             'difficulty': 'beginner', 'reps_or_duration': '3 sets of 8 breath cycles',
             'instructions': 'On hands and knees, inhale and arch your back with head up (cow), then exhale and round your spine toward the ceiling (cat). Move slowly with your breath.',
             'equipment': False, 'impact': 'none', 'space': 'small', 'family_friendly': True, 'fun_score': 'medium', 'requires_structure': False},
            {'key': 'hip_flexor_kneeling', 'name': 'Kneeling Hip Flexor Stretch', 'category': 'mobility',
             'difficulty': 'beginner', 'reps_or_duration': '2 sets of 30 seconds each side',
             'instructions': 'Kneel with one foot forward. Shift your hips forward until you feel a stretch in the front of the kneeling-side hip. Keep your torso upright.',
             'equipment': False, 'impact': 'none', 'space': 'small', 'family_friendly': True, 'fun_score': 'low', 'requires_structure': False},
            {'key': 'standing_hamstring_stretch', 'name': 'Standing Hamstring Stretch', 'category': 'mobility',
             'difficulty': 'beginner', 'reps_or_duration': '2 sets of 30 seconds each leg',
             'instructions': 'Place one foot on a low surface and hinge forward at the hip with a flat back until you feel a stretch in the back of your thigh.',
             'equipment': False, 'impact': 'none', 'space': 'small', 'family_friendly': True, 'fun_score': 'low', 'requires_structure': True},
            {'key': 'childs_pose', 'name': "Child's Pose", 'category': 'mobility',
             'difficulty': 'beginner', 'reps_or_duration': '3 holds of 30 seconds',
             'instructions': 'Kneel and sit back on your heels, extend your arms forward on the floor, and rest your forehead down. Breathe deeply and let your hips sink.',
             'equipment': False, 'impact': 'none', 'space': 'small', 'family_friendly': True, 'fun_score': 'low', 'requires_structure': False},
            {'key': 'thoracic_rotation', 'name': 'Seated Thoracic Rotation', 'category': 'mobility',
             'difficulty': 'beginner', 'reps_or_duration': '3 sets of 8 reps each side',
             'instructions': 'Sit cross-legged with one hand behind your head. Rotate your upper body to bring that elbow back as far as comfortable, then return.',
             'equipment': False, 'impact': 'none', 'space': 'small', 'family_friendly': True, 'fun_score': 'low', 'requires_structure': False},
            {'key': 'ankle_circles', 'name': 'Ankle Circles', 'category': 'mobility',
             'difficulty': 'beginner', 'reps_or_duration': '3 sets of 10 circles each direction',
             'instructions': 'Lift one foot slightly and rotate the ankle in slow full circles. Complete all reps one direction then reverse, then switch feet.',
             'equipment': False, 'impact': 'none', 'space': 'small', 'family_friendly': True, 'fun_score': 'low', 'requires_structure': False},
        ],
        'conditioning': [
            {'key': 'marching_in_place', 'name': 'Marching in Place', 'category': 'conditioning',
             'difficulty': 'beginner', 'reps_or_duration': '3 sets of 30 seconds',
             'instructions': 'March in place lifting your knees to hip height with each step. Pump your arms in opposition and maintain an upright posture.',
             'equipment': False, 'impact': 'low', 'space': 'small', 'family_friendly': True, 'fun_score': 'medium', 'requires_structure': False},
            {'key': 'jumping_jack', 'name': 'Jumping Jack', 'category': 'conditioning',
             'difficulty': 'beginner', 'reps_or_duration': '3 sets of 20 reps',
             'instructions': 'Jump your feet out wide while raising your arms overhead, then jump back to start. Land softly with each rep.',
             'equipment': False, 'impact': 'high', 'space': 'small', 'family_friendly': True, 'fun_score': 'high', 'requires_structure': False},
            {'key': 'step_touch', 'name': 'Side Step Touch', 'category': 'conditioning',
             'difficulty': 'beginner', 'reps_or_duration': '3 sets of 30 seconds',
             'instructions': 'Step one foot out to the side then bring the other foot to meet it. Continue side to side at a brisk rhythmic pace.',
             'equipment': False, 'impact': 'low', 'space': 'small', 'family_friendly': True, 'fun_score': 'medium', 'requires_structure': False},
            {'key': 'standing_bicycle', 'name': 'Standing Bicycle Kick', 'category': 'conditioning',
             'difficulty': 'beginner', 'reps_or_duration': '3 sets of 20 reps',
             'instructions': 'Stand with hands behind your head. Lift one knee while twisting the opposite elbow toward it, then alternate sides in a smooth motion.',
             'equipment': False, 'impact': 'low', 'space': 'small', 'family_friendly': True, 'fun_score': 'medium', 'requires_structure': False},
            {'key': 'low_skip', 'name': 'Low-Impact Skip', 'category': 'conditioning',
             'difficulty': 'beginner', 'reps_or_duration': '3 sets of 30 seconds',
             'instructions': 'Skip in place with a low controlled hop on each foot. Keep the impact light and swing your arms comfortably.',
             'equipment': False, 'impact': 'low', 'space': 'small', 'family_friendly': True, 'fun_score': 'high', 'requires_structure': False},
            {'key': 'boxer_shuffle', 'name': 'Boxer Shuffle', 'category': 'conditioning',
             'difficulty': 'beginner', 'reps_or_duration': '3 sets of 30 seconds',
             'instructions': 'Bounce lightly from foot to foot with knees slightly bent, keeping the movement small and rhythmic as if skipping rope without a rope.',
             'equipment': False, 'impact': 'low', 'space': 'small', 'family_friendly': True, 'fun_score': 'high', 'requires_structure': False},
        ],
    },
    'intermediate': {
        'upper_body': [
            {'key': 'push_up', 'name': 'Push-Up', 'category': 'upper_body',
             'difficulty': 'intermediate', 'reps_or_duration': '4 sets of 12 reps',
             'instructions': 'Start in a high plank with hands shoulder-width apart. Lower your chest to just above the floor keeping elbows at 45 degrees, then press back up with full arm extension.',
             'equipment': False, 'impact': 'low', 'space': 'small', 'family_friendly': True, 'fun_score': 'medium', 'requires_structure': False},
            {'key': 'diamond_push_up', 'name': 'Diamond Push-Up', 'category': 'upper_body',
             'difficulty': 'intermediate', 'reps_or_duration': '3 sets of 10 reps',
             'instructions': 'Form a diamond with your thumbs and index fingers on the floor beneath your chest. Perform a push-up keeping your elbows close to your body.',
             'equipment': False, 'impact': 'low', 'space': 'small', 'family_friendly': True, 'fun_score': 'medium', 'requires_structure': False},
            {'key': 'pike_push_up', 'name': 'Pike Push-Up', 'category': 'upper_body',
             'difficulty': 'intermediate', 'reps_or_duration': '3 sets of 10 reps',
             'instructions': 'Start in downward dog with hips high. Bend your elbows to lower the crown of your head toward the floor, then press back up. This targets the shoulders.',
             'equipment': False, 'impact': 'low', 'space': 'small', 'family_friendly': True, 'fun_score': 'medium', 'requires_structure': False},
            {'key': 'decline_push_up', 'name': 'Decline Push-Up', 'category': 'upper_body',
             'difficulty': 'intermediate', 'reps_or_duration': '3 sets of 10 reps',
             'instructions': 'Place your feet on an elevated surface and hands on the floor. Perform a push-up keeping your body in a straight line throughout.',
             'equipment': False, 'impact': 'low', 'space': 'small', 'family_friendly': True, 'fun_score': 'medium', 'requires_structure': True},
            {'key': 'wide_push_up', 'name': 'Wide-Grip Push-Up', 'category': 'upper_body',
             'difficulty': 'intermediate', 'reps_or_duration': '3 sets of 12 reps',
             'instructions': 'Place your hands wider than shoulder-width and perform a push-up, allowing elbows to flare to the sides to emphasise the chest.',
             'equipment': False, 'impact': 'low', 'space': 'small', 'family_friendly': True, 'fun_score': 'medium', 'requires_structure': False},
            {'key': 'sphinx_push_up', 'name': 'Sphinx Push-Up', 'category': 'upper_body',
             'difficulty': 'intermediate', 'reps_or_duration': '3 sets of 10 reps',
             'instructions': 'Start in a forearm plank. Press into your palms to extend one arm then the other until you reach a straight-arm plank, then lower back down one forearm at a time. Keep your hips level throughout.',
             'equipment': False, 'impact': 'low', 'space': 'small', 'family_friendly': True, 'fun_score': 'low', 'requires_structure': False},
        ],
        'lower_body': [
            {'key': 'jump_squat', 'name': 'Jump Squat', 'category': 'lower_body',
             'difficulty': 'intermediate', 'reps_or_duration': '4 sets of 10 reps',
             'instructions': 'Lower into a squat, then explode upward into a jump. Land softly with knees slightly bent and immediately sink into the next squat.',
             'equipment': False, 'impact': 'high', 'space': 'small', 'family_friendly': True, 'fun_score': 'high', 'requires_structure': False},
            {'key': 'walking_lunge', 'name': 'Walking Lunge', 'category': 'lower_body',
             'difficulty': 'intermediate', 'reps_or_duration': '3 sets of 12 reps each leg',
             'instructions': 'Step forward into a lunge lowering the back knee toward the floor, then push through the front heel and step the rear foot forward into the next rep.',
             'equipment': False, 'impact': 'low', 'space': 'medium', 'family_friendly': True, 'fun_score': 'medium', 'requires_structure': False},
            {'key': 'single_leg_glute_bridge', 'name': 'Single-Leg Glute Bridge', 'category': 'lower_body',
             'difficulty': 'intermediate', 'reps_or_duration': '3 sets of 12 reps each leg',
             'instructions': 'Lie on your back with one knee bent and the other leg extended. Drive through the planted heel to raise your hips until your body forms a straight diagonal.',
             'equipment': False, 'impact': 'none', 'space': 'small', 'family_friendly': True, 'fun_score': 'low', 'requires_structure': False},
            {'key': 'lateral_lunge', 'name': 'Lateral Lunge', 'category': 'lower_body',
             'difficulty': 'intermediate', 'reps_or_duration': '3 sets of 10 reps each side',
             'instructions': 'Step wide to one side, bend that knee and push the hip back while keeping the other leg straight, then push back to standing.',
             'equipment': False, 'impact': 'low', 'space': 'small', 'family_friendly': True, 'fun_score': 'medium', 'requires_structure': False},
            {'key': 'sumo_squat', 'name': 'Sumo Squat', 'category': 'lower_body',
             'difficulty': 'intermediate', 'reps_or_duration': '4 sets of 15 reps',
             'instructions': 'Stand with feet wider than shoulder-width and toes out. Squat deep keeping your torso upright and knees tracking over your toes.',
             'equipment': False, 'impact': 'low', 'space': 'small', 'family_friendly': True, 'fun_score': 'medium', 'requires_structure': False},
            {'key': 'bodyweight_good_morning', 'name': 'Bodyweight Good Morning', 'category': 'lower_body',
             'difficulty': 'intermediate', 'reps_or_duration': '3 sets of 12 reps',
             'instructions': 'Stand with feet hip-width apart and hands clasped behind your head. With a slight bend in your knees, hinge forward at the hips until your torso is nearly parallel to the floor, then drive your hips forward to return. Keep your back flat throughout.',
             'equipment': False, 'impact': 'low', 'space': 'small', 'family_friendly': True, 'fun_score': 'low', 'requires_structure': False},
        ],
        'core': [
            {'key': 'plank', 'name': 'Plank', 'category': 'core',
             'difficulty': 'intermediate', 'reps_or_duration': '3 sets of 45 seconds',
             'instructions': 'Hold a push-up position with a straight line from head to heels. Engage your abs, glutes, and quads without letting your hips sag or rise.',
             'equipment': False, 'impact': 'none', 'space': 'small', 'family_friendly': True, 'fun_score': 'low', 'requires_structure': False},
            {'key': 'hollow_body_hold', 'name': 'Hollow Body Hold', 'category': 'core',
             'difficulty': 'intermediate', 'reps_or_duration': '3 sets of 20 seconds',
             'instructions': 'Lie on your back, press your lower back firmly to the floor, and lift arms overhead and legs a few inches. Hold this curved dish shape.',
             'equipment': False, 'impact': 'none', 'space': 'small', 'family_friendly': True, 'fun_score': 'low', 'requires_structure': False},
            {'key': 'russian_twist', 'name': 'Russian Twist', 'category': 'core',
             'difficulty': 'intermediate', 'reps_or_duration': '3 sets of 20 reps',
             'instructions': 'Sit with knees bent and lean back slightly. Clasp your hands and rotate your torso left and right, touching the floor on each side.',
             'equipment': False, 'impact': 'none', 'space': 'small', 'family_friendly': True, 'fun_score': 'medium', 'requires_structure': False},
            {'key': 'bicycle_crunch', 'name': 'Bicycle Crunch', 'category': 'core',
             'difficulty': 'intermediate', 'reps_or_duration': '3 sets of 20 reps',
             'instructions': 'On your back with hands behind your head, bring one knee to your chest while rotating the opposite elbow toward it. Alternate in a pedalling motion.',
             'equipment': False, 'impact': 'none', 'space': 'small', 'family_friendly': True, 'fun_score': 'medium', 'requires_structure': False},
            {'key': 'straight_leg_raise', 'name': 'Straight-Leg Raise', 'category': 'core',
             'difficulty': 'intermediate', 'reps_or_duration': '3 sets of 15 reps',
             'instructions': 'Lie on your back with legs straight. Lift both legs to 90 degrees, then lower slowly without letting them touch the floor.',
             'equipment': False, 'impact': 'none', 'space': 'small', 'family_friendly': True, 'fun_score': 'low', 'requires_structure': False},
            {'key': 'side_plank', 'name': 'Side Plank', 'category': 'core',
             'difficulty': 'intermediate', 'reps_or_duration': '3 sets of 30 seconds each side',
             'instructions': 'Push up onto your forearm and the edge of your foot. Keep your body in a straight line with hips lifted, then switch sides.',
             'equipment': False, 'impact': 'none', 'space': 'small', 'family_friendly': True, 'fun_score': 'low', 'requires_structure': False},
        ],
        'mobility': [
            {'key': 'worlds_greatest_stretch', 'name': "World's Greatest Stretch", 'category': 'mobility',
             'difficulty': 'intermediate', 'reps_or_duration': '3 sets of 5 reps each side',
             'instructions': 'Step into a deep lunge, place the same-side hand on the floor, then rotate the top arm toward the ceiling. Shift into a hamstring stretch, then repeat on the other side.',
             'equipment': False, 'impact': 'none', 'space': 'medium', 'family_friendly': True, 'fun_score': 'medium', 'requires_structure': False},
            {'key': 'deep_squat_hold', 'name': 'Deep Squat Hold', 'category': 'mobility',
             'difficulty': 'intermediate', 'reps_or_duration': '3 holds of 45 seconds',
             'instructions': 'Squat with feet shoulder-width apart and heels on the floor. Use your elbows to gently push your knees out and hold a tall, upright torso.',
             'equipment': False, 'impact': 'none', 'space': 'small', 'family_friendly': True, 'fun_score': 'low', 'requires_structure': False},
            {'key': 'pigeon_pose', 'name': 'Pigeon Pose', 'category': 'mobility',
             'difficulty': 'intermediate', 'reps_or_duration': '2 holds of 60 seconds each side',
             'instructions': 'From a plank, bring one knee toward your wrist and let the shin rest at an angle. Lower your hips and walk your hands forward to deepen the stretch.',
             'equipment': False, 'impact': 'none', 'space': 'small', 'family_friendly': True, 'fun_score': 'low', 'requires_structure': False},
            {'key': 'spinal_twist', 'name': 'Supine Spinal Twist', 'category': 'mobility',
             'difficulty': 'intermediate', 'reps_or_duration': '2 holds of 45 seconds each side',
             'instructions': 'Lie on your back, draw one knee to your chest, then guide it across your body to the floor while extending the opposite arm out.',
             'equipment': False, 'impact': 'none', 'space': 'small', 'family_friendly': True, 'fun_score': 'low', 'requires_structure': False},
            {'key': 'doorway_pec_stretch', 'name': 'Doorway Chest Stretch', 'category': 'mobility',
             'difficulty': 'intermediate', 'reps_or_duration': '3 holds of 30 seconds',
             'instructions': 'Place your forearms on a doorframe at shoulder height and lean gently forward until you feel a stretch across your chest and shoulders.',
             'equipment': False, 'impact': 'none', 'space': 'small', 'family_friendly': True, 'fun_score': 'low', 'requires_structure': True},
            {'key': 'downdog_calf_stretch', 'name': 'Downward Dog Calf Stretch', 'category': 'mobility',
             'difficulty': 'intermediate', 'reps_or_duration': '3 sets of 5 reps each leg',
             'instructions': 'In downward dog, press one heel toward the floor and hold 2 seconds, then alternate feet in a gentle pedalling motion for the full count.',
             'equipment': False, 'impact': 'none', 'space': 'small', 'family_friendly': True, 'fun_score': 'low', 'requires_structure': False},
        ],
        'conditioning': [
            {'key': 'no_jump_burpee', 'name': 'No-Jump Burpee', 'category': 'conditioning',
             'difficulty': 'intermediate', 'reps_or_duration': '3 sets of 10 reps',
             'instructions': 'From standing, place hands on the floor, step feet back to a plank, do a push-up, step feet forward, and stand back up. No jump at the top.',
             'equipment': False, 'impact': 'low', 'space': 'small', 'family_friendly': True, 'fun_score': 'medium', 'requires_structure': False},
            {'key': 'mountain_climber', 'name': 'Mountain Climber', 'category': 'conditioning',
             'difficulty': 'intermediate', 'reps_or_duration': '3 sets of 20 reps',
             'instructions': 'In a high plank, drive one knee toward your chest then quickly switch legs. Continue alternating at a fast pace while keeping your hips level.',
             'equipment': False, 'impact': 'low', 'space': 'small', 'family_friendly': True, 'fun_score': 'medium', 'requires_structure': False},
            {'key': 'high_knees', 'name': 'High Knees', 'category': 'conditioning',
             'difficulty': 'intermediate', 'reps_or_duration': '3 sets of 30 seconds',
             'instructions': 'Run in place driving your knees up to hip height. Pump your arms and land on the balls of your feet at a fast rhythmic pace.',
             'equipment': False, 'impact': 'high', 'space': 'small', 'family_friendly': True, 'fun_score': 'high', 'requires_structure': False},
            {'key': 'skater_jump', 'name': 'Skater Jump', 'category': 'conditioning',
             'difficulty': 'intermediate', 'reps_or_duration': '3 sets of 15 reps each side',
             'instructions': 'Leap laterally from one foot to the other, landing softly with a slight knee bend. Swing your arms for momentum like a speed skater.',
             'equipment': False, 'impact': 'high', 'space': 'medium', 'family_friendly': True, 'fun_score': 'high', 'requires_structure': False},
            {'key': 'plank_to_downdog', 'name': 'Plank to Downward Dog', 'category': 'conditioning',
             'difficulty': 'intermediate', 'reps_or_duration': '3 sets of 10 reps',
             'instructions': 'From a high plank, push your hips up and back into downward dog, hold briefly, then flow back to plank. Coordinate each movement with your breath.',
             'equipment': False, 'impact': 'low', 'space': 'small', 'family_friendly': True, 'fun_score': 'medium', 'requires_structure': False},
            {'key': 'speed_squat', 'name': 'Speed Squat', 'category': 'conditioning',
             'difficulty': 'intermediate', 'reps_or_duration': '4 sets of 15 reps',
             'instructions': 'Perform bodyweight squats as quickly as possible while maintaining good form. Reach at least parallel on every rep and fully extend at the top.',
             'equipment': False, 'impact': 'low', 'space': 'small', 'family_friendly': True, 'fun_score': 'medium', 'requires_structure': False},
        ],
    },
    'advanced': {
        'upper_body': [
            {'key': 'archer_push_up', 'name': 'Archer Push-Up', 'category': 'upper_body',
             'difficulty': 'advanced', 'reps_or_duration': '4 sets of 8 reps each side',
             'instructions': 'In a wide push-up stance, lower toward one hand while extending the other arm straight out to the side. Push up and repeat on the opposite side.',
             'equipment': False, 'impact': 'low', 'space': 'small', 'family_friendly': True, 'fun_score': 'high', 'requires_structure': False},
            {'key': 'pseudo_planche_push_up', 'name': 'Pseudo Planche Push-Up', 'category': 'upper_body',
             'difficulty': 'advanced', 'reps_or_duration': '4 sets of 8 reps',
             'instructions': 'Place hands facing backward at hip level and lean your shoulders forward past your wrists. Perform a push-up maintaining this extreme forward lean.',
             'equipment': False, 'impact': 'low', 'space': 'small', 'family_friendly': False, 'fun_score': 'medium', 'requires_structure': False},
            {'key': 'typewriter_push_up', 'name': 'Typewriter Push-Up', 'category': 'upper_body',
             'difficulty': 'advanced', 'reps_or_duration': '3 sets of 6 reps each side',
             'instructions': 'Lower into the bottom of a wide push-up, then shift your weight horizontally across to one side before pressing up on that arm. Alternate sides each rep.',
             'equipment': False, 'impact': 'low', 'space': 'small', 'family_friendly': True, 'fun_score': 'high', 'requires_structure': False},
            {'key': 'plyometric_push_up', 'name': 'Plyometric Push-Up', 'category': 'upper_body',
             'difficulty': 'advanced', 'reps_or_duration': '4 sets of 8 reps',
             'instructions': 'Perform a push-up with enough force to launch your hands off the floor. Land with soft elbows and immediately lower into the next rep.',
             'equipment': False, 'impact': 'high', 'space': 'small', 'family_friendly': True, 'fun_score': 'high', 'requires_structure': False},
            {'key': 'wall_handstand_hold', 'name': 'Wall Handstand Hold', 'category': 'upper_body',
             'difficulty': 'advanced', 'reps_or_duration': '3 holds of 20 seconds',
             'instructions': 'Kick up into a handstand against a wall. Stack wrists, elbows, and shoulders vertically. Engage your core and glutes, pressing the floor away with your fingertips.',
             'equipment': False, 'impact': 'low', 'space': 'small', 'family_friendly': False, 'fun_score': 'high', 'requires_structure': True},
            {'key': 'assisted_one_arm_push_up', 'name': 'Assisted One-Arm Push-Up', 'category': 'upper_body',
             'difficulty': 'advanced', 'reps_or_duration': '3 sets of 5 reps each side',
             'instructions': 'Perform a push-up on one hand while resting the other on a low support. Lower with control keeping your body square, then press back up.',
             'equipment': False, 'impact': 'low', 'space': 'small', 'family_friendly': True, 'fun_score': 'high', 'requires_structure': True},
        ],
        'lower_body': [
            {'key': 'assisted_pistol_squat', 'name': 'Assisted Pistol Squat', 'category': 'lower_body',
             'difficulty': 'advanced', 'reps_or_duration': '3 sets of 6 reps each leg',
             'instructions': 'Hold a support for balance and stand on one leg with the other extended forward. Slowly squat as deep as possible on the standing leg, then drive back up.',
             'equipment': False, 'impact': 'low', 'space': 'small', 'family_friendly': True, 'fun_score': 'high', 'requires_structure': True},
            {'key': 'plyometric_lunge', 'name': 'Plyometric Lunge', 'category': 'lower_body',
             'difficulty': 'advanced', 'reps_or_duration': '4 sets of 10 reps each leg',
             'instructions': 'Lower into a lunge, then explode off both feet to switch leg positions in mid-air. Land softly in a lunge with the opposite leg forward and continue.',
             'equipment': False, 'impact': 'high', 'space': 'medium', 'family_friendly': True, 'fun_score': 'high', 'requires_structure': False},
            {'key': 'single_leg_good_morning', 'name': 'Single-Leg Good Morning', 'category': 'lower_body',
             'difficulty': 'advanced', 'reps_or_duration': '4 sets of 8 reps each leg',
             'instructions': 'Stand on one leg with a soft bend in the knee and hands clasped behind your head. Hinge forward at the hip until your torso is parallel to the floor with the free leg extending behind you, then drive your hips forward to return. Switch legs each set.',
             'equipment': False, 'impact': 'low', 'space': 'small', 'family_friendly': True, 'fun_score': 'low', 'requires_structure': False},
            {'key': 'shrimp_squat', 'name': 'Shrimp Squat', 'category': 'lower_body',
             'difficulty': 'advanced', 'reps_or_duration': '3 sets of 5 reps each leg',
             'instructions': 'Stand on one leg and hold the other foot behind you. Slowly lower your back knee toward the floor in a controlled single-leg squat, then drive back up.',
             'equipment': False, 'impact': 'low', 'space': 'small', 'family_friendly': True, 'fun_score': 'medium', 'requires_structure': False},
            {'key': 'broad_jump', 'name': 'Broad Jump', 'category': 'lower_body',
             'difficulty': 'advanced', 'reps_or_duration': '4 sets of 5 reps',
             'instructions': 'Swing your arms back, bend your knees, then explode forward as far as possible. Land with soft knees and absorb the impact through a full squat.',
             'equipment': False, 'impact': 'high', 'space': 'large', 'family_friendly': True, 'fun_score': 'high', 'requires_structure': False},
            {'key': 'sprint_intervals', 'name': 'Sprint Intervals', 'category': 'lower_body',
             'difficulty': 'advanced', 'reps_or_duration': '8 rounds of 20 seconds on / 10 seconds off',
             'instructions': 'Sprint at maximum effort for 20 seconds then rest 10 seconds. This is a Tabata protocol — complete all 8 rounds without reducing intensity.',
             'equipment': False, 'impact': 'high', 'space': 'large', 'family_friendly': True, 'fun_score': 'high', 'requires_structure': False},
        ],
        'core': [
            {'key': 'straddle_v_up', 'name': 'Straddle V-Up', 'category': 'core',
             'difficulty': 'advanced', 'reps_or_duration': '3 sets of 8 reps',
             'instructions': 'Lie on your back with arms extended overhead and legs spread wide. Simultaneously raise your torso and legs, reaching your hands toward your feet at the top, then lower with full control. Keep the descent slow — 3 seconds down.',
             'equipment': False, 'impact': 'none', 'space': 'small', 'family_friendly': True, 'fun_score': 'medium', 'requires_structure': False},
            {'key': 'l_sit_hold', 'name': 'Floor L-Sit', 'category': 'core',
             'difficulty': 'advanced', 'reps_or_duration': '3 holds of 10 seconds',
             'instructions': 'Sit on the floor with legs extended. Place your hands flat beside your hips and press down hard to lift your entire body off the floor. Hold with legs straight and parallel to the ground. Tuck your knees if you cannot yet hold them straight.',
             'equipment': False, 'impact': 'none', 'space': 'small', 'family_friendly': False, 'fun_score': 'medium', 'requires_structure': False},
            {'key': 'pike_walk_out', 'name': 'Pike Walk-Out', 'category': 'core',
             'difficulty': 'advanced', 'reps_or_duration': '3 sets of 8 reps',
             'instructions': 'Stand with feet hip-width apart. Walk your hands down your legs and along the floor, extending forward until your body forms a straight plank. Pause, then walk your hands back and return to standing. Keep your core braced throughout.',
             'equipment': False, 'impact': 'low', 'space': 'small', 'family_friendly': True, 'fun_score': 'medium', 'requires_structure': False},
            {'key': 'tuck_to_straight_leg_raise', 'name': 'Tuck-to-Straight Leg Raise', 'category': 'core',
             'difficulty': 'advanced', 'reps_or_duration': '4 sets of 10 reps',
             'instructions': 'Lie on your back with arms extended overhead pressing into the floor. Pull your knees to your chest in a tuck, then extend your legs straight at the top. Lower both straight legs slowly to just above the floor without touching. Keep your lower back pressed down throughout.',
             'equipment': False, 'impact': 'none', 'space': 'small', 'family_friendly': True, 'fun_score': 'medium', 'requires_structure': False},
            {'key': 'hollow_body_rock', 'name': 'Hollow Body Rock', 'category': 'core',
             'difficulty': 'advanced', 'reps_or_duration': '3 sets of 30 seconds',
             'instructions': 'Hold a hollow body position and rock forward and backward in a controlled arc. Your lower back must stay rounded throughout — any arch means you have lost the position.',
             'equipment': False, 'impact': 'none', 'space': 'small', 'family_friendly': True, 'fun_score': 'medium', 'requires_structure': False},
            {'key': 'planche_lean', 'name': 'Planche Lean', 'category': 'core',
             'difficulty': 'advanced', 'reps_or_duration': '3 holds of 20 seconds',
             'instructions': 'Start in a plank on straight arms. Gradually shift your weight forward over your wrists keeping your body completely rigid. The further forward, the harder.',
             'equipment': False, 'impact': 'none', 'space': 'small', 'family_friendly': False, 'fun_score': 'medium', 'requires_structure': False},
        ],
        'mobility': [
            {'key': 'pancake_stretch', 'name': 'Pancake Stretch', 'category': 'mobility',
             'difficulty': 'advanced', 'reps_or_duration': '3 holds of 60 seconds',
             'instructions': 'Sit in a wide straddle and hinge forward from the hips with a flat back, walking your hands along the floor. Relax and breathe deeply into the stretch — do not force it.',
             'equipment': False, 'impact': 'none', 'space': 'small', 'family_friendly': True, 'fun_score': 'low', 'requires_structure': False},
            {'key': 'bodyweight_jefferson_curl', 'name': 'Jefferson Curl (Bodyweight)', 'category': 'mobility',
             'difficulty': 'advanced', 'reps_or_duration': '3 sets of 8 slow reps',
             'instructions': 'Stand with feet together. Starting from the top of your head, curl each vertebra forward one at a time — chin to chest, then upper back, then lower back — until you are fully hanging with arms dangling. Uncurl slowly from the base of the spine upward. Take 5 seconds each direction.',
             'equipment': False, 'impact': 'none', 'space': 'small', 'family_friendly': True, 'fun_score': 'medium', 'requires_structure': False},
            {'key': 'front_split_prep', 'name': 'Front Split Progression', 'category': 'mobility',
             'difficulty': 'advanced', 'reps_or_duration': '3 holds of 60 seconds each side',
             'instructions': 'Kneel in a low lunge and slide your front foot forward, using your hands on the floor for support. Sink as deep as your flexibility allows and breathe steadily.',
             'equipment': False, 'impact': 'none', 'space': 'small', 'family_friendly': True, 'fun_score': 'medium', 'requires_structure': False},
            {'key': 'cossack_squat', 'name': 'Cossack Squat', 'category': 'mobility',
             'difficulty': 'advanced', 'reps_or_duration': '3 sets of 8 reps each side',
             'instructions': 'Stand in a wide stance, shift your weight to one leg and squat deep while extending the other leg straight to the side. Alternate sides with full control.',
             'equipment': False, 'impact': 'low', 'space': 'medium', 'family_friendly': True, 'fun_score': 'medium', 'requires_structure': False},
            {'key': 'shoulder_cars', 'name': 'Shoulder CARs', 'category': 'mobility',
             'difficulty': 'advanced', 'reps_or_duration': '3 sets of 5 reps each shoulder',
             'instructions': 'Stand tall and pin one arm firmly against your side. With the free arm, rotate the shoulder through its full active range — lead with the thumb forward and up overhead, then internally rotate as the arm sweeps behind your body and back to start. Move deliberately through every degree of range you control. Switch arms.',
             'equipment': False, 'impact': 'none', 'space': 'small', 'family_friendly': True, 'fun_score': 'low', 'requires_structure': False},
            {'key': 'wrist_prep', 'name': 'Wrist Mobility Routine', 'category': 'mobility',
             'difficulty': 'advanced', 'reps_or_duration': '3 sets of 10 reps each movement',
             'instructions': 'On all fours, perform: wrist circles both directions, forward and backward finger circles, and loaded wrist stretches. Essential prep for handstand and planche work.',
             'equipment': False, 'impact': 'none', 'space': 'small', 'family_friendly': True, 'fun_score': 'low', 'requires_structure': False},
        ],
        'conditioning': [
            {'key': 'full_burpee', 'name': 'Full Burpee', 'category': 'conditioning',
             'difficulty': 'advanced', 'reps_or_duration': '4 sets of 15 reps',
             'instructions': 'From standing, drop to a squat, kick back to a plank, do a push-up, jump feet to hands, then explode upward with arms overhead. Land softly and repeat immediately.',
             'equipment': False, 'impact': 'high', 'space': 'small', 'family_friendly': True, 'fun_score': 'high', 'requires_structure': False},
            {'key': 'tabata_mountain_climber', 'name': 'Tabata Mountain Climbers', 'category': 'conditioning',
             'difficulty': 'advanced', 'reps_or_duration': '8 rounds of 20 seconds on / 10 seconds off',
             'instructions': 'Perform mountain climbers at maximum speed for 20 seconds, then rest exactly 10 seconds. Complete all 8 rounds for a full 4-minute Tabata protocol.',
             'equipment': False, 'impact': 'low', 'space': 'small', 'family_friendly': True, 'fun_score': 'high', 'requires_structure': False},
            {'key': 'tuck_jump', 'name': 'Tuck Jump', 'category': 'conditioning',
             'difficulty': 'advanced', 'reps_or_duration': '4 sets of 8 reps',
             'instructions': 'Dip into a quarter squat then explode upward as high as possible, pulling both knees toward your chest at the peak. Land softly on the balls of your feet with knees bent to absorb the impact and immediately reset for the next rep.',
             'equipment': False, 'impact': 'high', 'space': 'small', 'family_friendly': True, 'fun_score': 'high', 'requires_structure': False},
            {'key': 'tuck_jump_burpee', 'name': 'Tuck Jump Burpee', 'category': 'conditioning',
             'difficulty': 'advanced', 'reps_or_duration': '3 sets of 8 reps',
             'instructions': 'From standing, place hands on the floor, kick back to a plank, perform a push-up, jump feet forward, then explode upward pulling both knees to your chest at the peak. Land softly with bent knees and flow immediately into the next rep.',
             'equipment': False, 'impact': 'high', 'space': 'small', 'family_friendly': True, 'fun_score': 'high', 'requires_structure': False},
            {'key': 'broad_jump_consecutive', 'name': 'Consecutive Broad Jumps', 'category': 'conditioning',
             'difficulty': 'advanced', 'reps_or_duration': '4 sets of 5 jumps',
             'instructions': 'Perform 5 broad jumps in sequence without pausing between them. Land and immediately load into the next jump, maintaining maximum power throughout.',
             'equipment': False, 'impact': 'high', 'space': 'large', 'family_friendly': True, 'fun_score': 'high', 'requires_structure': False},
            {'key': 'shuttle_run', 'name': 'Shuttle Run', 'category': 'conditioning',
             'difficulty': 'advanced', 'reps_or_duration': '5 rounds of 10m x 4 lengths',
             'instructions': 'Sprint 10 meters to a marker, touch it, sprint back, and repeat for 4 lengths per round. Rest 45 seconds between rounds. Focus on explosive direction changes.',
             'equipment': False, 'impact': 'high', 'space': 'large', 'family_friendly': True, 'fun_score': 'high', 'requires_structure': False},
        ],
    },
}


_GENERATOR_MAX_RETRIES = 20
_CATEGORIES = ('upper_body', 'lower_body', 'core', 'mobility', 'conditioning')

# --- Insight Library ---
# One insight per calendar day, same for all users. Indexed by day-of-year mod length.
# 90 insights covering 9 categories: Movement, Health, Recovery, Sleep,
# Balance, Flexibility, Habits, Family, Fun Facts.

INSIGHT_LIBRARY = [
    {'category': 'MOVEMENT',
     'text': "A ten-minute walk can lift your mood for the rest of the day."},
    {'category': 'MOVEMENT',
     'text': "Standing up and moving for just a minute or two every hour can add up to real benefits across a day."},
    {'category': 'MOVEMENT',
     'text': "Stretching your arms overhead first thing in the morning can help wake up your whole body."},
    {'category': 'MOVEMENT',
     'text': "Dancing around your living room counts as real exercise, not just fun."},
    {'category': 'MOVEMENT',
     'text': "Climbing stairs works more muscles at once than almost any other everyday movement."},
    {'category': 'MOVEMENT',
     'text': "Your body uses energy just to keep you standing, even when you're not moving at all."},
    {'category': 'MOVEMENT',
     'text': "Swinging your arms while you walk helps you move faster with less effort."},
    {'category': 'MOVEMENT',
     'text': "A short walk after a meal can help your body handle that food better."},
    {'category': 'MOVEMENT',
     'text': "Playing tag or chasing a ball is a full workout disguised as a game."},
    {'category': 'MOVEMENT',
     'text': "Moving around in the morning can wake you up faster than a cup of coffee."},
    {'category': 'HEALTH',
     'text': "Drinking water when you're thirsty is usually enough — you don't need to count exact glasses."},
    {'category': 'HEALTH',
     'text': "Laughing hard actually gives your stomach and chest muscles a small workout."},
    {'category': 'HEALTH',
     'text': "Your heart is roughly the size of your fist and beats about 100,000 times a day."},
    {'category': 'HEALTH',
     'text': "Eating slowly gives your body more time to realize when it's full."},
    {'category': 'HEALTH',
     'text': "Washing your hands well is one of the simplest ways to stay healthy."},
    {'category': 'HEALTH',
     'text': "Eating fruits and vegetables of different colors usually means you're getting different vitamins."},
    {'category': 'HEALTH',
     'text': "A short walk outside can ease stress faster than sitting still and worrying about it."},
    {'category': 'HEALTH',
     'text': "Your skin is your body's largest organ, and it repairs itself every day."},
    {'category': 'HEALTH',
     'text': "Feeling tired sometimes just means your body needs water, not caffeine."},
    {'category': 'HEALTH',
     'text': "Spending time outside in daylight helps your body know when it's day and when it's night."},
    {'category': 'RECOVERY',
     'text': "Sore muscles after exercise are usually a sign of repair, not damage."},
    {'category': 'RECOVERY',
     'text': "Resting after a hard workout is when your body actually gets stronger."},
    {'category': 'RECOVERY',
     'text': "A short walk on a rest day can help sore muscles feel better than sitting still all day."},
    {'category': 'RECOVERY',
     'text': "Muscle soreness usually peaks a day or two after exercise, not right away."},
    {'category': 'RECOVERY',
     'text': "Gentle stretching after exercise can help your muscles feel less tight the next day."},
    {'category': 'RECOVERY',
     'text': "Taking a full day off once in a while helps your body keep up with everything else you do."},
    {'category': 'RECOVERY',
     'text': "Warm water on tired muscles can help them relax faster."},
    {'category': 'RECOVERY',
     'text': "Your body repairs muscle while you rest, not while you're still exercising."},
    {'category': 'RECOVERY',
     'text': "Feeling tired after a workout is normal — feeling sharp pain is not."},
    {'category': 'RECOVERY',
     'text': "Giving your body a break after a busy week helps it come back stronger for the next one."},
    {'category': 'SLEEP',
     'text': "Most of your body's repair work happens while you're asleep."},
    {'category': 'SLEEP',
     'text': "A consistent bedtime can matter more than the exact number of hours you sleep."},
    {'category': 'SLEEP',
     'text': "Bright screens before bed can trick your brain into thinking it's still daytime."},
    {'category': 'SLEEP',
     'text': "Your brain sorts and stores memories while you sleep."},
    {'category': 'SLEEP',
     'text': "A short nap can make you feel more focused without ruining your night's sleep."},
    {'category': 'SLEEP',
     'text': "A cooler room tends to help most people fall asleep faster."},
    {'category': 'SLEEP',
     'text': "Kids and teens usually need more sleep than adults because their bodies are still growing."},
    {'category': 'SLEEP',
     'text': "Waking up at the same time every day can actually help you fall asleep easier at night."},
    {'category': 'SLEEP',
     'text': "Your muscles relax more deeply during sleep than at almost any other time."},
    {'category': 'SLEEP',
     'text': "A short walk earlier in the day can help you sleep better that night."},
    {'category': 'BALANCE',
     'text': "Standing on one foot for a few seconds a day can quietly improve your balance over time."},
    {'category': 'BALANCE',
     'text': "Balance isn't just for athletes — it helps prevent everyday trips and falls."},
    {'category': 'BALANCE',
     'text': "Your eyes, ears, and feet all work together to help you stay balanced."},
    {'category': 'BALANCE',
     'text': "Practicing balance gets easier the more often you do it, at any age."},
    {'category': 'BALANCE',
     'text': "Closing your eyes while standing still makes balancing much harder, which shows how much your eyes help."},
    {'category': 'BALANCE',
     'text': "Walking on uneven ground, like grass or sand, quietly trains your balance."},
    {'category': 'BALANCE',
     'text': "Balance tends to improve fastest when you practice it in small amounts, often."},
    {'category': 'BALANCE',
     'text': "Carrying something in one hand can test your balance more than you'd expect."},
    {'category': 'BALANCE',
     'text': "Good balance helps with everyday things, like getting dressed while standing up."},
    {'category': 'BALANCE',
     'text': "Toddlers and grandparents can both benefit from the exact same simple balance practice."},
    {'category': 'FLEXIBILITY',
     'text': "Flexible muscles can make everyday movements, like tying your shoes, feel easier."},
    {'category': 'FLEXIBILITY',
     'text': "A little gentle stretching most days works better than one long stretch once a week."},
    {'category': 'FLEXIBILITY',
     'text': "Your flexibility can change throughout the day — most people are looser in the evening."},
    {'category': 'FLEXIBILITY',
     'text': "Holding a stretch for about 20 to 30 seconds gives your muscles time to actually relax."},
    {'category': 'FLEXIBILITY',
     'text': "Being flexible isn't about touching your toes — it's about moving comfortably."},
    {'category': 'FLEXIBILITY',
     'text': "Cold muscles stretch less easily than warm ones, so moving around a little first helps."},
    {'category': 'FLEXIBILITY',
     'text': "Kids are usually naturally more flexible than adults, but adults can still improve a lot."},
    {'category': 'FLEXIBILITY',
     'text': "Stretching after a warm shower can feel easier because your muscles are already relaxed."},
    {'category': 'FLEXIBILITY',
     'text': "Flexible ankles can make balance and walking noticeably easier."},
    {'category': 'FLEXIBILITY',
     'text': "Yawning and stretching together is your body's natural way of waking itself up."},
    {'category': 'HABITS',
     'text': "Doing something at the same time every day makes it much easier to remember."},
    {'category': 'HABITS',
     'text': "Missing one day rarely breaks a habit — missing many days in a row is what does."},
    {'category': 'HABITS',
     'text': "Small habits repeated often tend to stick better than big changes done occasionally."},
    {'category': 'HABITS',
     'text': "Putting your shoes by the door can make it easier to actually go for that walk."},
    {'category': 'HABITS',
     'text': "Habits form faster when they're tied to something you already do every day."},
    {'category': 'HABITS',
     'text': "It's easier to build a new habit than to break an old one, so replacing beats removing."},
    {'category': 'HABITS',
     'text': "Telling a friend about a goal can make you more likely to actually follow through on it."},
    {'category': 'HABITS',
     'text': "A habit that feels too hard to start usually just needs to be made smaller."},
    {'category': 'HABITS',
     'text': "Most habits take longer to form than people expect — patience matters more than willpower."},
    {'category': 'HABITS',
     'text': "Celebrating small wins helps your brain want to repeat the behavior again."},
    {'category': 'FAMILY',
     'text': "Kids are more likely to be active when they see the adults around them moving too."},
    {'category': 'FAMILY',
     'text': "A short walk after dinner is an easy way for a family to spend time together and move."},
    {'category': 'FAMILY',
     'text': "Playing outside together counts as exercise for everyone, no matter their age."},
    {'category': 'FAMILY',
     'text': "Grandparents and grandkids often move differently, but both benefit from moving together."},
    {'category': 'FAMILY',
     'text': "Family game nights that involve standing and moving can be just as fun as the sit-down kind."},
    {'category': 'FAMILY',
     'text': "Doing chores together, like raking leaves or vacuuming, is real movement that adds up."},
    {'category': 'FAMILY',
     'text': "Kids often copy the habits they see at home more than the ones they're told to follow."},
    {'category': 'FAMILY',
     'text': "A family walk is a good time to talk without screens getting in the way."},
    {'category': 'FAMILY',
     'text': "Teaching a younger family member a new movement can help you both learn it better."},
    {'category': 'FAMILY',
     'text': "Shared routines, like a Sunday walk, tend to last longer than ones you do alone."},
    {'category': 'FUN FACTS',
     'text': "Babies are born with more bones than adults — many of them fuse together as you grow."},
    {'category': 'FUN FACTS',
     'text': "The strongest muscle in your body, for its size, is the one in your jaw."},
    {'category': 'FUN FACTS',
     'text': "You're slightly taller in the morning than at night because gravity squishes you a little throughout the day."},
    {'category': 'FUN FACTS',
     'text': "Your tongue is actually made up of several muscles working together, not just one."},
    {'category': 'FUN FACTS',
     'text': "A sneeze can shoot out of your nose faster than a car drives down most neighborhood streets."},
    {'category': 'FUN FACTS',
     'text': "Your nose can tell apart thousands of different smells without you even trying."},
    {'category': 'FUN FACTS',
     'text': "It's almost impossible to tickle yourself the way someone else can tickle you."},
    {'category': 'FUN FACTS',
     'text': "Pound for pound, your bones are stronger than steel."},
    {'category': 'FUN FACTS',
     'text': "Humans are one of the few animals that can sweat over almost their entire body to cool down."},
    {'category': 'FUN FACTS',
     'text': "Over your lifetime, your heart pumps enough blood to fill a small lake."},
]


def get_daily_insight(date_str):
    d = date.fromisoformat(date_str)
    idx = (d.timetuple().tm_yday - 1) % len(INSIGHT_LIBRARY)
    return INSIGHT_LIBRARY[idx]


# --- Brain Boost ---
# One multiple-choice question per day, same question for every user that day
# (same day-of-year indexing pattern as Today's Insight). Answering is optional,
# allowed once per user per day, and never affects streaks or completion.

BRAIN_BOOST_CORRECT_POINTS = 10
BRAIN_BOOST_INCORRECT_POINTS = 3

BRAIN_BOOST_LIBRARY = [
    {'question': "About how long should you hold a stretch for it to actually help?",
     'options': ["2-3 seconds", "20-30 seconds", "5 minutes", "It doesn't matter"],
     'correct_index': 1,
     'explanation': "Holding a stretch for 20 to 30 seconds gives your muscles enough time to actually relax and lengthen — shorter than that barely does anything."},
    {'question': "What usually causes muscle soreness a day or two after exercise?",
     'options': ["Muscle damage that's getting worse", "Your body repairing and adapting",
                 "Dehydration only", "A sign you should stop exercising"],
     'correct_index': 1,
     'explanation': "That achy feeling usually means your muscles are rebuilding stronger, not breaking down further. Sharp, lasting pain is the one to actually worry about."},
    {'question': "Which helps most people fall asleep faster?",
     'options': ["A warmer room", "A cooler room", "Bright lights before bed", "A late afternoon nap"],
     'correct_index': 1,
     'explanation': "Your body naturally drops in temperature to fall asleep, so a cooler room works with that instead of against it."},
    {'question': "What's a good way to test your balance at home?",
     'options': ["Standing on one foot for a few seconds", "Running in place",
                 "Holding your breath", "Closing both eyes while walking fast"],
     'correct_index': 0,
     'explanation': "A simple one-foot stand is one of the easiest ways to check — and train — your balance, no equipment needed."},
    {'question': "How much does a short walk after a meal typically help?",
     'options': ["No effect at all", "Helps your body handle the food better",
                 "Only helps if it's an hour long", "Only helps before eating"],
     'correct_index': 1,
     'explanation': "Even a short walk after eating can help your body process that meal more smoothly — you don't need a long workout for the benefit."},
    {'question': "What's true about flexibility throughout the day?",
     'options': ["You're equally flexible all day", "Most people are looser in the evening",
                 "Morning is always more flexible", "Flexibility doesn't change"],
     'correct_index': 1,
     'explanation': "Your muscles warm up over the course of the day, so most people are naturally more flexible by evening than first thing in the morning."},
    {'question': "What's the best way to build a new habit?",
     'options': ["Make it as big as possible right away", "Tie it to something you already do every day",
                 "Only do it when you feel motivated", "Wait until you have a perfect plan"],
     'correct_index': 1,
     'explanation': "Habits stick best when they ride along with something you already do every day — like stretching right after you brush your teeth."},
    {'question': "Which is closest to the size of your heart?",
     'options': ["A grain of rice", "About the size of your fist",
                 "The size of a basketball", "The size of your head"],
     'correct_index': 1,
     'explanation': "Your heart is roughly the size of your own closed fist — small, but it beats around 100,000 times a day."},
    {'question': "What's the best response to missing one day of a habit?",
     'options': ["Start over from scratch next month", "Just continue the next day",
                 "Quit the habit entirely", "Double the work the next day"],
     'correct_index': 1,
     'explanation': "One missed day almost never breaks a habit — what actually breaks it is treating one miss as a reason to quit."},
    {'question': "What does climbing stairs do, compared to most everyday movements?",
     'options': ["Works fewer muscles than walking",
                 "Works more muscles at once than almost any other everyday movement",
                 "Has no real exercise benefit", "Only works your calves"],
     'correct_index': 1,
     'explanation': "Climbing stairs works your legs, core, and even your arms (if you swing them) all at once — it's a surprisingly complete movement."},
    {'question': "How do kids tend to pick up movement habits?",
     'options': ["From being told to be active", "Mostly by copying the adults around them",
                 "Movement habits aren't learned", "Only from school gym class"],
     'correct_index': 1,
     'explanation': "Kids tend to mirror what they see at home far more than what they're told to do — movement included."},
    {'question': "What helps most people get better at balance?",
     'options': ["One long practice session, once", "Short, frequent practice",
                 "Avoiding any unstable surfaces", "Only practicing with eyes closed"],
     'correct_index': 1,
     'explanation': "Balance improves fastest with little, regular practice — a minute here and there beats one long session."},
    {'question': "True or false: pound for pound, bones are stronger than steel.",
     'options': ["True", "False", "Only baby bones", "Only in animals, not humans"],
     'correct_index': 0,
     'explanation': "Pound for pound, bone is actually stronger than steel — it's just lighter, so a steel beam the same size would weigh far more."},
    {'question': "What's true about short naps?",
     'options': ["Any nap ruins your night's sleep", "A short nap can help focus without ruining night sleep",
                 "Naps are only useful for kids", "Naps should always be 2+ hours"],
     'correct_index': 1,
     'explanation': "A short nap (think 10-20 minutes) can sharpen focus without leaving you groggy or messing with your sleep that night."},
    {'question': "What's a good sign that recovery, not damage, happened after a workout?",
     'options': ["Sharp pain that doesn't go away", "General tiredness and mild soreness",
                 "Swelling that gets worse for a week", "Inability to move the area at all"],
     'correct_index': 1,
     'explanation': "Mild soreness and tiredness are normal signs of recovery. Sharp pain that lingers or gets worse is your body asking you to back off."},

    # --- Mind & Wellness ---
    # Beginner-friendly, family-friendly, positive, non-clinical. Covers stress, emotions,
    # resilience, healthy habits, self-care, relationships, gratitude, and coping skills.
    {'question': "What's a simple way to calm down when you're feeling stressed?",
     'options': ["Hold your breath as long as possible", "Take a few slow, deep breaths",
                 "Think about everything that's wrong", "Ignore it and hope it goes away"],
     'correct_index': 1, 'category': 'Mind & Wellness',
     'explanation': "Slow, deep breaths signal your body to relax — it's one of the fastest ways to take the edge off stress."},
    {'question': "Which of these is a healthy way to handle a big emotion?",
     'options': ["Naming what you're feeling", "Pretending you don't feel it",
                 "Yelling at whoever is nearby", "Eating a whole bag of chips"],
     'correct_index': 0, 'category': 'Mind & Wellness',
     'explanation': "Just naming an emotion — \"I feel frustrated\" — can make it easier to manage. It's a simple first step, not a fix-all."},
    {'question': "What does \"resilience\" mean?",
     'options': ["Never feeling sad or upset", "Being able to recover and keep going after a hard time",
                 "Always being the strongest person in the room", "Avoiding anything difficult"],
     'correct_index': 1, 'category': 'Mind & Wellness',
     'explanation': "Resilience isn't about never struggling — it's about bouncing back afterward."},
    {'question': "Which is a healthy self-care habit?",
     'options': ["Skipping meals when busy", "Getting enough sleep",
                 "Staying online all night", "Avoiding friends when stressed"],
     'correct_index': 1, 'category': 'Mind & Wellness',
     'explanation': "Sleep is one of the simplest, most effective forms of self-care — it affects mood, focus, and patience."},
    {'question': "What's a healthy way to handle a disagreement with someone you care about?",
     'options': ["Give them the silent treatment", "Say calmly how you feel and listen to them too",
                 "Bring up every past argument", "Walk away and never discuss it"],
     'correct_index': 1, 'category': 'Mind & Wellness',
     'explanation': "Calm, honest communication — and actually listening back — solves more than silence or scorekeeping ever does."},
    {'question': "Practicing gratitude means...",
     'options': ["Pretending everything is perfect", "Noticing and appreciating good things, even small ones",
                 "Only being thankful for big achievements", "Comparing your life to others"],
     'correct_index': 1, 'category': 'Mind & Wellness',
     'explanation': "Gratitude is about noticing what's already good — even something small, like a sunny morning or a good meal."},
    {'question': "What's a healthy coping skill when you're feeling overwhelmed?",
     'options': ["Taking a short walk", "Bottling it up indefinitely",
                 "Avoiding the problem forever", "Making a big decision while upset"],
     'correct_index': 0, 'category': 'Mind & Wellness',
     'explanation': "A short walk can lower stress and help you think more clearly before deciding what to do next."},
    {'question': "True or false: it's okay to ask for help when you're struggling.",
     'options': ["True", "False", "Only for emergencies", "Only if no one else needs help"],
     'correct_index': 0, 'category': 'Mind & Wellness',
     'explanation': "Asking for help is a strength, not a weakness — everyone needs support sometimes."},
    {'question': "Which habit tends to improve mood over time?",
     'options': ["Regular movement, even short walks", "Staying inside all day",
                 "Skipping meals", "Avoiding sunlight"],
     'correct_index': 0, 'category': 'Mind & Wellness',
     'explanation': "Regular movement — even brief — is linked to better mood, not just physical health."},
    {'question': "What's a kind way to talk to yourself after a mistake?",
     'options': ["\"I always mess everything up\"", "\"That didn't go well, but I can try again\"",
                 "\"I'm the worst at everything\"", "\"I should just give up\""],
     'correct_index': 1, 'category': 'Mind & Wellness',
     'explanation': "Talking to yourself the way you'd talk to a friend — fair and encouraging — makes setbacks easier to move past."},
    {'question': "What's one sign that you might need a break?",
     'options': ["Feeling consistently tired or irritable", "Finishing all your tasks easily",
                 "Feeling excited about your day", "Wanting to spend time with friends"],
     'correct_index': 0, 'category': 'Mind & Wellness',
     'explanation': "Ongoing tiredness or irritability is often your body and mind asking for rest."},
    {'question': "Which is a healthy way to support a friend who's upset?",
     'options': ["Tell them to just get over it", "Listen without immediately trying to fix everything",
                 "Change the subject quickly", "Compare it to a bigger problem you once had"],
     'correct_index': 1, 'category': 'Mind & Wellness',
     'explanation': "Sometimes people just need to feel heard — listening first matters more than jumping straight to solutions."},
    {'question': "What can journaling (writing down your thoughts) help with?",
     'options': ["Nothing really", "Sorting out feelings and noticing patterns",
                 "Making problems disappear instantly", "Only useful for writers"],
     'correct_index': 1, 'category': 'Mind & Wellness',
     'explanation': "Writing things down can help you untangle feelings and notice patterns you might not see otherwise."},
    {'question': "Which is an example of a healthy boundary?",
     'options': ["Saying yes to everything to avoid conflict", "Saying \"I can't take that on right now\"",
                 "Never telling anyone what you need", "Doing things you resent to keep the peace"],
     'correct_index': 1, 'category': 'Mind & Wellness',
     'explanation': "A healthy boundary is simply being honest about what you can and can't take on."},
    {'question': "What's a simple way to reset during a stressful day?",
     'options': ["Step outside for a few minutes", "Scroll on your phone faster",
                 "Skip your next meal", "Keep pushing through with no break at all"],
     'correct_index': 0, 'category': 'Mind & Wellness',
     'explanation': "A few minutes outside — fresh air, a change of scenery — can genuinely reset your mood."},
    {'question': "Which best describes a \"growth mindset\"?",
     'options': ["Believing abilities can improve with effort", "Believing you're either good at something or not",
                 "Avoiding anything you're not already good at", "Comparing yourself to others constantly"],
     'correct_index': 0, 'category': 'Mind & Wellness',
     'explanation': "A growth mindset is the belief that skills and abilities can grow with practice — including emotional ones."},
    {'question': "What's a healthy way to celebrate a small win?",
     'options': ["Dismiss it as \"not a big deal\"", "Acknowledge it, even briefly",
                 "Wait until something huge happens", "Compare it to someone else's bigger win"],
     'correct_index': 1, 'category': 'Mind & Wellness',
     'explanation': "Small wins add up. Taking a moment to notice them helps motivation stick around."},
    {'question': "Which is true about feeling nervous before something new?",
     'options': ["It means you should avoid it", "It's a normal feeling, even for exciting things",
                 "Only anxious people feel that way", "It always means something is wrong"],
     'correct_index': 1, 'category': 'Mind & Wellness',
     'explanation': "Nervousness before something new — a first day, a big event — is common and doesn't mean something's wrong."},
    {'question': "What's a good first step when you're feeling angry?",
     'options': ["React immediately", "Pause before responding",
                 "Say the first thing that comes to mind", "Hold it in forever"],
     'correct_index': 1, 'category': 'Mind & Wellness',
     'explanation': "A short pause before reacting gives you a chance to respond instead of just react."},
    {'question': "Which habit helps build stronger relationships over time?",
     'options': ["Showing up consistently, even in small ways", "Only connecting during emergencies",
                 "Avoiding tough conversations forever", "Keeping score of who did more"],
     'correct_index': 0, 'category': 'Mind & Wellness',
     'explanation': "Small, consistent moments — a text, a check-in — build trust more than occasional grand gestures."},
    {'question': "What does \"self-care\" actually mean?",
     'options': ["Expensive treats only", "Basic things like rest, food, and movement that keep you well",
                 "Avoiding all responsibilities", "Something only adults need"],
     'correct_index': 1, 'category': 'Mind & Wellness',
     'explanation': "Self-care is often the basics — sleep, food, movement, rest — not a luxury, but maintenance."},
    {'question': "Which is a healthy way to handle comparing yourself to others?",
     'options': ["Compare constantly to stay motivated", "Notice it, then refocus on your own progress",
                 "Avoid all social situations", "Decide you'll never measure up"],
     'correct_index': 1, 'category': 'Mind & Wellness',
     'explanation': "Comparison is natural — the healthy move is noticing it, then gently bringing focus back to your own path."},
    {'question': "What's one benefit of spending time outdoors?",
     'options': ["No real benefit", "It can lift mood and lower stress",
                 "It only matters in summer", "Only children benefit from it"],
     'correct_index': 1, 'category': 'Mind & Wellness',
     'explanation': "Time outdoors — even briefly — is linked to better mood and lower stress at any age."},
    {'question': "Which is the healthiest response to feeling left out?",
     'options': ["Assume the worst about everyone", "Reach out and check in with someone",
                 "Withdraw completely", "Pretend you don't care at all"],
     'correct_index': 1, 'category': 'Mind & Wellness',
     'explanation': "Reaching out — even a small message — often helps more than assuming the worst or withdrawing."},
    {'question': "What's a good reason to take breaks during a busy day?",
     'options': ["Breaks are a waste of time", "They help you focus better afterward",
                 "Only tired people need them", "They should be avoided at work or school"],
     'correct_index': 1, 'category': 'Mind & Wellness',
     'explanation': "Short breaks actually improve focus afterward — they're not wasted time, they're recovery time."},
]


def get_daily_brain_boost(date_str):
    d = date.fromisoformat(date_str)
    idx = (d.timetuple().tm_yday - 1) % len(BRAIN_BOOST_LIBRARY)
    return BRAIN_BOOST_LIBRARY[idx]


# --- Rickie's joke library ---
# Family-friendly, kid-friendly, fitness/health/wellness themed. No sarcasm,
# no political or adult humor, no medical advice. Used by the Coach route to
# give Rickie real, pre-written jokes to draw from instead of inventing one
# on the fly when a user asks for a joke or something silly.

RICKIE_JOKES = [
    "Why did the bicycle fall over? It was two-tired.",
    "Why don't kettlebells ever tell secrets? They always spill the iron.",
    "Why did the jump rope quit its job? It was tired of being skipped.",
    "Why do stretches make terrible liars? They're always caught reaching.",
    "What's a runner's favorite school subject? Jog-raphy.",
    "What do you call a vegetable that lifts weights? A buff-et.",
    "Why did the smoothie blush? It saw the blender's smooth moves.",
    "What do you call a chicken at the gym? A hen-thusiast.",
    "What's a tree's favorite exercise? Branch presses.",
    "Why did the apple join the gym? It wanted to turn over a new leaf.",
    "Why did the broccoli win the race? It was ahead by a sprout.",
    "Why don't oranges ever skip leg day? They're always well-rounded.",
    "What's a fish's favorite workout? The backstroke.",
    "Why did the stairs get a promotion? They always stepped up.",
    "What do you call a calm cucumber? Cool, collected, and well-hydrated.",
    "Why did the egg go for a jog? It wanted to get a little more egg-cited.",
    "Why did the pillow join the wellness club? It needed better support.",
    "What do you call a dog that does yoga? A dog-a practitioner.",
    "Why did the watermelon win an award? It was outstanding in its field.",
    "Why did the salad get the lead role? It really dressed the part.",
    "What do plants say after a workout? \"That was un-be-leaf-able!\"",
    "What's a frog's favorite exercise? Jumping jacks.",
    "Why did the grape stop exercising? It was raisin the bar too high.",
    "What do you call a well-rested mountain? Boulder than ever.",
    "Why did the tomato turn red? It saw the salad getting all the attention.",
    "Why did the lemon stay positive? It always looked for the zest in life.",
    "What do you call a strong bowl of soup? Souper fit.",
    "What do you call a chill mushroom? A fungi who knows how to relax.",
    "Why did the bread feel proud after baking? It really rose to the occasion.",
    "What do you call a strong cup of tea? A steeped-up workout buddy.",
    "What do you call a happy, hydrated flower? Well-watered and blooming.",
    "What's a turtle's favorite exercise? Slow and steady stretching.",
    "What do you call a strong loaf of bread? Well-kneaded.",
    "Why did the squirrel do push-ups? To get ready for nut-cracking season.",
    "What do you call a relaxed cactus? Low maintenance and well-grounded.",
    "What's a snail's favorite kind of workout? Anything nice and slow.",
    "What do you call a happy glass of water? Refreshed and ready for more.",
    "Why did the banana feel great after the walk? It was finally peeling good.",
    "Why did the celery look so fit? It always stayed crunchy and upright.",
    "What do you call a strong, steady walk? A step in the right direction.",
    "Why did the balloon skip the gym? It already felt light and full of air.",
    "What do you call a calm wave at the beach? Totally board with stress.",
    "Why did the clock stretch every morning? To keep good time with its joints.",
    "What do you call a carrot that works out? Well-grounded and crunchy strong.",
    "Why did the marathon runner bring a map? To find the long way to feeling great.",
    "What do you call a happy pair of running shoes? Sole mates.",
    "Why did the avocado feel zen after yoga? It found its inner pit of calm.",
    "What do you call a strong, calm ocean? Well-balanced and full of good vibes.",
    "Why did the spinach feel unstoppable? It always had a leafy green attitude.",
    "What do you call a peaceful nap after a long walk? A well-earned recharge.",
    "Why did the cyclist bring an umbrella? To stay ahead of any rainy excuses.",
    "What do you call a flexible willow tree? Bendy, balanced, and proud of it.",
    "Why did the granola bar feel confident? It was packed with good energy.",
    "What do you call a calm, steady heartbeat after a walk? A job well done.",
    "Why did the orange peel stay positive? It always saw the bright side.",
    "What do you call a strong handshake after a workout? A grip well earned.",
    "Why did the cloud feel lighter after the rain? It let go of what it didn't need.",
    "What do you call a well-stretched rubber band? Flexible and ready for anything.",
    "Why did the river keep moving? It liked the steady flow of a good routine.",
    "What do you call a happy, well-rested raccoon? Rickie, probably.",
]

_JOKE_TRIGGER_WORDS = ('joke', 'funny', 'silly', 'laugh', 'pun', 'hilarious')


def get_user_stats(user_id):
    """Return current_streak, best_streak, total_missions, and brain_boost_answers."""
    completed_dates = sorted(set(db.session.execute(
        db.select(DailyCompletion.date)
        .where(DailyCompletion.user_id == user_id)
        .group_by(DailyCompletion.date)
        .having(db.func.count(DailyCompletion.exercise_key) >= 5)
    ).scalars().all()))

    total_missions = len(completed_dates)

    brain_boost_answers = db.session.execute(
        db.select(db.func.count(BrainBoostAnswer.id))
        .where(BrainBoostAnswer.user_id == user_id)
    ).scalar() or 0

    if not completed_dates:
        return {'current_streak': 0, 'best_streak': 0, 'total_missions': 0,
                'brain_boost_answers': brain_boost_answers}

    date_set = set(completed_dates)
    today     = date.today()
    yesterday = today - timedelta(days=1)

    check   = today if today in date_set else yesterday
    current = 0
    while check in date_set:
        current += 1
        check -= timedelta(days=1)

    best = run = 0
    prev = None
    for d in completed_dates:
        run  = run + 1 if (prev and (d - prev).days == 1) else 1
        best = max(best, run)
        prev = d

    return {'current_streak': current, 'best_streak': best, 'total_missions': total_missions,
            'brain_boost_answers': brain_boost_answers}


def get_daily_exercises(user_id, date_str, skill_level):
    if skill_level not in EXERCISE_LIBRARY:
        skill_level = 'beginner'

    seed = int(hashlib.sha256(
        f"{user_id}:{date_str}:{skill_level}".encode()
    ).hexdigest(), 16) % (2 ** 32)
    rng  = random.Random(seed)
    pool = EXERCISE_LIBRARY[skill_level]

    # Pre-check: can this level satisfy the fun floor at all?
    level_has_high_fun = any(
        ex['fun_score'] == 'high' for exs in pool.values() for ex in exs
    )

    candidate = None
    for _ in range(_GENERATOR_MAX_RETRIES):
        candidate = [rng.choice(pool[cat]) for cat in _CATEGORIES]
        impacts   = [ex['impact'] for ex in candidate]

        # Constraint 1 — fun floor: at least one high-fun exercise when the
        # pool makes it possible.
        fun_ok    = any(ex['fun_score'] == 'high' for ex in candidate) or not level_has_high_fun
        # Constraint 2 — impact balance: not every exercise can be static.
        impact_ok = any(i in ('low', 'high') for i in impacts)
        # Constraint 3 — high-impact cap: no more than 2 explosive exercises.
        cap_ok    = impacts.count('high') <= 2

        if fun_ok and impact_ok and cap_ok:
            return candidate

    # Graceful fallback: return last generated candidate unchanged.
    return candidate


def get_daily5_streak(user_id):
    """Return consecutive calendar days where the user finished all 5 Daily
    exercises, counting backward from the most recent complete day.

    If today is already complete, the streak includes today.
    If today is not yet complete, the streak counts from yesterday — the streak
    remains alive until a day is actually missed, matching challenge-card
    behaviour."""
    completed_dates = set(db.session.execute(
        db.select(DailyCompletion.date)
        .where(DailyCompletion.user_id == user_id)
        .group_by(DailyCompletion.date)
        .having(db.func.count(DailyCompletion.exercise_key) >= 5)
    ).scalars().all())

    today     = date.today()
    yesterday = today - timedelta(days=1)
    # Start from today if already complete; otherwise give the user the rest
    # of today before counting the streak as broken.
    check = today if today in completed_dates else yesterday

    streak = 0
    while check in completed_dates:
        streak += 1
        check -= timedelta(days=1)
    return streak


# --- Database Models ---

class User(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False)
    password_hash = db.Column(db.String(256), nullable=False)
    skill_level  = db.Column(db.String(20), nullable=False, default='beginner')
    display_mode = db.Column(db.String(20), nullable=False, default='game')
    rickie_mode  = db.Column(db.String(20), nullable=False, default='full')
    xp_total = db.Column(db.Integer, nullable=False, default=0)
    acorns_total = db.Column(db.Integer, nullable=False, default=0)
    is_plus = db.Column(db.Boolean, nullable=False, default=False)
    challenges = db.relationship('Challenge', backref='owner', lazy=True)

class AnalyticsEvent(db.Model):
    __tablename__ = 'analytics_event'
    id         = db.Column(db.Integer, primary_key=True)
    event_name = db.Column(db.String(64), nullable=False)
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)

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

class BrainBoostAnswer(db.Model):
    __tablename__ = 'brain_boost_answer'
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    date = db.Column(db.Date, nullable=False)
    correct = db.Column(db.Boolean, nullable=False)
    points_earned = db.Column(db.Integer, nullable=False)
    answered_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)

    __table_args__ = (
        db.UniqueConstraint('user_id', 'date', name='uq_brain_boost_answer'),
    )

class ProgressEvent(db.Model):
    __tablename__ = 'progress_event'
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    event_type = db.Column(db.String(40), nullable=False)
    xp_delta = db.Column(db.Integer, nullable=False, default=0)
    acorn_delta = db.Column(db.Integer, nullable=False, default=0)
    team_id = db.Column(db.Integer, nullable=True)
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)

# --- Teams (R2.1 Team Foundations — see TEAM_SYSTEM_BASELINE.md) ---
# Team Rickie is deliberately not represented here — it has no membership row,
# no chat, no Campfire. It's UI-only, built from data these tables don't touch.

class Team(db.Model):
    __tablename__ = 'team'
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    created_by_user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)

class TeamMembership(db.Model):
    __tablename__ = 'team_membership'
    id = db.Column(db.Integer, primary_key=True)
    team_id = db.Column(db.Integer, db.ForeignKey('team.id'), nullable=False)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    joined_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)

    __table_args__ = (
        db.UniqueConstraint('team_id', 'user_id', name='uq_team_membership'),
        db.Index('ix_team_membership_user_id', 'user_id'),
    )

class TeamInviteCode(db.Model):
    __tablename__ = 'team_invite_code'
    id = db.Column(db.Integer, primary_key=True)
    team_id = db.Column(db.Integer, db.ForeignKey('team.id'), nullable=False)
    code = db.Column(db.String(8), nullable=False)
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    rotated_at = db.Column(db.DateTime, nullable=True)

    __table_args__ = (
        db.UniqueConstraint('team_id', name='uq_team_invite_code_team'),
        db.UniqueConstraint('code', name='uq_team_invite_code_code'),
    )

class TeamMessage(db.Model):
    __tablename__ = 'team_message'
    id = db.Column(db.Integer, primary_key=True)
    team_id = db.Column(db.Integer, db.ForeignKey('team.id'), nullable=False)
    sender_type = db.Column(db.String(10), nullable=False)  # 'user' | 'rickie'
    sender_user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=True)
    body = db.Column(db.Text, nullable=False)
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)

    __table_args__ = (
        db.Index('ix_team_message_team_id', 'team_id'),
    )

class TeamMoment(db.Model):
    __tablename__ = 'team_moment'
    id = db.Column(db.Integer, primary_key=True)
    team_id = db.Column(db.Integer, db.ForeignKey('team.id'), nullable=False)
    moment_type = db.Column(db.String(40), nullable=False)
    subject_user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=True)
    occurred_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    moment_metadata = db.Column(db.Text, nullable=True)  # JSON string; named to avoid colliding with SQLAlchemy's Model.metadata

    __table_args__ = (
        db.Index('ix_team_moment_team_id', 'team_id'),
    )

class TeamCampfire(db.Model):
    __tablename__ = 'team_campfire'
    id = db.Column(db.Integer, primary_key=True)
    team_id = db.Column(db.Integer, db.ForeignKey('team.id'), nullable=False)
    total_team_missions = db.Column(db.Integer, nullable=False, default=0)
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)

    __table_args__ = (
        db.UniqueConstraint('team_id', name='uq_team_campfire_team'),
    )


class VerificationRun(db.Model):
    """StreakFit Control / Mission Control (R3.0) -- one row per run of the
    verification suite (scripts/verify_all.py, triggered from the admin
    page via WsgiClient or from the CLI). Backs Project Status's "Last
    Verification", the live run in Verify Application, and the
    Verification History table. results_json is the same structured
    (name, passed, detail) rows Results.check() already produces --
    stored verbatim, not re-derived."""
    __tablename__ = 'verification_run'
    id = db.Column(db.Integer, primary_key=True)
    started_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    finished_at = db.Column(db.DateTime, nullable=True)
    suite_version = db.Column(db.Integer, nullable=False)
    commit_sha = db.Column(db.String(40), nullable=True)
    status = db.Column(db.String(20), nullable=False, default='running')  # running | passed | failed | error
    total = db.Column(db.Integer, nullable=False, default=0)
    passed = db.Column(db.Integer, nullable=False, default=0)
    failed = db.Column(db.Integer, nullable=False, default=0)
    results_json = db.Column(db.Text, nullable=True)


# --- Retention: XP / Acorns (helper layer only — nothing wired to routes yet) ---

MISSION_COMPLETE_XP = 25
PERFECT_MISSION_XP = 15
BRAIN_BOOST_CORRECT_XP = 10
BRAIN_BOOST_ATTEMPT_XP = 3
NEW_EXERCISE_BONUS_XP = 20
FAMILY_SESSION_XP = 30

MISSION_COMPLETE_ACORNS = 3
PERFECT_MISSION_ACORNS = 2
BRAIN_BOOST_CORRECT_ACORNS = 1
NEW_EXERCISE_BONUS_ACORNS = 5

LEVEL_TITLES = {
    1: 'Explorer',
    2: 'Adventurer',
    3: 'Pathfinder',
    4: 'Trailblazer',
    5: 'Guide',
    6: 'Ranger',
    7: 'Champion',
    8: 'Legend',
}


def _level_threshold(level):
    """Cumulative XP required to reach the start of `level` (level 1 = 0 XP).

    Thresholds follow the approved curve (0, 100, 250, 450, 700, ...), where
    each level costs 50 more XP than the last to reach — continued smoothly
    forever rather than capped, so numeric level always keeps climbing even
    past the last named title."""
    n = level - 1
    return 25 * n * n + 75 * n


def xp_to_level(xp_total):
    """Derive level/title/progress from lifetime XP. Never stored — always
    computed fresh so the curve can be retuned without a data migration."""
    level = 1
    while _level_threshold(level + 1) <= xp_total:
        level += 1

    xp_into_level = xp_total - _level_threshold(level)
    xp_required = _level_threshold(level + 1) - _level_threshold(level)
    xp_to_next = xp_required - xp_into_level
    level_title = LEVEL_TITLES.get(level, LEVEL_TITLES[max(LEVEL_TITLES)])

    return {
        'level': level,
        'level_title': level_title,
        'xp_into_level': xp_into_level,
        'xp_required': xp_required,
        'xp_to_next': xp_to_next,
    }


def award_progress(user, event_type, xp, acorns, team_id=None):
    """Record an XP/Acorn award: writes a ProgressEvent, increments the
    user's lifetime counters, and reports whether this award crossed a
    level boundary. XP and acorns never decrease — this is the only
    function that should ever change xp_total/acorns_total."""
    old_level = xp_to_level(user.xp_total)['level']

    db.session.add(ProgressEvent(
        user_id=user.id,
        event_type=event_type,
        xp_delta=xp,
        acorn_delta=acorns,
        team_id=team_id,
    ))
    user.xp_total += xp
    user.acorns_total += acorns
    db.session.commit()

    new_level_info = xp_to_level(user.xp_total)
    new_level = new_level_info['level']

    return {
        'xp_awarded': xp,
        'acorns_awarded': acorns,
        'old_level': old_level,
        'new_level': new_level,
        'leveled_up': new_level > old_level,
        'level_title': new_level_info['level_title'],
    }


# --- Frontend ---

@app.route('/')
def frontend():
    return app.send_static_file('index.html')


@app.route('/sw.js')
def service_worker():
    # Served at root (not /static/sw.js) so its default scope covers the
    # whole app — a script under /static/ would only control /static/ and
    # never the '/' start_url, breaking PWA installability.
    response = app.make_response(app.send_static_file('sw.js'))
    response.headers['Service-Worker-Allowed'] = '/'
    return response


# --- Health Check ---

@app.route('/health', methods=['GET'])
def health():
    return jsonify({"status": "ok"}), 200


# --- Admin ---

@app.route('/admin')
def admin_dashboard():
    return app.send_static_file('admin.html')


def _require_admin_secret():
    """Shared X-Admin-Secret gate for every /api/admin/* route -- same
    model as admin_stats used alone for years; factored out now that
    StreakFit Control (R3.0) adds four more routes needing the same check."""
    secret = request.headers.get('X-Admin-Secret', '')
    env_secret = os.environ.get('ADMIN_SECRET', '')
    if not env_secret or secret != env_secret:
        abort(403)


def _get_commit_sha():
    """Real value, not invented: prefers Render's own env var if present,
    falls back to asking git directly (the deployed checkout still has
    its .git directory on a normal Render deploy), else None -- shown
    honestly as "unknown" rather than a fabricated commit."""
    env_sha = os.environ.get('RENDER_GIT_COMMIT')
    if env_sha:
        return env_sha[:12]
    try:
        result = subprocess.run(
            ['git', 'rev-parse', 'HEAD'],
            cwd=os.path.dirname(os.path.abspath(__file__)),
            capture_output=True, text=True, timeout=5,
        )
        if result.returncode == 0:
            return result.stdout.strip()[:12]
    except Exception:
        pass
    return None


@app.route('/api/admin/stats')
@limiter.limit("120 per minute")
def admin_stats():
    _require_admin_secret()

    now = datetime.utcnow()
    today_start  = datetime(now.year, now.month, now.day)
    seven_ago    = now - timedelta(days=7)
    thirty_ago   = now - timedelta(days=30)

    try:
        def counts(event_name):
            def n(since):
                return db.session.query(
                    db.func.count(AnalyticsEvent.id)
                ).filter(
                    AnalyticsEvent.event_name == event_name,
                    AnalyticsEvent.created_at >= since
                ).scalar() or 0
            return {
                'today':    n(today_start),
                'week':     n(seven_ago),
                'month':    n(thirty_ago),
                'all_time': db.session.query(
                    db.func.count(AnalyticsEvent.id)
                ).filter(AnalyticsEvent.event_name == event_name).scalar() or 0,
            }

        today = date.today()
        seven_days_ago = today - timedelta(days=6)  # inclusive 7-day window

        total_registered_users = db.session.query(
            db.func.count(User.id)
        ).scalar() or 0

        active_users_today = db.session.query(
            db.func.count(db.func.distinct(DailyCompletion.user_id))
        ).filter(DailyCompletion.date == today).scalar() or 0

        completions_today = db.session.query(
            db.func.count(DailyCompletion.id)
        ).filter(DailyCompletion.date == today).scalar() or 0

        completions_7d = db.session.query(
            db.func.count(DailyCompletion.id)
        ).filter(DailyCompletion.date >= seven_days_ago).scalar() or 0

        completions_all_time = db.session.query(
            db.func.count(DailyCompletion.id)
        ).scalar() or 0

        # Most recent 50 users by ID. There's no created_at column on User,
        # so ID order (not a real join date) is the best available proxy
        # for signup recency — never label this as a join date.
        recent_users_rows = db.session.query(User).order_by(User.id.desc()).limit(50).all()
        recent_users = []
        for u in recent_users_rows:
            stats = get_user_stats(u.id)
            last_active = db.session.query(
                db.func.max(DailyCompletion.date)
            ).filter(DailyCompletion.user_id == u.id).scalar()
            recent_users.append({
                'id':                  u.id,
                'username':            u.username,
                'missions_completed':  stats['total_missions'],
                'current_streak':      stats['current_streak'],
                'last_active':         last_active.isoformat() if last_active else None,
            })

        # Users with >=1 full Daily Mission (all 5 exercises in one day) ever.
        full_mission_days = (
            db.session.query(DailyCompletion.user_id)
            .group_by(DailyCompletion.user_id, DailyCompletion.date)
            .having(db.func.count(DailyCompletion.exercise_key) >= 5)
            .subquery()
        )
        users_with_completion = db.session.query(
            db.func.count(db.func.distinct(full_mission_days.c.user_id))
        ).scalar() or 0

        # Day-over-day return: of users active yesterday, how many were
        # also active today. Derived entirely from existing DailyCompletion
        # rows — no new tracking needed. Only reflects one day of transition,
        # so it's a thin signal until more days accumulate.
        yesterday = today - timedelta(days=1)
        yesterday_user_ids = {
            row[0] for row in db.session.query(DailyCompletion.user_id)
            .filter(DailyCompletion.date == yesterday).distinct().all()
        }
        today_user_ids = {
            row[0] for row in db.session.query(DailyCompletion.user_id)
            .filter(DailyCompletion.date == today).distinct().all()
        }
        active_yesterday_count = len(yesterday_user_ids)
        returned_next_day_count = len(yesterday_user_ids & today_user_ids)

        return jsonify({
            'generated_at': now.isoformat() + 'Z',
            'users': {
                'total_registered': total_registered_users,
            },
            'active_users': {
                'today_by_completion': active_users_today,
            },
            'mission_completions': {
                'today':    completions_today,
                'week':     completions_7d,
                'all_time': completions_all_time,
            },
            'events': {
                'guest_start':                counts('guest_start'),
                'guest_complete':             counts('guest_complete'),
                'guest_create_account_click': counts('guest_create_account_click'),
                'account_created':            counts('account_created'),
            },
            'recent_users': recent_users,
            'users_with_completion': {
                'count': users_with_completion,
            },
            'returned_next_day': {
                'returned':         returned_next_day_count,
                'active_yesterday': active_yesterday_count,
            },
            # Calls out which numbers above are exact counts vs. derived
            # approximations, since this app has no unique-visitor or
            # login-session tracking — only registration and completion events.
            'metric_notes': {
                'users.total_registered':            'exact',
                'active_users.today_by_completion':  'approximation — counts users with >=1 mission completion today; not session/login based, so inactive-but-logged-in users are not counted',
                'mission_completions':                'exact — count of DailyCompletion rows',
                'events.guest_start':                 'proxy for visits — not unique-visitor tracking',
                'events.account_created':              'exact — fired server-side in /api/register on every successful signup, recorded from this deploy forward; pre-existing accounts are not backfilled',
                'recent_users':                       'sorted by user ID descending, not join date — User has no creation timestamp column. last_active is "Unknown" (null) if the user has never completed a mission.',
                'users_with_completion':               'exact — distinct users with >=1 day of all 5 exercises completed, all-time',
                'returned_next_day':                   'exact, but a single-day cohort — only meaningful once several days of yesterday→today transitions have accumulated',
            },
        })
    except Exception:
        db.session.rollback()
        app.logger.warning('admin_stats query failed')
        return jsonify({'error': 'stats_unavailable'}), 503


# --- StreakFit Control / Mission Control (R3.0) ---
#
# _verification_state tracks the one background run this process can have
# in flight at a time (v1 doesn't support concurrent runs -- the button is
# disabled client-side while one is running, and the server rejects a
# second start with 409 regardless). VerificationRun rows are the durable
# record; this dict is just live progress for the poller.
_verification_state = {"running": False, "current_module": None, "run_id": None}


def _compute_system_health():
    """Real checks only -- see CLAUDE.md / scripts/verification/README.md
    for why Notifications and Render health are honestly labeled instead
    of faked. Answering this request at all is the API's own health
    signal, so there's no self-HTTP-call here (that would be the exact
    self-referential-request problem WsgiClient exists to avoid)."""
    db_healthy = True
    try:
        db.session.execute(db.text('SELECT 1'))
    except Exception:
        db.session.rollback()
        db_healthy = False

    repo_root = os.path.dirname(os.path.abspath(__file__))

    sw_version = None
    try:
        with open(os.path.join(repo_root, 'static', 'sw.js')) as f:
            for line in f:
                if line.strip().startswith('const CACHE'):
                    sw_version = line.split('=', 1)[1].strip().rstrip(';').strip().strip("'\"")
                    break
    except Exception:
        sw_version = None

    manifest_path = os.path.join(repo_root, 'static', 'manifest.json')
    manifest_present = os.path.exists(manifest_path)
    icons_present = False
    if manifest_present:
        try:
            with open(manifest_path) as f:
                manifest = json.load(f)
            icon_paths = [icon.get('src', '') for icon in manifest.get('icons', [])]
            icons_present = bool(icon_paths) and all(
                os.path.exists(os.path.join(repo_root, p.lstrip('/'))) for p in icon_paths if p
            )
        except Exception:
            icons_present = False

    return {
        "api": "healthy",
        "database": "healthy" if db_healthy else "unhealthy",
        "service_worker": {"cache_version": sw_version},
        "pwa": {"manifest_present": manifest_present, "icons_present": icons_present},
        "notifications": {
            "status": "not_applicable",
            "note": "Client-side only feature (browser Notification API + local service worker display) -- no server-side signal exists to check.",
        },
        "render": {
            "status": "unavailable",
            "note": "No Render API key configured in this environment.",
        },
    }


def _run_verification_background(run_id):
    """Runs the full suite via WsgiClient (in-process WSGI dispatch, not a
    real socket -- see scripts/verify_all.py's docstring for why that
    matters on a single-worker deployment) and writes the result to the
    VerificationRun row this thread owns exclusively."""
    client = WsgiClient(app)

    def on_module_start(label):
        _verification_state["current_module"] = label

    try:
        results = run_suite(client, on_module_start=on_module_start)
        summary = results.to_dict()
        with app.app_context():
            run = db.session.get(VerificationRun, run_id)
            run.finished_at = datetime.utcnow()
            run.status = 'passed' if summary['failed'] == 0 else 'failed'
            run.total = summary['total']
            run.passed = summary['passed']
            run.failed = summary['failed']
            run.results_json = json.dumps(summary['checks'])
            db.session.commit()
    except SystemExit:
        # Results.fatal() calls sys.exit(2) on an unrecoverable setup
        # failure (e.g. registration itself failing) -- that's a CLI exit
        # convention this background thread needs to catch, not propagate.
        with app.app_context():
            run = db.session.get(VerificationRun, run_id)
            run.finished_at = datetime.utcnow()
            run.status = 'error'
            db.session.commit()
    except Exception as e:
        with app.app_context():
            run = db.session.get(VerificationRun, run_id)
            run.finished_at = datetime.utcnow()
            run.status = 'error'
            run.results_json = json.dumps({"error": str(e)})
            db.session.commit()
    finally:
        _verification_state["running"] = False
        _verification_state["current_module"] = None


@app.route('/api/admin/project-status')
@limiter.limit("60 per minute")
def admin_project_status():
    _require_admin_secret()
    health = _compute_system_health()
    latest = db.session.query(VerificationRun).order_by(VerificationRun.id.desc()).first()

    system_ok = health["database"] == "healthy"
    verification_stale = (
        latest is not None and latest.finished_at is not None
        and (datetime.utcnow() - latest.finished_at) > timedelta(hours=24)
    )

    if not system_ok or (latest is not None and latest.status == "failed"):
        overall = "red"
    elif latest is None or latest.status in ("running", "error") or verification_stale:
        overall = "yellow"
    else:
        overall = "green"

    commit_sha = _get_commit_sha()

    return jsonify({
        "production_health": "healthy" if system_ok else "unhealthy",
        "commit_sha": commit_sha,
        # No versioning scheme exists anywhere in this repo (see CLAUDE.md) --
        # the commit SHA is the real, honest identity until one does.
        "current_version": commit_sha or "unknown",
        "last_deployment_at": _PROCESS_STARTED_AT.isoformat() + "Z",
        "last_verification": None if latest is None else {
            "run_id": latest.id,
            "status": latest.status,
            "suite_version": latest.suite_version,
            "total": latest.total,
            "passed": latest.passed,
            "failed": latest.failed,
            "finished_at": latest.finished_at.isoformat() + "Z" if latest.finished_at else None,
        },
        "overall_health": overall,
    }), 200


@app.route('/api/admin/system-health')
@limiter.limit("60 per minute")
def admin_system_health():
    _require_admin_secret()
    return jsonify(_compute_system_health()), 200


@app.route('/api/admin/verify', methods=['POST'])
@limiter.limit("6 per minute")
def admin_verify_start():
    _require_admin_secret()
    if _verification_state["running"]:
        return jsonify({"error": "verification_already_running"}), 409

    run = VerificationRun(
        suite_version=VERIFICATION_SUITE_VERSION,
        commit_sha=_get_commit_sha(),
        status='running',
        total=0, passed=0, failed=0,
    )
    db.session.add(run)
    db.session.commit()

    _verification_state["running"] = True
    _verification_state["current_module"] = None
    _verification_state["run_id"] = run.id

    thread = threading.Thread(target=_run_verification_background, args=(run.id,), daemon=True)
    thread.start()

    return jsonify({"run_id": run.id, "status": "started"}), 202


@app.route('/api/admin/verify/status')
@limiter.limit("120 per minute")
def admin_verify_status():
    _require_admin_secret()
    run_id = _verification_state.get("run_id")
    latest = db.session.get(VerificationRun, run_id) if run_id else None
    if latest is None:
        latest = db.session.query(VerificationRun).order_by(VerificationRun.id.desc()).first()
    if latest is None:
        return jsonify({"status": "never_run", "running": False}), 200

    return jsonify({
        "run_id": latest.id,
        "status": latest.status,
        "running": _verification_state["running"],
        "current_module": _verification_state["current_module"] if _verification_state["running"] else None,
        "suite_version": latest.suite_version,
        "commit_sha": latest.commit_sha,
        "started_at": latest.started_at.isoformat() + "Z",
        "finished_at": latest.finished_at.isoformat() + "Z" if latest.finished_at else None,
        "total": latest.total,
        "passed": latest.passed,
        "failed": latest.failed,
        "checks": json.loads(latest.results_json) if latest.results_json else [],
    }), 200


@app.route('/api/admin/verify/history')
@limiter.limit("60 per minute")
def admin_verify_history():
    _require_admin_secret()
    runs = db.session.query(VerificationRun).order_by(VerificationRun.id.desc()).limit(20).all()
    return jsonify({
        "runs": [
            {
                "run_id": r.id,
                "started_at": r.started_at.isoformat() + "Z",
                "finished_at": r.finished_at.isoformat() + "Z" if r.finished_at else None,
                "suite_version": r.suite_version,
                "commit_sha": r.commit_sha,
                "status": r.status,
                "total": r.total,
                "passed": r.passed,
                "failed": r.failed,
                "duration_seconds": (
                    (r.finished_at - r.started_at).total_seconds() if r.finished_at else None
                ),
            }
            for r in runs
        ]
    }), 200


# --- Analytics ---

_ALLOWED_EVENTS = {
    'guest_start', 'guest_complete', 'guest_create_account_click',
    'notification_permission_granted', 'notification_permission_denied',
    'notification_sent_daily', 'notification_sent_completion',
    'install_prompt_shown', 'install_prompt_accepted', 'install_prompt_dismissed',
}

@app.route('/api/events', methods=['POST'])
@limiter.limit("30 per minute")
def record_event():
    data = request.get_json(silent=True) or {}
    name = data.get('event', '')
    if name not in _ALLOWED_EVENTS:
        return jsonify({"error": "unknown event"}), 400
    try:
        db.session.add(AnalyticsEvent(event_name=name))
        db.session.commit()
    except Exception:
        db.session.rollback()
        app.logger.warning('analytics write failed for event: %s', name)
    return '', 204


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

    try:
        db.session.add(AnalyticsEvent(event_name='account_created'))
        db.session.commit()
    except Exception:
        db.session.rollback()
        app.logger.warning('analytics write failed for event: account_created')

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
    stats = get_user_stats(user_id)
    level_info = xp_to_level(user.xp_total)
    return jsonify({
        "id": user.id,
        "username": user.username,
        "skill_level": user.skill_level,
        "display_mode": user.display_mode,
        "rickie_mode": user.rickie_mode,
        "current_streak": stats['current_streak'],
        "best_streak": stats['best_streak'],
        "total_missions": stats['total_missions'],
        "brain_boost_answers": stats['brain_boost_answers'],
        "xp_total": user.xp_total,
        "acorns_total": user.acorns_total,
        "level": level_info['level'],
        "level_title": level_info['level_title'],
        "xp_into_level": level_info['xp_into_level'],
        "xp_required": level_info['xp_required'],
        "xp_to_next_level": level_info['xp_to_next']
    }), 200

@app.route('/api/me', methods=['PATCH'])
@jwt_required()
def update_me():
    data = request.get_json()
    if not data or ('skill_level' not in data and 'display_mode' not in data and 'rickie_mode' not in data):
        return jsonify({"error": "Provide skill_level, display_mode, and/or rickie_mode"}), 400

    if 'skill_level' in data and data['skill_level'] not in VALID_SKILL_LEVELS:
        return jsonify({"error": "Invalid skill_level. Must be one of: beginner, intermediate, advanced, custom"}), 400

    if 'display_mode' in data and data['display_mode'] not in VALID_DISPLAY_MODES:
        return jsonify({"error": "Invalid display_mode. Must be one of: classic, bright, game"}), 400

    if 'rickie_mode' in data and data['rickie_mode'] not in VALID_RICKIE_MODES:
        return jsonify({"error": "Invalid rickie_mode. Must be one of: full, quiet, minimal"}), 400

    user_id = int(get_jwt_identity())
    user = db.session.get(User, user_id)
    if user is None:
        abort(404)

    if 'skill_level' in data:
        user.skill_level = data['skill_level']
    if 'display_mode' in data:
        user.display_mode = data['display_mode']
    if 'rickie_mode' in data:
        user.rickie_mode = data['rickie_mode']

    db.session.commit()
    stats = get_user_stats(user_id)
    return jsonify({
        "id": user.id,
        "username": user.username,
        "skill_level": user.skill_level,
        "display_mode": user.display_mode,
        "rickie_mode": user.rickie_mode,
        "current_streak": stats['current_streak'],
        "best_streak": stats['best_streak'],
        "total_missions": stats['total_missions'],
        "brain_boost_answers": stats['brain_boost_answers']
    }), 200

_MEMORY_BOOK_TIMELINE_LIMIT = 30

_MILESTONE_DEFINITIONS = [
    {'key': 'first_mission',  'label': 'First Mission',      'metric': 'missions_completed',    'target': 1},
    {'key': 'exercises_100',  'label': '100 Exercises',       'metric': 'exercises_completed',   'target': 100},
    {'key': 'exercises_500',  'label': '500 Exercises',       'metric': 'exercises_completed',   'target': 500},
    {'key': 'brain_boost_100', 'label': '100 Brain Boosts',   'metric': 'brain_boosts_answered', 'target': 100},
    {'key': 'xp_1000',        'label': '1000 XP',             'metric': 'xp_total',              'target': 1000},
    {'key': 'acorns_100',     'label': '100 Acorns',          'metric': 'acorns_total',           'target': 100},
    {'key': 'level_10',       'label': 'Level 10',            'metric': 'level',                  'target': 10},
]


def _resolve_exercise_meta(exercise_key):
    """Look up an exercise's display name/category from EXERCISE_LIBRARY by
    key, searching across every skill tier since DailyCompletion doesn't
    record which tier a key was completed under."""
    for pools in EXERCISE_LIBRARY.values():
        for exercises in pools.values():
            for ex in exercises:
                if ex['key'] == exercise_key:
                    return ex['name'], ex['category']
    return exercise_key, None


@app.route('/api/memory-book', methods=['GET'])
@jwt_required()
def get_memory_book():
    user_id = int(get_jwt_identity())
    user = db.session.get(User, user_id)
    if user is None:
        abort(404)

    stats = get_user_stats(user_id)
    level_info = xp_to_level(user.xp_total)

    exercises_completed = db.session.execute(
        db.select(db.func.count(DailyCompletion.id)).where(DailyCompletion.user_id == user_id)
    ).scalar() or 0

    correct_answers = db.session.execute(
        db.select(db.func.count(BrainBoostAnswer.id)).where(
            BrainBoostAnswer.user_id == user_id, BrainBoostAnswer.correct == True
        )
    ).scalar() or 0

    completion_dates = set(db.session.execute(
        db.select(DailyCompletion.date).where(DailyCompletion.user_id == user_id).distinct()
    ).scalars().all())
    brain_boost_dates = set(db.session.execute(
        db.select(BrainBoostAnswer.date).where(BrainBoostAnswer.user_id == user_id).distinct()
    ).scalars().all())
    days_active = len(completion_dates | brain_boost_dates)

    lifetime = {
        'xp_total': user.xp_total,
        'acorns_total': user.acorns_total,
        'missions_completed': stats['total_missions'],
        'brain_boosts_answered': stats['brain_boost_answers'],
        'correct_answers': correct_answers,
        'exercises_completed': exercises_completed,
        'days_active': days_active,
    }

    metric_values = dict(lifetime)
    metric_values['level'] = level_info['level']

    milestones = [
        {
            'key': m['key'],
            'label': m['label'],
            'target': m['target'],
            'progress': min(metric_values[m['metric']], m['target']),
            'unlocked': metric_values[m['metric']] >= m['target'],
        }
        for m in _MILESTONE_DEFINITIONS
    ]

    favorite_row = db.session.execute(
        db.select(DailyCompletion.exercise_key, db.func.count(DailyCompletion.id).label('n'))
        .where(DailyCompletion.user_id == user_id)
        .group_by(DailyCompletion.exercise_key)
        .order_by(db.desc('n'))
        .limit(1)
    ).first()

    if favorite_row:
        fav_name, fav_category = _resolve_exercise_meta(favorite_row.exercise_key)
    else:
        fav_name, fav_category = None, None

    category_row = None
    if favorite_row:
        # category isn't stored on DailyCompletion, so tally categories in
        # Python from each completion's resolved exercise metadata.
        category_counts = {}
        all_rows = db.session.execute(
            db.select(DailyCompletion.exercise_key).where(DailyCompletion.user_id == user_id)
        ).scalars().all()
        for key in all_rows:
            _, cat = _resolve_exercise_meta(key)
            if cat:
                category_counts[cat] = category_counts.get(cat, 0) + 1
        if category_counts:
            category_row = max(category_counts.items(), key=lambda kv: kv[1])[0]

    favorites = {
        'favorite_exercise': fav_name,
        'favorite_category': category_row,
    }

    events = db.session.execute(
        db.select(ProgressEvent)
        .where(ProgressEvent.user_id == user_id)
        .order_by(ProgressEvent.created_at.desc(), ProgressEvent.id.desc())
        .limit(_MEMORY_BOOK_TIMELINE_LIMIT)
    ).scalars().all()

    timeline = [
        {
            'event_type': e.event_type,
            'xp_delta': e.xp_delta,
            'acorn_delta': e.acorn_delta,
            'created_at': e.created_at.isoformat(),
        }
        for e in events
    ]

    return jsonify({
        'version': 1,
        'lifetime': lifetime,
        'milestones': milestones,
        'favorites': favorites,
        'timeline': timeline,
    }), 200


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
    insight   = get_daily_insight(today_str)
    boost     = get_daily_brain_boost(today_str)

    boost_answer = db.session.execute(
        db.select(BrainBoostAnswer).where(
            BrainBoostAnswer.user_id == user_id,
            BrainBoostAnswer.date == today
        )
    ).scalar_one_or_none()

    brain_boost_payload = {
        "question": boost['question'],
        "options": boost['options'],
        "answered": boost_answer is not None,
    }
    if boost_answer is not None:
        brain_boost_payload["correct"] = boost_answer.correct
        brain_boost_payload["points_earned"] = boost_answer.points_earned
        brain_boost_payload["correct_index"] = boost['correct_index']
        brain_boost_payload["explanation"] = boost['explanation']

    completed_keys = set(db.session.execute(
        db.select(DailyCompletion.exercise_key).where(
            DailyCompletion.user_id == user_id,
            DailyCompletion.date == today
        )
    ).scalars().all())

    yesterday = today - timedelta(days=1)
    all_completed_dates = sorted(db.session.execute(
        db.select(DailyCompletion.date)
        .where(DailyCompletion.user_id == user_id)
        .group_by(DailyCompletion.date)
        .having(db.func.count(DailyCompletion.exercise_key) >= 5)
    ).scalars().all())

    all_date_set = set(all_completed_dates)
    best = run = 0
    prev = None
    for d in all_completed_dates:
        run  = run + 1 if (prev and (d - prev).days == 1) else 1
        best = max(best, run)
        prev = d

    rise_again = (
        bool(all_date_set)
        and len(completed_keys) < 5
        and yesterday not in all_date_set
        and best >= 7
    )

    return jsonify({
        "date": today_str,
        "skill_level": user.skill_level,
        "completed_count": len(completed_keys),
        "rise_again": rise_again,
        "insight": insight,
        "brain_boost": brain_boost_payload,
        "exercises": [
            {
                "key": ex['key'],
                "name": ex['name'],
                "category": ex['category'],
                "difficulty": ex['difficulty'],
                "reps_or_duration": ex['reps_or_duration'],
                "instructions": ex['instructions'],
                "completed": ex['key'] in completed_keys,
                "image_url": f"/static/exercises/{ex['key']}.svg"
            }
            for ex in exercises
        ]
    }), 200


@app.route('/api/demo/daily', methods=['GET'])
def get_demo_daily():
    today = date.today()
    today_str = today.isoformat()
    exercises = get_daily_exercises('demo', today_str, 'beginner')
    insight   = get_daily_insight(today_str)
    return jsonify({
        "date": today_str,
        "skill_level": "beginner",
        "completed_count": 0,
        "rise_again": False,
        "insight": insight,
        "exercises": [
            {
                "key": ex['key'],
                "name": ex['name'],
                "category": ex['category'],
                "difficulty": ex['difficulty'],
                "reps_or_duration": ex['reps_or_duration'],
                "instructions": ex['instructions'],
                "completed": False,
                "image_url": f"/static/exercises/{ex['key']}.svg"
            }
            for ex in exercises
        ]
    }), 200


def _progress_response(old_level, user, events):
    """Combine a list of award_progress() results into the additive response
    fields shared by both completion routes. Route-response shaping only —
    not part of the R1.2 helper layer itself."""
    new_level_info = xp_to_level(user.xp_total)
    return {
        'xp_awarded': sum(e['xp_awarded'] for e in events),
        'acorns_awarded': sum(e['acorns_awarded'] for e in events),
        'old_level': old_level,
        'new_level': new_level_info['level'],
        'leveled_up': new_level_info['level'] > old_level,
        'level_title': new_level_info['level_title'],
        'progress_events': events,
    }


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

    old_level = xp_to_level(user.xp_total)['level']
    events = []

    existing = db.session.execute(
        db.select(DailyCompletion).where(
            DailyCompletion.user_id == user_id,
            DailyCompletion.date == today,
            DailyCompletion.exercise_key == exercise_key
        )
    ).scalar_one_or_none()

    if not existing:
        # Must check "ever completed this key before" BEFORE inserting today's
        # row, otherwise the row we're about to add would make this always true.
        is_new_exercise_ever = db.session.execute(
            db.select(db.func.count(DailyCompletion.id)).where(
                DailyCompletion.user_id == user_id,
                DailyCompletion.exercise_key == exercise_key
            )
        ).scalar() == 0

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

        if is_new_exercise_ever:
            events.append(award_progress(user, 'new_exercise', NEW_EXERCISE_BONUS_XP, NEW_EXERCISE_BONUS_ACORNS))

        team_campfire_updates = []
        if completed_count == 5:
            events.append(award_progress(user, 'mission_complete', MISSION_COMPLETE_XP, MISSION_COMPLETE_ACORNS))
            events.append(award_progress(user, 'perfect_mission', PERFECT_MISSION_XP, PERFECT_MISSION_ACORNS))

            # R2.3 Campfire MVP: this branch (not existing -> just inserted,
            # completed_count == 5) can only be reached once per user per day,
            # the same way it already is for mission_complete/perfect_mission
            # above -- the unique constraint on DailyCompletion makes a 5th
            # *new* completion today a one-time event, so this is naturally
            # idempotent, not something guarded separately. One completion
            # counts for every real team the user belongs to, simultaneously
            # (TEAM_SYSTEM_BASELINE Section 3) -- no "which team" picker.
            # Team Rickie is UI-only and has no team_campfire row to touch.
            memberships = db.session.execute(
                db.select(TeamMembership).where(TeamMembership.user_id == user_id)
            ).scalars().all()
            for m in memberships:
                campfire = db.session.execute(
                    db.select(TeamCampfire).where(TeamCampfire.team_id == m.team_id)
                ).scalar_one_or_none()
                if not campfire:
                    continue
                stage_before = _campfire_stage(campfire.total_team_missions)
                campfire.total_team_missions += 1
                stage_after = _campfire_stage(campfire.total_team_missions)
                team_campfire_updates.append({
                    "team_id": m.team_id,
                    "total_team_missions": campfire.total_team_missions,
                    "stage": stage_after,
                })

                # R2.4 Team Moments MVP: the log itself is always recorded
                # (raw ledger data for a future contribution-history view,
                # per TEAM_SYSTEM_BASELINE Section 10 -- not meant to be
                # rendered 1:1 as a moment card). The stage moment only
                # fires when the increment actually crossed a threshold --
                # comparing before/after here means no separate dedup guard
                # is needed, same reasoning as the campfire increment itself.
                create_team_moment(
                    m.team_id, 'campfire_log_added', subject_user_id=user_id,
                    metadata={"total_team_missions": campfire.total_team_missions}
                )
                # R2.6 Rickie Team Reactions MVP: only the team's very first
                # log gets a Rickie message -- every log after that is a
                # moment (raw ledger) but not a chat post, or Rickie would
                # spam the thread on every single mission completion.
                if campfire.total_team_missions == 1:
                    create_rickie_team_message(m.team_id, 'first_log')
                if stage_after != stage_before:
                    create_team_moment(
                        m.team_id, 'campfire_stage_reached', subject_user_id=None,
                        metadata={"stage": stage_after, "total_team_missions": campfire.total_team_missions}
                    )
                    create_rickie_team_message(m.team_id, 'campfire_stage_reached')
            if team_campfire_updates:
                db.session.commit()
    else:
        completed_count = db.session.execute(
            db.select(db.func.count(DailyCompletion.id)).where(
                DailyCompletion.user_id == user_id,
                DailyCompletion.date == today
            )
        ).scalar()
        team_campfire_updates = []

    response = {
        "message": "Exercise completed",
        "exercise_key": exercise_key,
        "completed_count": completed_count,
        "team_campfire_updates": team_campfire_updates
    }
    response.update(_progress_response(old_level, user, events))
    return jsonify(response), 200


@app.route('/api/brain-boost/answer', methods=['POST'])
@jwt_required()
def answer_brain_boost():
    data = request.get_json(silent=True) or {}
    selected_index = data.get('selected_index')
    if not isinstance(selected_index, int) or isinstance(selected_index, bool):
        return jsonify({"error": "selected_index_required"}), 400

    user_id = int(get_jwt_identity())
    user = db.session.get(User, user_id)
    if user is None:
        abort(404)

    today = date.today()
    boost = get_daily_brain_boost(today.isoformat())

    if selected_index < 0 or selected_index >= len(boost['options']):
        return jsonify({"error": "invalid_selected_index"}), 400

    old_level = xp_to_level(user.xp_total)['level']

    existing = db.session.execute(
        db.select(BrainBoostAnswer).where(
            BrainBoostAnswer.user_id == user_id,
            BrainBoostAnswer.date == today
        )
    ).scalar_one_or_none()

    if existing:
        response = {
            "correct": existing.correct,
            "points_earned": existing.points_earned,
            "correct_index": boost['correct_index'],
            "explanation": boost['explanation']
        }
        response.update(_progress_response(old_level, user, []))
        return jsonify(response), 200

    is_correct = (selected_index == boost['correct_index'])
    points = BRAIN_BOOST_CORRECT_POINTS if is_correct else BRAIN_BOOST_INCORRECT_POINTS

    try:
        db.session.add(BrainBoostAnswer(
            user_id=user_id, date=today, correct=is_correct, points_earned=points
        ))
        db.session.commit()
    except Exception:
        db.session.rollback()
        existing = db.session.execute(
            db.select(BrainBoostAnswer).where(
                BrainBoostAnswer.user_id == user_id,
                BrainBoostAnswer.date == today
            )
        ).scalar_one_or_none()
        if existing:
            response = {
                "correct": existing.correct,
                "points_earned": existing.points_earned,
                "correct_index": boost['correct_index'],
                "explanation": boost['explanation']
            }
            response.update(_progress_response(old_level, user, []))
            return jsonify(response), 200
        abort(500)

    events = [award_progress(user, 'brain_boost_attempt', BRAIN_BOOST_ATTEMPT_XP, 0)]
    if is_correct:
        events.append(award_progress(user, 'brain_boost_correct', BRAIN_BOOST_CORRECT_XP, BRAIN_BOOST_CORRECT_ACORNS))

    response = {
        "correct": is_correct,
        "points_earned": points,
        "correct_index": boost['correct_index'],
        "explanation": boost['explanation']
    }
    response.update(_progress_response(old_level, user, events))
    return jsonify(response), 200


# --- Teams (R2.1 Team Foundations) ---
# Schema-and-plumbing sprint only: no chat routes, no moments routes, no
# Rickie behavior, no UI. team_message and team_moment tables exist (see
# Database Models above) but have no routes yet — that's R2.4/R2.5.
#
# Team member cap = highest plan tier held by any CURRENT member of that
# team (TEAM_SYSTEM_BASELINE.md Section 12). Team-count cap = the joining
# user's own tier. Neither cap check uses row locking (contrast check_in's
# SELECT...FOR UPDATE) — a genuine race exists if two people join the same
# near-full team at the exact same instant. Accepted, not fixed, for this
# foundations sprint: low-traffic, low-probability, not the "no admin"-style
# decision this project reopens without a real incident driving it.

TEAM_FREE_MEMBER_CAP = 8
TEAM_PLUS_MEMBER_CAP = 25
TEAM_FREE_TEAM_COUNT_CAP = 10  # Plus: unlimited (no cap check at all)

CAMPFIRE_STAGE_THRESHOLDS = [
    (0, 'Kindling'),
    (100, 'Small Flame'),
    (300, 'Campfire'),
    (750, 'Bonfire'),
    (2000, 'Beacon'),
]

def _campfire_stage(total_missions):
    stage = CAMPFIRE_STAGE_THRESHOLDS[0][1]
    for threshold, name in CAMPFIRE_STAGE_THRESHOLDS:
        if total_missions >= threshold:
            stage = name
    return stage

def _generate_team_invite_code():
    alphabet = string.ascii_uppercase + string.digits
    for _ in range(20):
        code = ''.join(random.choice(alphabet) for _ in range(6))
        exists = db.session.execute(
            db.select(TeamInviteCode).where(TeamInviteCode.code == code)
        ).scalar_one_or_none()
        if not exists:
            return code
    abort(500)

def create_team_moment(team_id, moment_type, subject_user_id=None, metadata=None):
    """R2.4 Team Moments MVP -- the durable historical record
    TEAM_SYSTEM_BASELINE Section 10 already speced. Moments are history, not
    a feed: never records absence (no missed-day moment type exists), never
    ranks members (no leaderboard moment type exists). Caller commits;
    this only stages the row alongside whatever else that caller is doing."""
    moment = TeamMoment(
        team_id=team_id,
        moment_type=moment_type,
        subject_user_id=subject_user_id,
        moment_metadata=json.dumps(metadata) if metadata is not None else None,
    )
    db.session.add(moment)
    return moment

def _team_member_cap(team_id):
    has_plus_member = db.session.execute(
        db.select(TeamMembership.id)
        .join(User, User.id == TeamMembership.user_id)
        .where(TeamMembership.team_id == team_id, User.is_plus == True)
        .limit(1)
    ).scalar_one_or_none()
    return TEAM_PLUS_MEMBER_CAP if has_plus_member else TEAM_FREE_MEMBER_CAP

def _user_team_count_cap(user):
    return None if user.is_plus else TEAM_FREE_TEAM_COUNT_CAP


@app.route('/api/teams', methods=['POST'])
@jwt_required()
@limiter.limit("10 per minute")
def create_team():
    data = request.get_json()
    if not data or not data.get('name') or not data['name'].strip():
        return jsonify({"error": "Team name is required"}), 400

    user_id = int(get_jwt_identity())
    user = db.session.get(User, user_id)

    count_cap = _user_team_count_cap(user)
    if count_cap is not None:
        current_count = db.session.execute(
            db.select(db.func.count(TeamMembership.id)).where(TeamMembership.user_id == user_id)
        ).scalar()
        if current_count >= count_cap:
            return jsonify({"error": f"You've reached the {count_cap}-team limit for your plan"}), 403

    team = Team(name=data['name'].strip()[:100], created_by_user_id=user_id)
    db.session.add(team)
    db.session.flush()

    db.session.add(TeamMembership(team_id=team.id, user_id=user_id))
    db.session.add(TeamCampfire(team_id=team.id, total_team_missions=0))
    invite = TeamInviteCode(team_id=team.id, code=_generate_team_invite_code())
    db.session.add(invite)
    create_team_moment(team.id, 'team_created', subject_user_id=user_id)
    db.session.commit()

    return jsonify({
        "message": "Team created",
        "team": {"id": team.id, "name": team.name, "invite_code": invite.code}
    }), 201


@app.route('/api/teams', methods=['GET'])
@jwt_required()
def list_teams():
    user_id = int(get_jwt_identity())

    memberships = db.session.execute(
        db.select(TeamMembership).where(TeamMembership.user_id == user_id)
    ).scalars().all()

    result = []
    for m in memberships:
        team = db.session.get(Team, m.team_id)
        member_count = db.session.execute(
            db.select(db.func.count(TeamMembership.id)).where(TeamMembership.team_id == team.id)
        ).scalar()
        campfire = db.session.execute(
            db.select(TeamCampfire).where(TeamCampfire.team_id == team.id)
        ).scalar_one_or_none()
        total_missions = campfire.total_team_missions if campfire else 0
        result.append({
            "id": team.id,
            "name": team.name,
            "member_count": member_count,
            "campfire_stage": _campfire_stage(total_missions),
            "total_team_missions": total_missions,
        })

    return jsonify(result), 200


@app.route('/api/teams/lookup/<code>', methods=['GET'])
@jwt_required()
def lookup_team_by_code(code):
    invite = db.session.execute(
        db.select(TeamInviteCode).where(TeamInviteCode.code == code.strip().upper())
    ).scalar_one_or_none()
    if not invite:
        return jsonify({"error": "Invalid invite code"}), 404

    team = db.session.get(Team, invite.team_id)
    member_count = db.session.execute(
        db.select(db.func.count(TeamMembership.id)).where(TeamMembership.team_id == team.id)
    ).scalar()
    campfire = db.session.execute(
        db.select(TeamCampfire).where(TeamCampfire.team_id == team.id)
    ).scalar_one_or_none()
    total_missions = campfire.total_team_missions if campfire else 0

    return jsonify({
        "team_id": team.id,
        "name": team.name,
        "member_count": member_count,
        "campfire_stage": _campfire_stage(total_missions),
    }), 200


@app.route('/api/teams/<int:team_id>', methods=['GET'])
@jwt_required()
def get_team(team_id):
    user_id = int(get_jwt_identity())

    membership = db.session.execute(
        db.select(TeamMembership).where(TeamMembership.team_id == team_id, TeamMembership.user_id == user_id)
    ).scalar_one_or_none()
    if not membership:
        return jsonify({"error": "Forbidden"}), 403

    team = db.session.get(Team, team_id)
    if not team:
        abort(404)

    memberships = db.session.execute(
        db.select(TeamMembership).where(TeamMembership.team_id == team_id)
    ).scalars().all()
    members = []
    for m in memberships:
        member_user = db.session.get(User, m.user_id)
        members.append({
            "user_id": member_user.id,
            "username": member_user.username,
            "is_creator": member_user.id == team.created_by_user_id,
        })

    invite = db.session.execute(
        db.select(TeamInviteCode).where(TeamInviteCode.team_id == team_id)
    ).scalar_one_or_none()

    campfire = db.session.execute(
        db.select(TeamCampfire).where(TeamCampfire.team_id == team_id)
    ).scalar_one_or_none()
    total_missions = campfire.total_team_missions if campfire else 0

    return jsonify({
        "id": team.id,
        "name": team.name,
        "created_by_user_id": team.created_by_user_id,
        "is_creator": team.created_by_user_id == user_id,
        "members": members,
        "member_count": len(members),
        "member_cap": _team_member_cap(team_id),
        "invite_code": invite.code if invite else None,
        "campfire": {
            "total_team_missions": total_missions,
            "stage": _campfire_stage(total_missions),
        },
    }), 200


@app.route('/api/teams/<int:team_id>/join', methods=['POST'])
@jwt_required()
@limiter.limit("10 per minute")
def join_team(team_id):
    data = request.get_json()
    code = (data or {}).get('code', '').strip().upper()
    if not code:
        return jsonify({"error": "Invite code is required"}), 400

    user_id = int(get_jwt_identity())
    user = db.session.get(User, user_id)

    team = db.session.get(Team, team_id)
    if not team:
        abort(404)

    invite = db.session.execute(
        db.select(TeamInviteCode).where(TeamInviteCode.team_id == team_id)
    ).scalar_one_or_none()
    if not invite or invite.code != code:
        return jsonify({"error": "Invalid invite code"}), 403

    existing = db.session.execute(
        db.select(TeamMembership).where(TeamMembership.team_id == team_id, TeamMembership.user_id == user_id)
    ).scalar_one_or_none()
    if existing:
        return jsonify({"error": "Already a member of this team"}), 400

    count_cap = _user_team_count_cap(user)
    if count_cap is not None:
        current_count = db.session.execute(
            db.select(db.func.count(TeamMembership.id)).where(TeamMembership.user_id == user_id)
        ).scalar()
        if current_count >= count_cap:
            return jsonify({"error": f"You've reached the {count_cap}-team limit for your plan"}), 403

    member_cap = _team_member_cap(team_id)
    current_member_count = db.session.execute(
        db.select(db.func.count(TeamMembership.id)).where(TeamMembership.team_id == team_id)
    ).scalar()
    if current_member_count >= member_cap:
        return jsonify({"error": f"This team is at its {member_cap}-member limit"}), 403

    db.session.add(TeamMembership(team_id=team_id, user_id=user_id))
    create_team_moment(team_id, 'member_joined', subject_user_id=user_id)
    create_rickie_team_message(team_id, 'member_joined')
    db.session.commit()

    return jsonify({"message": "Joined team", "team_id": team_id}), 200


@app.route('/api/teams/<int:team_id>/leave', methods=['POST'])
@jwt_required()
def leave_team(team_id):
    user_id = int(get_jwt_identity())

    membership = db.session.execute(
        db.select(TeamMembership).where(TeamMembership.team_id == team_id, TeamMembership.user_id == user_id)
    ).scalar_one_or_none()
    if not membership:
        return jsonify({"error": "Not a member of this team"}), 404

    db.session.delete(membership)
    create_team_moment(team_id, 'member_left', subject_user_id=user_id)
    db.session.commit()

    return jsonify({"message": "Left team"}), 200


@app.route('/api/teams/<int:team_id>/members/<int:member_user_id>', methods=['DELETE'])
@jwt_required()
def remove_team_member(team_id, member_user_id):
    """Team System Baseline Section 4's first of exactly two creator safety
    powers (the second is rotate_team_invite below) -- the narrow, safety-
    scoped exception to "no admin," not a general moderation feature. Kept
    silent on purpose: no team_moment, no chat message, no Rickie reaction.
    Removal is a private safety action between the creator and whoever's
    being removed, not something to broadcast to the rest of the team."""
    user_id = int(get_jwt_identity())

    team = db.session.get(Team, team_id)
    if not team:
        abort(404)
    if team.created_by_user_id != user_id:
        return jsonify({"error": "Forbidden"}), 403
    if member_user_id == user_id:
        return jsonify({"error": "Use Leave Team to remove yourself"}), 400

    membership = db.session.execute(
        db.select(TeamMembership).where(TeamMembership.team_id == team_id, TeamMembership.user_id == member_user_id)
    ).scalar_one_or_none()
    if not membership:
        return jsonify({"error": "Not a member of this team"}), 404

    db.session.delete(membership)
    db.session.commit()

    return jsonify({"message": "Member removed"}), 200


@app.route('/api/teams/<int:team_id>/rotate-invite', methods=['POST'])
@jwt_required()
def rotate_team_invite(team_id):
    user_id = int(get_jwt_identity())

    team = db.session.get(Team, team_id)
    if not team:
        abort(404)
    if team.created_by_user_id != user_id:
        return jsonify({"error": "Forbidden"}), 403

    invite = db.session.execute(
        db.select(TeamInviteCode).where(TeamInviteCode.team_id == team_id)
    ).scalar_one_or_none()
    if not invite:
        abort(404)

    invite.code = _generate_team_invite_code()
    invite.rotated_at = datetime.utcnow()
    db.session.commit()

    return jsonify({"message": "Invite code rotated", "invite_code": invite.code}), 200


@app.route('/api/teams/<int:team_id>/campfire', methods=['GET'])
@jwt_required()
def get_team_campfire(team_id):
    user_id = int(get_jwt_identity())

    membership = db.session.execute(
        db.select(TeamMembership).where(TeamMembership.team_id == team_id, TeamMembership.user_id == user_id)
    ).scalar_one_or_none()
    if not membership:
        return jsonify({"error": "Forbidden"}), 403

    campfire = db.session.execute(
        db.select(TeamCampfire).where(TeamCampfire.team_id == team_id)
    ).scalar_one_or_none()
    if not campfire:
        abort(404)

    return jsonify({
        "team_id": team_id,
        "total_team_missions": campfire.total_team_missions,
        "stage": _campfire_stage(campfire.total_team_missions),
    }), 200


def _moment_display_text(moment_type, subject_username, metadata):
    """Kept deliberately plain -- not Rickie's voice. Rickie-voiced moment
    callbacks are a later sprint (team chat / Memory Book), not this one."""
    if moment_type == 'team_created':
        return f"{subject_username} created the team" if subject_username else "The team was created"
    if moment_type == 'member_joined':
        return f"{subject_username} joined the team" if subject_username else "A member joined"
    if moment_type == 'member_left':
        return f"{subject_username} left the team" if subject_username else "A member left"
    if moment_type == 'campfire_log_added':
        return f"{subject_username} added a log to the campfire" if subject_username else "A log was added to the campfire"
    if moment_type == 'campfire_stage_reached':
        stage = (metadata or {}).get('stage')
        return f"The campfire reached {stage}" if stage else "The campfire reached a new stage"
    return None


@app.route('/api/teams/<int:team_id>/moments', methods=['GET'])
@jwt_required()
def get_team_moments(team_id):
    user_id = int(get_jwt_identity())

    membership = db.session.execute(
        db.select(TeamMembership).where(TeamMembership.team_id == team_id, TeamMembership.user_id == user_id)
    ).scalar_one_or_none()
    if not membership:
        return jsonify({"error": "Forbidden"}), 403

    moments = db.session.execute(
        db.select(TeamMoment)
        .where(TeamMoment.team_id == team_id)
        .order_by(TeamMoment.occurred_at.desc())
    ).scalars().all()

    result = []
    for m in moments:
        subject_username = None
        if m.subject_user_id:
            subject_user = db.session.get(User, m.subject_user_id)
            subject_username = subject_user.username if subject_user else None
        metadata = json.loads(m.moment_metadata) if m.moment_metadata else None
        result.append({
            "moment_type": m.moment_type,
            "subject_username": subject_username,
            "occurred_at": m.occurred_at.isoformat(),
            "metadata": metadata,
            "display_text": _moment_display_text(m.moment_type, subject_username, metadata),
        })

    return jsonify(result), 200


TEAM_MESSAGE_MAX_LENGTH = 240

# R2.6 Rickie Team Reactions MVP -- fixed, pre-written templates only (no AI
# generation). Rare and warm by construction: wired to just 3 trigger points
# (member_joined, first-ever campfire log, campfire_stage_reached), never to
# every campfire_log_added -- see TEAM_SYSTEM_BASELINE's "meaningful events,
# not a feed" principle. Follows the same voice rules as RICKIE_LINES in
# app.js: never mentions absence, never pressures a streak, never compares
# or ranks members, no jokes at anyone's expense.
RICKIE_TEAM_MESSAGES = {
    'member_joined': [
        "Welcome to the campfire.",
        "Glad you're here.",
    ],
    'first_log': [
        "First log added. The fire is starting.",
    ],
    'campfire_stage_reached': [
        "The campfire grew brighter.",
        "You built this together.",
    ],
}


def create_rickie_team_message(team_id, trigger):
    """Stages a Rickie-voiced TeamMessage for one of RICKIE_TEAM_MESSAGES'
    fixed templates. Caller commits, same convention as create_team_moment."""
    body = random.choice(RICKIE_TEAM_MESSAGES[trigger])
    message = TeamMessage(
        team_id=team_id,
        sender_type='rickie',
        sender_user_id=None,
        body=body,
    )
    db.session.add(message)
    return message


def _serialize_team_message(m):
    sender_username = None
    if m.sender_type == 'user' and m.sender_user_id:
        sender = db.session.get(User, m.sender_user_id)
        sender_username = sender.username if sender else None
    return {
        "sender_type": m.sender_type,
        "sender_username": sender_username,
        "body": m.body,
        "created_at": m.created_at.isoformat(),
    }


@app.route('/api/teams/<int:team_id>/messages', methods=['GET'])
@jwt_required()
def get_team_messages(team_id):
    user_id = int(get_jwt_identity())

    membership = db.session.execute(
        db.select(TeamMembership).where(TeamMembership.team_id == team_id, TeamMembership.user_id == user_id)
    ).scalar_one_or_none()
    if not membership:
        return jsonify({"error": "Forbidden"}), 403

    messages = db.session.execute(
        db.select(TeamMessage)
        .where(TeamMessage.team_id == team_id)
        .order_by(TeamMessage.created_at.asc())
    ).scalars().all()

    return jsonify([_serialize_team_message(m) for m in messages]), 200


@app.route('/api/teams/<int:team_id>/messages', methods=['POST'])
@jwt_required()
@limiter.limit("30 per minute")
def post_team_message(team_id):
    user_id = int(get_jwt_identity())

    membership = db.session.execute(
        db.select(TeamMembership).where(TeamMembership.team_id == team_id, TeamMembership.user_id == user_id)
    ).scalar_one_or_none()
    if not membership:
        return jsonify({"error": "Forbidden"}), 403

    data = request.get_json(silent=True) or {}
    body = (data.get('body') or '').strip()
    if not body:
        return jsonify({"error": "message_required"}), 400
    if len(body) > TEAM_MESSAGE_MAX_LENGTH:
        return jsonify({"error": "message_too_long"}), 400

    message = TeamMessage(
        team_id=team_id,
        sender_type='user',
        sender_user_id=user_id,
        body=body,
    )
    db.session.add(message)
    db.session.commit()

    return jsonify(_serialize_team_message(message)), 201


# --- Coach v1 ---

_COACH_SYSTEM_PROMPT = """\
You are Rickie, a friendly raccoon who is StreakFit's mascot and coach. You help users \
understand how StreakFit works and expand on Today's Insight.

Voice: a friendly coach and health educator first — warm, encouraging, and clear. \
Sound like a playful raccoon talking to a friend, not documentation.

Rules:
- Never sarcastic toward users, and never childish — keep the tone approachable for \
kids, adults, and seniors alike.
- Never shame a user for missing a day or falling behind.
- Never diagnose a condition, assess an injury, or imply medical expertise — if \
something sounds like it needs that, say so plainly and suggest they check with a \
professional.
- You may include a small, light joke occasionally — at most one per answer — but \
skip it entirely if the user seems frustrated or the question is serious.
- If the user asks for a joke, a funny fact, or something silly, share one of the \
jokes provided to you in this prompt (verbatim or lightly adapted) instead of \
redirecting back to StreakFit features. Let the joke stand on its own. No mission \
redirect, no coaching pivot.
- Format every joke with line breaks: the setup on its own line, a blank line, then \
the punchline on its own line, a blank line, then one short reaction in Rickie's own \
voice — a self-aware raccoon, not a generic comedian. Never use "Ha!", "Classic!", \
or "Good one!" — instead sound like an actual raccoon, e.g. "My standards are low. \
I'm a raccoon." or "That joke was found in a dumpster." Plain text only — no \
buttons, no multiple choice, nothing hidden. For example:
Why did the dog do yoga?

Because it wanted to master downward dog.

🦝 Don't look at me. You asked for it.
- Ask at most one follow-up question, and only if it genuinely helps.

Format:
- Target 25-60 words by default. Hard cap: 100 words — go longer only if the user \
explicitly asks for more detail.
- Maximum 4 short paragraphs. Each paragraph is 1-2 sentences, never more.
- Prefer brevity over completeness. Mobile readability matters more than covering \
everything — if the user asked about one feature, answer only that.
- Use bullets for lists of more than two items.
- Never use markdown formatting. Do not use **bold**, *italic*, # headings, \
numbered markdown lists, or markdown links. Use plain text only.

Broad overview questions — like "How does StreakFit work?", "What is this?", \
"What do I do?", or "Explain the app" — get a short starter answer only, never a \
full feature tour: one line describing StreakFit, up to 3 bullets, then one offer \
to go deeper. 40-70 words max. Do not explain every feature unless the user \
specifically asks about it. For example:
StreakFit is a tiny daily health game.

• Do 5 simple exercises
• Answer a Brain Boost question
• Build your streak one day at a time

Want me to explain missions, streaks, or Brain Boost?

StreakFit features:

Daily Mission — 5 exercises chosen each day based on skill level. \
Completing all 5 counts as a completed mission. Refreshes at midnight.

Streak — the number of consecutive days a user has completed all 5 exercises. \
A streak stays alive if yesterday or today is complete. \
Missing both yesterday and today breaks the streak.

Best Streak — the highest streak the user has ever reached.

Total Missions — total count of days where all 5 exercises were completed.

Milestone Banners — shown when a user completes a mission at a streak milestone: \
Day 1, 7, 14, 30, 100. Celebratory, not evaluative.

Rise Again — a one-time screen shown when a user with a best streak of 7 or more \
returns after their streak has broken. It acknowledges the return. \
No statistics, no guilt, no comparison. Copy: "You came back. That's what matters."

Only answer questions about StreakFit features described above, Today's Insight, \
and — when asked — a family-friendly joke from the list provided to you.
Do not answer questions about fitness training, exercise substitutions, \
nutrition, diet, medical topics, Teams, or Campfire.
When a question is outside Rickie's allowed topics, return the exact refusal \
message and nothing else: \
"I'm focused on StreakFit and Today's Insight — I can't help with that one." \
"""


@app.route('/api/coach', methods=['POST'])
@jwt_required()
@limiter.limit("10 per day")
@limiter.limit("3 per minute")
def coach():
    data = request.get_json(silent=True) or {}
    message = (data.get('message') or '').strip()
    if not message:
        return jsonify({"error": "message_required"}), 400
    if len(message) > 500:
        return jsonify({"error": "message_too_long"}), 400

    context  = data.get('context') or {}
    ctx_type = context.get('type', 'general')
    if ctx_type not in ('general', 'insight'):
        return jsonify({"error": "invalid_context_type"}), 400

    if not _anthropic_api_key:
        return jsonify({"error": "coach_unavailable"}), 503

    system = _COACH_SYSTEM_PROMPT
    if ctx_type == 'insight':
        insight_text     = (context.get('insight_text') or '').strip()
        insight_category = (context.get('insight_category') or '').strip()
        if insight_text:
            system += (
                f"\n\nToday's Insight (category: {insight_category}): \"{insight_text}\"\n"
                "The user wants to know more about this insight. "
                "Add depth without restating it verbatim."
            )

    if any(word in message.lower() for word in _JOKE_TRIGGER_WORDS):
        sample = random.sample(RICKIE_JOKES, min(5, len(RICKIE_JOKES)))
        system += (
            "\n\nThe user seems to want a joke or something silly. Here are some "
            "options you can use (pick one, verbatim or lightly adapted):\n- "
            + "\n- ".join(sample)
        )

    try:
        client   = _anthropic_lib.Anthropic(api_key=_anthropic_api_key)
        response = client.messages.create(
            model='claude-haiku-4-5-20251001',
            max_tokens=512,
            system=system,
            messages=[{'role': 'user', 'content': message}]
        )
        reply = response.content[0].text
        return jsonify({"reply": reply}), 200
    except Exception:
        return jsonify({"error": "coach_unavailable"}), 503


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
    app.logger.error('Unhandled exception: %s', e, exc_info=True)
    return jsonify({"error": "Internal server error"}), 500


# --- Startup migration ---
# Runs flask db upgrade on every startup so Render free tier (no shell access)
# applies pending migrations automatically. Alembic is idempotent — already-applied
# migrations are skipped. Safe with multiple gunicorn workers (Alembic uses a DB lock).

with app.app_context():
    try:
        from flask_migrate import upgrade as _db_upgrade
        _db_upgrade()
    except Exception as _e:
        import logging
        logging.getLogger(__name__).error('Startup migration failed: %s', _e)


if __name__ == '__main__':
    app.run(host='0.0.0.0', port=int(os.environ.get('PORT', 5000)))
