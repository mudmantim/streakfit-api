import os
from datetime import datetime, date, timedelta
from flask import Flask, request, jsonify, abort
from flask_sqlalchemy import SQLAlchemy
from werkzeug.security import generate_password_hash, check_password_hash
from flask_jwt_extended import JWTManager, create_access_token, jwt_required, get_jwt_identity

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
jwt = JWTManager(app)

# --- Database Models ---
class User(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False)
    password_hash = db.Column(db.String(256), nullable=False)
    challenges = db.relationship('Challenge', backref='owner', lazy=True)

class Challenge(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    title = db.Column(db.String(100), nullable=False)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    current_streak = db.Column(db.Integer, default=0)
    longest_streak = db.Column(db.Integer, default=0)
    last_check_in = db.Column(db.Date, nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

with app.app_context():
    db.create_all()

# --- Health Check ---
@app.route('/health', methods=['GET'])
def health():
    return jsonify({"status": "ok"}), 200

# --- API Routes ---
@app.route('/api/register', methods=['POST'])
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
def login():
    data = request.get_json()
    if not data or not data.get('username') or not data.get('password'):
        return jsonify({"error": "Missing credentials"}), 400

    user = User.query.filter_by(username=data['username']).first()
    if not user or not check_password_hash(user.password_hash, data['password']):
        return jsonify({"error": "Invalid credentials"}), 401

    access_token = create_access_token(identity=str(user.id))
    return jsonify({"access_token": access_token}), 200

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
        # Consecutive day check-in
        challenge.current_streak += 1
    elif challenge.last_check_in is None:
        # First check-in ever
        challenge.current_streak = 1
    else:
        # Streak was broken
        challenge.current_streak = 1
        
    if challenge.current_streak > challenge.longest_streak:
        challenge.longest_streak = challenge.current_streak
        
    challenge.last_check_in = today
    db.session.commit()
    
    return jsonify({
        "message": "Check-in successful", 
        "current_streak": challenge.current_streak,
        "longest_streak": challenge.longest_streak
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
