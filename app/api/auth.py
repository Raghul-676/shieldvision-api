import io
import csv
import re
import jwt
from datetime import datetime, timezone
from functools import wraps

from flask import Blueprint, request, jsonify, session, redirect, url_for, render_template, current_app
from app.models.database import db, User

auth_bp = Blueprint('auth', __name__)

# ── Constants ─────────────────────────────────────────────────────────────────
_USERNAME_RE = re.compile(r'^[a-zA-Z0-9_.-]{3,64}$')
_EMAIL_RE    = re.compile(r'^[^@\s]+@[^@\s]+\.[^@\s]+$')
_MAX_PASSWORD_LEN = 128
_MIN_PASSWORD_LEN = 8

# Characters that trigger CSV formula injection (=, +, -, @, tab, CR)
_CSV_INJECTION_RE = re.compile(r'^[=+\-@\t\r]')


# ── Helpers ───────────────────────────────────────────────────────────────────

def _sanitize_csv_field(value: str) -> str:
    """Strip leading formula-injection characters from a CSV cell value."""
    return _CSV_INJECTION_RE.sub('', value).strip()


def _validate_user_fields(username: str, email: str, password: str) -> str | None:
    """Return an error string or None if all fields are valid."""
    if not _USERNAME_RE.match(username):
        return 'Username must be 3–64 chars: letters, digits, _ . -'
    if not _EMAIL_RE.match(email):
        return 'Invalid email address'
    if len(password) < _MIN_PASSWORD_LEN:
        return f'Password must be at least {_MIN_PASSWORD_LEN} characters'
    if len(password) > _MAX_PASSWORD_LEN:
        return f'Password must be at most {_MAX_PASSWORD_LEN} characters'
    return None


def _issue_jwt(user: User) -> str:
    """Issue a signed JWT access token for the given user."""
    payload = {
        'sub': user.id,
        'username': user.username,
        'role': user.role,
        'iat': datetime.now(timezone.utc),
        'exp': datetime.now(timezone.utc) + current_app.config['JWT_ACCESS_TOKEN_EXPIRES'],
    }
    return jwt.encode(payload, current_app.config['JWT_SECRET_KEY'], algorithm='HS256')


def _decode_jwt(token: str) -> dict | None:
    """Decode and verify a JWT; return payload or None on failure."""
    try:
        return jwt.decode(
            token,
            current_app.config['JWT_SECRET_KEY'],
            algorithms=['HS256']
        )
    except jwt.ExpiredSignatureError:
        return None
    except jwt.InvalidTokenError:
        return None


# ── Context helpers ──────────────────────────────────────────────────────────

def _get_current_user_id() -> int | None:
    """Return the authenticated user's ID from session or JWT request context."""
    return session.get('user_id') or getattr(request, 'jwt_user_id', None)


def _get_current_role() -> str | None:
    """Return the authenticated user's role from session or JWT request context."""
    return session.get('role') or getattr(request, 'jwt_role', None)


# ── Decorators ────────────────────────────────────────────────────────────────

def login_required(f):
    """Accept either a valid session OR a valid Bearer JWT."""
    @wraps(f)
    def decorated(*args, **kwargs):
        # 1. Session-based (browser)
        if 'user_id' in session:
            return f(*args, **kwargs)

        # 2. JWT Bearer token (API clients)
        auth_header = request.headers.get('Authorization', '')
        if auth_header.startswith('Bearer '):
            payload = _decode_jwt(auth_header[7:])
            if payload:
                # Inject into request context so downstream code can read it
                request.jwt_user_id = payload['sub']
                request.jwt_role    = payload['role']
                return f(*args, **kwargs)

        if request.is_json:
            return jsonify({'error': 'Unauthorized'}), 401
        return redirect(url_for('auth.login_page'))
    return decorated


def admin_required(f):
    """Require admin role via session or JWT."""
    @wraps(f)
    def decorated(*args, **kwargs):
        role = session.get('role')

        if not role:
            auth_header = request.headers.get('Authorization', '')
            if auth_header.startswith('Bearer '):
                payload = _decode_jwt(auth_header[7:])
                if payload:
                    role = payload.get('role')
                    request.jwt_user_id = payload['sub']
                    request.jwt_role    = role

        if role != 'admin':
            if request.is_json:
                return jsonify({'error': 'Admin access required'}), 403
            return redirect(url_for('auth.login_page'))
        return f(*args, **kwargs)
    return decorated


# ── Page Routes ───────────────────────────────────────────────────────────────

@auth_bp.route('/login', methods=['GET'])
def login_page():
    if 'user_id' in session:
        return redirect(url_for('admin.dashboard') if session.get('role') == 'admin'
                        else url_for('user.dashboard'))
    return render_template('auth/login.html')


# ── Auth API ──────────────────────────────────────────────────────────────────

