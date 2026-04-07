import os
import secrets
from datetime import timedelta

class Config:
    _secret     = os.environ.get('SECRET_KEY')
    _jwt_secret = os.environ.get('JWT_SECRET_KEY')

    # Accept any of these as non-production environments
    _dev_envs = {'development', 'dev', 'test', 'render', None}
    _env      = os.environ.get('FLASK_ENV')

    # Generate random key if not set and not strictly production
    # On Render: set SECRET_KEY in environment variables dashboard
    SECRET_KEY     = _secret     or secrets.token_hex(32)
    JWT_SECRET_KEY = _jwt_secret or secrets.token_hex(32)

    SQLALCHEMY_DATABASE_URI = os.environ.get(
        'DATABASE_URL',
        'postgresql://postgres:password@localhost:5432/shieldvision'
    )
    # Render provides DATABASE_URL with postgres:// prefix — SQLAlchemy needs postgresql://
    if SQLALCHEMY_DATABASE_URI.startswith('postgres://'):
        SQLALCHEMY_DATABASE_URI = SQLALCHEMY_DATABASE_URI.replace('postgres://', 'postgresql://', 1)

    SQLALCHEMY_TRACK_MODIFICATIONS = False
    JWT_ACCESS_TOKEN_EXPIRES  = timedelta(hours=8)
    JWT_REFRESH_TOKEN_EXPIRES = timedelta(days=7)

    CSV_MAX_BYTES          = 1 * 1024 * 1024
    CSV_MAX_ROWS           = 500
    ALLOWED_CSV_EXTENSIONS = {'csv'}

    RATELIMIT_DEFAULT     = '200 per day;50 per hour'
    RATELIMIT_STORAGE_URI = 'memory://'

    UPLOAD_FOLDER      = os.path.join(os.path.dirname(__file__), 'uploads')
    MODELS_FOLDER      = os.path.join(os.path.dirname(__file__), 'models')
    MAX_CONTENT_LENGTH = 500 * 1024 * 1024
