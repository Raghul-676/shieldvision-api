import re
import time
import logging
from datetime import datetime, timezone

from flask import Blueprint, request, jsonify, session, Response
from app.models.database import db, Camera, CameraModelMapping, UserCameraMapping, User, Alert, _validate_roi
from app.api.auth import admin_required, login_required, _get_current_user_id, _get_current_role

# Use real stream_manager when cv2/torch are available (local with GPU)
# Fall back to stub when deploying API-only to cloud (Render, Railway, etc.)
try:
    from app.services.stream_service import stream_manager
except ImportError:
    from app.services.stream_stub import stream_manager

cameras_bp = Blueprint('cameras', __name__)
logger = logging.getLogger(__name__)

# ── Constants ─────────────────────────────────────────────────────────────────
VALID_MODELS   = frozenset({'intrusion', 'fire', 'violence'})
_RTSP_RE = re.compile(r'^(rtsp[s]?|rtmp[s]?|http[s]?|/|[0-9]+$|.*youtube\.com|.*youtu\.be)', re.IGNORECASE)
_MAX_NAME_LEN  = 100
_MAX_LOC_LEN   = 200
_MAX_URL_LEN   = 500


# ── Input validation helpers ──────────────────────────────────────────────────

def _validate_camera_fields(name: str, rtsp_url: str, location: str = '') -> str | None:
    """Return an error string or None if all camera fields are valid."""
    if not name or not name.strip():
        return 'Camera name is required'
    if len(name) > _MAX_NAME_LEN:
        return f'Camera name must be ≤ {_MAX_NAME_LEN} characters'
    if not rtsp_url or not rtsp_url.strip():
        return 'RTSP URL is required'
    if len(rtsp_url) > _MAX_URL_LEN:
        return f'RTSP URL must be ≤ {_MAX_URL_LEN} characters'
    if not _RTSP_RE.match(rtsp_url.strip()):
        return 'URL must start with rtsp://, rtsps://, rtmp://, rtmps://, http://, https://, a YouTube URL, or be a device index'
    if len(location) > _MAX_LOC_LEN:
        return f'Location must be ≤ {_MAX_LOC_LEN} characters'
    return None


def _user_owns_camera(user_id: int, camera_id: int) -> bool:
    """Return True if the user has been assigned access to this camera."""
    return UserCameraMapping.query.filter_by(
        user_id=user_id, camera_id=camera_id
    ).first() is not None


def _require_camera_access(cid: int):
    """
    Return (camera, error_response) tuple.
    Admins can access any camera; regular users only their assigned ones.
    """
    cam = Camera.query.get(cid)
    if cam is None:
        return None, (jsonify({'error': 'Camera not found'}), 404)

    role = _get_current_role()
    if role != 'admin':
        uid = _get_current_user_id()
        if not _user_owns_camera(uid, cid):
            return None, (jsonify({'error': 'Access denied to this camera'}), 403)

    return cam, None


# ── Camera CRUD ───────────────────────────────────────────────────────────────

@cameras_bp.route('/api/cameras', methods=['GET'])
@admin_required
def list_cameras():
    """List all cameras. Admin only."""
    cameras = Camera.query.all()
    return jsonify([c.to_dict() for c in cameras])


@cameras_bp.route('/api/cameras', methods=['POST'])
@admin_required
def add_camera():
    """
    Add a new camera with optional AI model assignments.

    Body: { name, rtsp_url, location?, models?: ['intrusion','fire','violence'] }
    """
    data     = request.get_json(silent=True) or {}
    name     = str(data.get('name',     '')).strip()
    rtsp_url = str(data.get('rtsp_url', '')).strip()
    location = str(data.get('location', '')).strip()
    models   = data.get('models', [])

    err = _validate_camera_fields(name, rtsp_url, location)
    if err:
        return jsonify({'error': err}), 422

    # Validate model list
    invalid_models = [m for m in models if m not in VALID_MODELS]
    if invalid_models:
        return jsonify({'error': f'Invalid model types: {invalid_models}. Valid: {sorted(VALID_MODELS)}'}), 422

    cam = Camera(
        name=name,
        rtsp_url=rtsp_url,
        location=location,
        created_by=_get_current_user_id(),
    )
    db.session.add(cam)
    db.session.flush()   # get cam.id before adding mappings

    for mtype in set(models):   # deduplicate
        db.session.add(CameraModelMapping(camera_id=cam.id, model_type=mtype))

    db.session.commit()
    return jsonify(cam.to_dict()), 201


