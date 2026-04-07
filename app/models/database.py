from datetime import datetime, timezone
from flask_sqlalchemy import SQLAlchemy
from werkzeug.security import generate_password_hash, check_password_hash
import re

db = SQLAlchemy()

# Matches rtsp://user:pass@host — used to strip credentials before exposing URLs
_RTSP_CRED_RE = re.compile(r'(rtsp[s]?://)([^@]+@)', re.IGNORECASE)

def _utcnow():
    """Timezone-aware UTC timestamp (replaces deprecated datetime.utcnow)."""
    return datetime.now(timezone.utc)

def _mask_rtsp(url: str) -> str:
    """Replace embedded credentials in an RTSP URL with '***'."""
    return _RTSP_CRED_RE.sub(r'\1***@', url)

def _validate_roi(roi) -> bool:
    """Return True if roi is None or a valid [x1,y1,x2,y2] list of floats in [0,1]."""
    if roi is None:
        return True
    if not isinstance(roi, list) or len(roi) != 4:
        return False
    try:
        return all(isinstance(v, (int, float)) and 0.0 <= float(v) <= 1.0 for v in roi)
    except (TypeError, ValueError):
        return False


# ── Models ────────────────────────────────────────────────────────────────────

class User(db.Model):
    __tablename__ = 'users'

    id            = db.Column(db.Integer, primary_key=True)
    username      = db.Column(db.String(64),  unique=True, nullable=False, index=True)
    email         = db.Column(db.String(120), unique=True, nullable=False, index=True)
    password_hash = db.Column(db.String(256), nullable=False)
    role          = db.Column(db.String(20),  nullable=False, default='user')   # 'admin' | 'user'
    is_active     = db.Column(db.Boolean,     nullable=False, default=True)
    created_at    = db.Column(db.DateTime(timezone=True), default=_utcnow, nullable=False)

    # Relationships
    camera_mappings = db.relationship(
        'UserCameraMapping',
        foreign_keys='UserCameraMapping.user_id',
        backref='user',
        lazy='select',
        cascade='all, delete-orphan',
    )

    def set_password(self, password: str) -> None:
        self.password_hash = generate_password_hash(password)

    def check_password(self, password: str) -> bool:
        return check_password_hash(self.password_hash, password)

    def to_dict(self) -> dict:
        return {
            'id':         self.id,
            'username':   self.username,
            'email':      self.email,
            'role':       self.role,
            'is_active':  self.is_active,
            'created_at': self.created_at.isoformat(),
        }

    def __repr__(self) -> str:
        return f'<User id={self.id} username={self.username!r} role={self.role!r}>'


class Camera(db.Model):
    __tablename__ = 'cameras'

    id         = db.Column(db.Integer, primary_key=True)
    name       = db.Column(db.String(100), nullable=False)
    rtsp_url   = db.Column(db.String(500), nullable=False)
    location   = db.Column(db.String(200), nullable=False, default='')
    is_active  = db.Column(db.Boolean, nullable=False, default=True)
    created_at = db.Column(db.DateTime(timezone=True), default=_utcnow, nullable=False)
    created_by = db.Column(db.Integer, db.ForeignKey('users.id', ondelete='SET NULL'), nullable=True)

    # Relationships
    model_mappings = db.relationship(
        'CameraModelMapping',
        backref='camera',
        lazy='select',
        cascade='all, delete-orphan',
    )
    user_mappings = db.relationship(
        'UserCameraMapping',
        foreign_keys='UserCameraMapping.camera_id',
        backref='camera',
        lazy='select',
        cascade='all, delete-orphan',
    )
    alerts = db.relationship(
        'Alert',
        backref='camera',
        lazy='select',
        cascade='all, delete-orphan',
    )

    def to_dict(self, mask_url: bool = True) -> dict:
        """
        Serialize camera to dict.
        mask_url=True (default) strips embedded RTSP credentials before returning.
        Pass mask_url=False only in internal/admin-only contexts.
        """
        return {
            'id':         self.id,
            'name':       self.name,
            'rtsp_url':   _mask_rtsp(self.rtsp_url) if mask_url else self.rtsp_url,
            'location':   self.location,
            'is_active':  self.is_active,
            'models':     [m.to_dict() for m in self.model_mappings],
            'created_at': self.created_at.isoformat(),
            'created_by': self.created_by,
        }

    def __repr__(self) -> str:
        return f'<Camera id={self.id} name={self.name!r} active={self.is_active}>'


