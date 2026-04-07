from flask import Blueprint, render_template, session, redirect, url_for
from app.api.auth import admin_required, login_required
from app.models.database import Camera, User, Alert, UserCameraMapping

admin_bp = Blueprint('admin', __name__)
user_bp = Blueprint('user', __name__)

# ── Admin Routes ──────────────────────────────────────────────────────────────

@admin_bp.route('/admin/dashboard')
@admin_required
def dashboard():
    total_cameras = Camera.query.count()
    total_users = User.query.filter_by(role='user').count()
    total_alerts = Alert.query.count()
    recent_alerts = Alert.query.order_by(Alert.timestamp.desc()).limit(10).all()
    return render_template('admin/dashboard.html',
        total_cameras=total_cameras,
        total_users=total_users,
        total_alerts=total_alerts,
        recent_alerts=recent_alerts
    )

@admin_bp.route('/admin/cameras')
@admin_required
def cameras():
    cameras = Camera.query.all()
    users = User.query.filter_by(role='user').all()
    return render_template('admin/cameras.html', cameras=cameras, users=users)

@admin_bp.route('/admin/users')
@admin_required
def users():
    users = User.query.filter_by(role='user').all()
    return render_template('admin/users.html', users=users)

@admin_bp.route('/admin/alerts')
@admin_required
def alerts():
    cameras = Camera.query.all()
    return render_template('admin/alerts.html', cameras=cameras)

@admin_bp.route('/admin/camera/<int:cid>')
@admin_required
def camera_view(cid):
    """Admin can view any camera directly without needing to be assigned."""
    camera = Camera.query.get_or_404(cid)
    return render_template('user/camera_view.html', camera=camera)

# ── User Routes ───────────────────────────────────────────────────────────────

@user_bp.route('/user/dashboard')
@login_required
def dashboard():
    uid = session['user_id']
    mappings = UserCameraMapping.query.filter_by(user_id=uid).all()
    camera_ids = [m.camera_id for m in mappings]
    cameras = Camera.query.filter(Camera.id.in_(camera_ids), Camera.is_active == True).all()
    recent_alerts = Alert.query.filter(
        Alert.camera_id.in_(camera_ids)
    ).order_by(Alert.timestamp.desc()).limit(20).all()
    return render_template('user/dashboard.html', cameras=cameras, recent_alerts=recent_alerts)

@user_bp.route('/user/camera/<int:cid>')
@login_required
def camera_view(cid):
    uid = session['user_id']
    if session.get('role') != 'admin':
        mapping = UserCameraMapping.query.filter_by(user_id=uid, camera_id=cid).first()
        if not mapping:
            return redirect(url_for('user.dashboard'))
    camera = Camera.query.get_or_404(cid)
    return render_template('user/camera_view.html', camera=camera)