@cameras_bp.route('/api/cameras/<int:cid>', methods=['GET'])
@admin_required
def get_camera(cid):
    """Get a single camera by ID. Admin only."""
    cam = Camera.query.get_or_404(cid)
    return jsonify(cam.to_dict())


@cameras_bp.route('/api/cameras/<int:cid>', methods=['PUT'])
@admin_required
def update_camera(cid):
    """
    Update camera fields and/or replace its model assignments.

    Body (all optional): { name, rtsp_url, location, is_active, models }
    """
    cam  = Camera.query.get_or_404(cid)
    data = request.get_json(silent=True) or {}

    name     = str(data.get('name',     cam.name)).strip()
    rtsp_url = str(data.get('rtsp_url', cam.rtsp_url)).strip()
    location = str(data.get('location', cam.location)).strip()

    err = _validate_camera_fields(name, rtsp_url, location)
    if err:
        return jsonify({'error': err}), 422

    cam.name     = name
    cam.rtsp_url = rtsp_url
    cam.location = location

    if 'is_active' in data:
        if not isinstance(data['is_active'], bool):
            return jsonify({'error': 'is_active must be a boolean'}), 422
        cam.is_active = data['is_active']

    if 'models' in data:
        models = data['models']
        invalid_models = [m for m in models if m not in VALID_MODELS]
        if invalid_models:
            return jsonify({'error': f'Invalid model types: {invalid_models}'}), 422

        CameraModelMapping.query.filter_by(camera_id=cid).delete()
        for mtype in set(models):
            db.session.add(CameraModelMapping(camera_id=cid, model_type=mtype))

        if stream_manager.is_running(cid):
            stream_manager.update_models(cid, list(set(models)))

    db.session.commit()
    return jsonify(cam.to_dict())


@cameras_bp.route('/api/cameras/<int:cid>', methods=['DELETE'])
@admin_required
def delete_camera(cid):
    """Delete a camera and stop its stream. Admin only."""
    cam = Camera.query.get_or_404(cid)
    stream_manager.stop_camera(cid)
    db.session.delete(cam)
    db.session.commit()
    return jsonify({'status': 'deleted', 'id': cid})


# ── Model Assignment ──────────────────────────────────────────────────────────

@cameras_bp.route('/api/cameras/<int:cid>/models', methods=['GET'])
@admin_required
def get_camera_models(cid):
    """List AI models assigned to a camera."""
    Camera.query.get_or_404(cid)
    mappings = CameraModelMapping.query.filter_by(camera_id=cid).all()
    return jsonify([m.to_dict() for m in mappings])


@cameras_bp.route('/api/cameras/<int:cid>/models', methods=['PUT'])
@admin_required
def set_camera_models(cid):
    """
    Replace the full set of AI models for a camera.

    Body: { models: ['intrusion', 'fire', 'violence'] }
    """
    Camera.query.get_or_404(cid)
    data   = request.get_json(silent=True) or {}
    models = data.get('models', [])

    if not isinstance(models, list):
        return jsonify({'error': 'models must be a list'}), 422

    invalid = [m for m in models if m not in VALID_MODELS]
    if invalid:
        return jsonify({'error': f'Invalid model types: {invalid}. Valid: {sorted(VALID_MODELS)}'}), 422

    CameraModelMapping.query.filter_by(camera_id=cid).delete()
    for mtype in set(models):
        db.session.add(CameraModelMapping(camera_id=cid, model_type=mtype))

    db.session.commit()

    if stream_manager.is_running(cid):
        stream_manager.update_models(cid, list(set(models)))

    return jsonify({'camera_id': cid, 'models': list(set(models))})


@cameras_bp.route('/api/cameras/<int:cid>/models/<model_type>', methods=['DELETE'])
@admin_required
def remove_camera_model(cid, model_type):
    """Remove a single AI model from a camera."""
    if model_type not in VALID_MODELS:
        return jsonify({'error': f'Invalid model type: {model_type!r}'}), 422

    Camera.query.get_or_404(cid)
    deleted = CameraModelMapping.query.filter_by(
        camera_id=cid, model_type=model_type
    ).delete()

    if not deleted:
        return jsonify({'error': f'Model {model_type!r} not assigned to this camera'}), 404

    db.session.commit()
    return jsonify({'status': 'removed', 'model_type': model_type})


# ── User Assignment ───────────────────────────────────────────────────────────