class Alert(db.Model):
    __tablename__ = 'alerts'

    id            = db.Column(db.Integer, primary_key=True)
    camera_id     = db.Column(db.Integer, db.ForeignKey('cameras.id', ondelete='CASCADE'), nullable=False, index=True)
    alert_type    = db.Column(db.String(50), nullable=False, index=True)   # intrusion | fire | violence
    confidence    = db.Column(db.Float,   nullable=True)
    timestamp     = db.Column(db.DateTime(timezone=True), default=_utcnow, nullable=False, index=True)
    snapshot_path = db.Column(db.String(500), nullable=True)
    roi_triggered = db.Column(db.Boolean, nullable=False, default=False)

    def to_dict(self, camera_name: str | None = None) -> dict:
        """
        Accepts an optional pre-fetched camera_name to avoid lazy-loading
        (prevents N+1 queries when serializing alert lists).
        """
        return {
            'id':           self.id,
            'camera_id':    self.camera_id,
            'camera_name':  camera_name or (self.camera.name if self.camera else 'Unknown'),
            'alert_type':   self.alert_type,
            'confidence':   round(self.confidence, 3) if self.confidence is not None else None,
            'timestamp':    self.timestamp.isoformat(),
            'roi_triggered': self.roi_triggered,
        }

    def __repr__(self) -> str:
        return f'<Alert id={self.id} type={self.alert_type!r} camera_id={self.camera_id}>'


class UserCameraMapping(db.Model):
    """Many-to-many: which users can access which cameras."""
    __tablename__ = 'user_camera_mapping'

    id          = db.Column(db.Integer, primary_key=True)
    user_id     = db.Column(db.Integer, db.ForeignKey('users.id',   ondelete='CASCADE'), nullable=False)
    camera_id   = db.Column(db.Integer, db.ForeignKey('cameras.id', ondelete='CASCADE'), nullable=False)
    assigned_at = db.Column(db.DateTime(timezone=True), default=_utcnow, nullable=False)
    assigned_by = db.Column(db.Integer, db.ForeignKey('users.id',   ondelete='SET NULL'), nullable=True)

    __table_args__ = (
        db.UniqueConstraint('user_id', 'camera_id', name='uq_user_camera'),
    )

    def __repr__(self) -> str:
        return f'<UserCameraMapping user={self.user_id} camera={self.camera_id}>'


class CameraModelMapping(db.Model):
    """Which AI models are active on each camera, with optional per-model ROI."""
    __tablename__ = 'camera_model_mapping'

    id         = db.Column(db.Integer, primary_key=True)
    camera_id  = db.Column(db.Integer, db.ForeignKey('cameras.id', ondelete='CASCADE'), nullable=False)
    model_type = db.Column(db.String(50), nullable=False)   # intrusion | fire | violence
    roi        = db.Column(db.JSON, nullable=True)           # [x1, y1, x2, y2] normalized 0–1
    is_enabled = db.Column(db.Boolean, nullable=False, default=True)

    __table_args__ = (
        db.UniqueConstraint('camera_id', 'model_type', name='uq_camera_model'),
    )

    def set_roi(self, roi) -> None:
        """Validate and set ROI; raises ValueError on bad input."""
        if not _validate_roi(roi):
            raise ValueError(
                f'ROI must be None or a list of 4 floats in [0,1], got: {roi!r}'
            )
        self.roi = roi

    def to_dict(self) -> dict:
        return {
            'model_type': self.model_type,
            'roi':        self.roi,
            'is_enabled': self.is_enabled,
        }

    def __repr__(self) -> str:
        return f'<CameraModelMapping camera={self.camera_id} model={self.model_type!r} enabled={self.is_enabled}>'