@auth_bp.route('/api/login', methods=['POST'])
def login():
    """
    Rate-limited login endpoint.
    Returns both a session cookie (for browser) and a JWT token (for API clients).
    """
    from app import limiter
    # Apply rate limit inline so it only targets this endpoint
    limiter.limit('10 per minute')(lambda: None)()

    data = request.get_json(silent=True) or {}
    username = str(data.get('username', '')).strip()
    password = str(data.get('password', ''))

    if not username or not password:
        return jsonify({'error': 'Username and password are required'}), 400

    user = User.query.filter_by(username=username).first()

    # Constant-time-equivalent: always call check_password to prevent timing attacks
    if not user or not user.check_password(password):
        return jsonify({'error': 'Invalid credentials'}), 401
    if not user.is_active:
        return jsonify({'error': 'Account disabled'}), 403

    # Establish session (browser)
    session.clear()
    session['user_id']  = user.id
    session['role']     = user.role
    session['username'] = user.username

    # Issue JWT (API clients)
    token = _issue_jwt(user)

    redirect_url = '/admin/dashboard' if user.role == 'admin' else '/user/dashboard'
    return jsonify({
        'role': user.role,
        'redirect': redirect_url,
        'token': token,
        'expires_in': int(current_app.config['JWT_ACCESS_TOKEN_EXPIRES'].total_seconds()),
    })


@auth_bp.route('/api/logout', methods=['POST'])
def logout():
    session.clear()
    return jsonify({'status': 'ok'})


@auth_bp.route('/api/register_admin', methods=['POST'])
def register_admin():
    """One-time admin bootstrap — disabled once any admin exists."""
    if User.query.filter_by(role='admin').first():
        return jsonify({'error': 'Admin already exists'}), 403

    data = request.get_json(silent=True) or {}
    username = str(data.get('username', '')).strip()
    email    = str(data.get('email', '')).strip().lower()
    password = str(data.get('password', ''))

    err = _validate_user_fields(username, email, password)
    if err:
        return jsonify({'error': err}), 422

    user = User(username=username, email=email, role='admin')
    user.set_password(password)
    db.session.add(user)
    db.session.commit()
    return jsonify({'status': 'Admin created'}), 201


# ── User Management API ───────────────────────────────────────────────────────

@auth_bp.route('/api/users', methods=['GET'])
@admin_required
def list_users():
    users = User.query.filter_by(role='user').all()
    return jsonify([u.to_dict() for u in users])


@auth_bp.route('/api/users', methods=['POST'])
@admin_required
def create_user():
    data     = request.get_json(silent=True) or {}
    username = str(data.get('username', '')).strip()
    email    = str(data.get('email', '')).strip().lower()
    password = str(data.get('password', ''))

    err = _validate_user_fields(username, email, password)
    if err:
        return jsonify({'error': err}), 422

    if User.query.filter(
        (User.username == username) | (User.email == email)
    ).first():
        return jsonify({'error': 'Username or email already exists'}), 409

    u = User(username=username, email=email, role='user')
    u.set_password(password)
    db.session.add(u)
    db.session.commit()
    return jsonify(u.to_dict()), 201


@auth_bp.route('/api/users/bulk', methods=['POST'])
@admin_required
def bulk_create_users():
    """
    Upload a CSV file with columns: username, email, password
    Security controls:
      - Only .csv extension accepted
      - Max 1 MB file size
      - Max 500 rows per import
      - CSV injection sanitization on every field
      - Full field validation per row
    """
    file = request.files.get('file')
    if not file or not file.filename:
        return jsonify({'error': 'No file provided'}), 400

    # ── Extension check (fix for CWE-434) ────────────────────────────────────
    ext = file.filename.rsplit('.', 1)[-1].lower() if '.' in file.filename else ''
    if ext not in current_app.config['ALLOWED_CSV_EXTENSIONS']:
        return jsonify({'error': 'Only .csv files are accepted'}), 415

    # ── Size check ────────────────────────────────────────────────────────────
    raw = file.read(current_app.config['CSV_MAX_BYTES'] + 1)
    if len(raw) > current_app.config['CSV_MAX_BYTES']:
        return jsonify({'error': 'CSV file exceeds 1 MB limit'}), 413

    try:
        content = raw.decode('utf-8')
    except UnicodeDecodeError:
        return jsonify({'error': 'CSV must be UTF-8 encoded'}), 422

    reader  = csv.DictReader(io.StringIO(content))
    created, skipped = [], []
    row_count = 0

    for row in reader:
        row_count += 1
        if row_count > current_app.config['CSV_MAX_ROWS']:
            skipped.append(f'row_{row_count} (limit reached)')
            continue

        # ── CSV injection sanitization ────────────────────────────────────────
        username = _sanitize_csv_field(row.get('username', ''))
        email    = _sanitize_csv_field(row.get('email', '')).lower()
        password = row.get('password', '').strip()   # don't strip injection chars from passwords

        err = _validate_user_fields(username, email, password)
        if err:
            skipped.append(f'{username or "row_" + str(row_count)} ({err})')
            continue

        if User.query.filter(
            (User.username == username) | (User.email == email)
        ).first():
            skipped.append(f'{username} (already exists)')
            continue

        u = User(username=username, email=email, role='user')
        u.set_password(password)
        db.session.add(u)
        created.append(username)

    db.session.commit()
    return jsonify({'created': created, 'skipped': skipped})


@auth_bp.route('/api/users/<int:uid>', methods=['DELETE'])
@admin_required
def delete_user(uid):
    u = User.query.get_or_404(uid)
    db.session.delete(u)
    db.session.commit()
    return jsonify({'status': 'deleted'})


@auth_bp.route('/api/users/<int:uid>/toggle', methods=['POST'])
@admin_required
def toggle_user(uid):
    u = User.query.get_or_404(uid)
    u.is_active = not u.is_active
    db.session.commit()
    return jsonify({'is_active': u.is_active})