@cameras_bp.route('/api/cameras/<int:cid>/users', methods=['GET'])
@admin_required
def get_camera_users(cid):
    """List users assigned to a camera with their details."""
    Camera.query.get_or_404(cid)
    mappings = (
        UserCameraMapping.query
        .filter_by(camera_id=cid)
        .join(User, UserCameraMapping.user_id == User.id)
        .add_columns(User.username, User.email, User.is_active)
        .all()
    )
    return jsonify([
        {
            'user_id':     m.UserCameraMapping.user_id,
            'username':    m.username,
            'email':       m.email,
            'is_active':   m.is_active,
            'assigned_at': m.UserCameraMapping.assigned_at.isoformat(),
        }
        for m in mappings
    ])


@cameras_bp.route('/api/cameras/<int:cid>/users', methods=['PUT'])
@admin_required
def assign_users(cid):
    """
    Replace the full set of users assigned to a camera.

    Body: { user_ids: [1, 2, 3] }
    Returns the list of user_ids that were actually assigned (skips invalid IDs).
    """
    Camera.query.get_or_404(cid)
    data     = request.get_json(silent=True) or {}
    user_ids = data.get('user_ids', [])

    if not isinstance(user_ids, list):
        return jsonify({'error': 'user_ids must be a list'}), 422

    # Validate all IDs are integers
    if not all(isinstance(uid, int) for uid in user_ids):
        return jsonify({'error': 'All user_ids must be integers'}), 422

    # Resolve which IDs actually exist and are regular users
    valid_users = User.query.filter(
        User.id.in_(user_ids),
        User.role == 'user',
    ).all()
    valid_ids   = {u.id for u in valid_users}
    skipped_ids = [uid for uid in user_ids if uid not in valid_ids]

    admin_id = _get_current_user_id()

    # Replace all existing assignments atomically
    UserCameraMapping.query.filter_by(camera_id=cid).delete()
    for uid in valid_ids:
        db.session.add(UserCameraMapping(
            user_id=uid,
            camera_id=cid,
            assigned_by=admin_id,
        ))

    db.session.commit()
    return jsonify({
        'camera_id': cid,
        'assigned':  list(valid_ids),
        'skipped':   skipped_ids,
    })


@cameras_bp.route('/api/cameras/<int:cid>/users/<int:uid>', methods=['DELETE'])
@admin_required
def remove_user_from_camera(cid, uid):
    """Remove a single user's access to a camera."""
    Camera.query.get_or_404(cid)
    deleted = UserCameraMapping.query.filter_by(
        camera_id=cid, user_id=uid
    ).delete()

    if not deleted:
        return jsonify({'error': 'This user is not assigned to this camera'}), 404

    db.session.commit()
    return jsonify({'status': 'removed', 'user_id': uid, 'camera_id': cid})


# ── ROI ───────────────────────────────────────────────────────────────────────

@cameras_bp.route('/api/cameras/<int:cid>/roi', methods=['POST'])
@login_required
def update_roi(cid):
    """
    Set or clear the ROI zone for a specific model on a camera.
    Users can only update ROI on cameras they are assigned to.

    Body: { model_type: 'intrusion', roi: [x1,y1,x2,y2] | null }
    """
    # Authorization: users can only touch their own cameras (fix for CWE-862)
    cam, err = _require_camera_access(cid)
    if err:
        return err

    data       = request.get_json(silent=True) or {}
    model_type = data.get('model_type', 'intrusion')
    roi        = data.get('roi')

    if model_type not in VALID_MODELS:
        return jsonify({'error': f'Invalid model type: {model_type!r}'}), 422

    if not _validate_roi(roi):
        return jsonify({'error': 'ROI must be null or a list of 4 floats in [0.0, 1.0]'}), 422

    mapping = CameraModelMapping.query.filter_by(
        camera_id=cid, model_type=model_type
    ).first()

    if not mapping:
        return jsonify({'error': f'Model {model_type!r} is not assigned to this camera'}), 404

    mapping.roi = roi
    db.session.commit()

    # Propagate to live stream if running.
    # If stream isn't running yet, the ROI is saved in DB and will be
    # loaded when the stream starts via start_stream() which reads cam.model_mappings.
    running = stream_manager.is_running(cid)
    logger.info(f'[ROI] cam={cid} model={model_type} roi={roi} stream_running={running} active={stream_manager.active_cameras()}')
    if running:
        all_mappings = CameraModelMapping.query.filter_by(camera_id=cid).all()
        roi_map = {m.model_type: m.roi for m in all_mappings if m.roi}
        logger.info(f'[ROI] Updating stream roi_map={roi_map}')
        stream_manager.update_roi(cid, roi_map)
    else:
        logger.info(f'[ROI] cam={cid} stream not running — ROI saved to DB, will apply on next stream start')

    return jsonify({'status': 'ok', 'model_type': model_type, 'roi': roi})


