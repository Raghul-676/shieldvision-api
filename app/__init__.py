from flask import Flask, redirect, url_for
from flask_cors import CORS
from app.models.database import db
from config import Config
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address

limiter = Limiter(key_func=get_remote_address, default_limits=[])

def create_app():
    app = Flask(__name__, template_folder='templates', static_folder='static')
    app.config.from_object(Config)

    # Allow requests from any origin (Android app, local Flask, browsers)
    CORS(app, supports_credentials=True, origins='*')

    db.init_app(app)
    limiter.init_app(app)

    from app.api.auth import auth_bp
    from app.api.cameras import cameras_bp
    from app.api.alerts import alerts_bp
    from app.api.pages import admin_bp, user_bp
    from app.api.gpu import gpu_bp

    app.register_blueprint(auth_bp)
    app.register_blueprint(cameras_bp)
    app.register_blueprint(alerts_bp)
    app.register_blueprint(admin_bp)
    app.register_blueprint(user_bp)
    app.register_blueprint(gpu_bp)

    @app.route('/')
    def index():
        from flask import session
        if 'user_id' in session:
            if session.get('role') == 'admin':
                return redirect(url_for('admin.dashboard'))
            return redirect(url_for('user.dashboard'))
        return redirect(url_for('auth.login_page'))

    with app.app_context():
        db.create_all()
        _seed_admin(app)

    return app

def _seed_admin(app):
    from app.models.database import User
    if not User.query.filter_by(role='admin').first():
        admin = User(username='admin', email='admin@shieldvision.local', role='admin')
        admin.set_password('admin123')
        db.session.add(admin)
        db.session.commit()
        print("[Setup] Default admin created: admin / admin123")