# ── Streaming ─────────────────────────────────────────────────────────────────

@cameras_bp.route('/api/cameras/<int:cid>/stream/start', methods=['POST'])
@login_required
def start_stream(cid):
    """
    Start AI inference stream for a camera.
    Users can only start streams for cameras they are assigned to.
    """
    cam, err = _require_camera_access(cid)
    if err:
        return err

    if not cam.is_active:
        return jsonify({'error': 'Camera is disabled'}), 400

    model_types = [m.model_type for m in cam.model_mappings if m.is_enabled]
    # Load existing ROI from DB — applies zones drawn before stream started
    roi_map = {m.model_type: m.roi for m in cam.model_mappings if m.roi}
    logger.info(f'[Stream] cam={cid} models={model_types} roi_map={roi_map}')

    from app.services.alert_service import broadcaster
    from flask import current_app
    # Capture the real app object NOW (inside request context)
    # so the background thread can use it safely
    _app = current_app._get_current_object()

    def on_alert(camera_id: int, alert_type: str, confidence: float, snapshot_path: str | None):
        logger.info(f'[Alert] cam={camera_id} type={alert_type} conf={confidence:.2f}')
        # Broadcast via SSE immediately
        broadcaster.broadcast({
            'type':        'ALERT',
            'camera_id':   camera_id,
            'camera_name': cam.name,
            'alert_type':  alert_type,
            'confidence':  round(confidence, 3),
            'timestamp':   datetime.now(timezone.utc).isoformat(),
        })
        logger.info(f'[Alert] Broadcast sent to {len(broadcaster._clients)} SSE clients')
        # Persist to DB using the captured app reference
        try:
            with _app.app_context():
                alert = Alert(
                    camera_id=camera_id,
                    alert_type=alert_type,
                    confidence=confidence,
                    snapshot_path=snapshot_path,
                    roi_triggered=True,
                )
                db.session.add(alert)
                db.session.commit()
                db.session.remove()   # release session back to pool
        except Exception as e:
            logger.warning(f'Alert DB persist failed: {e}')

    stream_manager.start_camera(cid, cam.rtsp_url, model_types, roi_map, on_alert)
    return jsonify({'status': 'started', 'camera_id': cid})


@cameras_bp.route('/api/cameras/<int:cid>/stream/stop', methods=['POST'])
@login_required
def stop_stream(cid):
    """
    Stop the stream for a camera.
    Users can only stop streams for cameras they are assigned to.
    """
    _, err = _require_camera_access(cid)
    if err:
        return err

    stream_manager.stop_camera(cid)
    return jsonify({'status': 'stopped', 'camera_id': cid})


@cameras_bp.route('/api/cameras/<int:cid>/feed')
@login_required
def video_feed(cid):
    """MJPEG stream — only available when running locally with GPU."""
    _, err = _require_camera_access(cid)
    if err:
        return err

    try:
        import cv2
    except ImportError:
        return jsonify({'error': 'Video streaming not available in API-only deployment'}), 503

    FEED_DELAY   = 1.0 / 25
    JPEG_QUALITY = 70

    def generate():
        last_sent = 0.0
        while True:
            try:
                gap = FEED_DELAY - (time.time() - last_sent)
                if gap > 0:
                    time.sleep(gap)
                frame = stream_manager.get_frame(cid)
                if frame is None:
                    time.sleep(0.01)
                    continue
                ret, buf = cv2.imencode('.jpg', frame,
                                        [cv2.IMWRITE_JPEG_QUALITY, JPEG_QUALITY])
                if ret:
                    last_sent = time.time()
                    yield (b'--frame\r\nContent-Type: image/jpeg\r\n\r\n'
                           + buf.tobytes() + b'\r\n')
            except GeneratorExit:
                break
            except Exception:
                time.sleep(0.05)

    return Response(
        generate(),
        mimetype='multipart/x-mixed-replace; boundary=frame',
        headers={
            'Cache-Control':     'no-cache, no-store, must-revalidate',
            'Pragma':            'no-cache',
            'X-Accel-Buffering': 'no',
        }
    )
