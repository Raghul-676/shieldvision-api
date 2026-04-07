import uuid
from datetime import datetime

from flask import Blueprint, request, jsonify, Response, session
from sqlalchemy.orm import joinedload
from app.models.database import db, Alert, Camera, UserCameraMapping
from app.api.auth import login_required, admin_required, _get_current_user_id, _get_current_role
from app.services.alert_service import broadcaster

alerts_bp = Blueprint('alerts', __name__)

@alerts_bp.route('/api/alerts', methods=['GET'])
@login_required
def get_alerts():
    camera_id = request.args.get('camera_id', type=int)
    alert_type = request.args.get('alert_type')
    date_from = request.args.get('date_from')
    date_to = request.args.get('date_to')
    limit = request.args.get('limit', 100, type=int)

    # Eager-load camera to avoid N+1 queries when serializing
    query = Alert.query.options(joinedload(Alert.camera))

    # Non-admins can only see alerts from their assigned cameras
    if _get_current_role() != 'admin':
        uid = _get_current_user_id()
        assigned = UserCameraMapping.query.filter_by(user_id=uid).all()
        allowed_ids = [m.camera_id for m in assigned]
        query = query.filter(Alert.camera_id.in_(allowed_ids))

    if camera_id:
        query = query.filter(Alert.camera_id == camera_id)
    if alert_type:
        query = query.filter(Alert.alert_type == alert_type)
    if date_from:
        query = query.filter(Alert.timestamp >= datetime.fromisoformat(date_from))
    if date_to:
        query = query.filter(Alert.timestamp <= datetime.fromisoformat(date_to))

    # Cap limit to prevent accidental full-table dumps
    limit = min(limit, 500)
    alerts = query.order_by(Alert.timestamp.desc()).limit(limit).all()
    # Pass camera_name to avoid re-hitting the DB per alert
    return jsonify([a.to_dict(camera_name=a.camera.name if a.camera else 'Unknown') for a in alerts])

@alerts_bp.route('/api/alerts', methods=['POST'])
def create_alert():
    """Internal endpoint called by stream service to persist alerts."""
    data = request.json
    alert = Alert(
        camera_id=data['camera_id'],
        alert_type=data['alert_type'],
        confidence=data.get('confidence'),
        snapshot_path=data.get('snapshot_path'),
        roi_triggered=data.get('roi_triggered', False)
    )
    db.session.add(alert)
    db.session.commit()
    broadcaster.broadcast(alert.to_dict() | {'type': 'ALERT'})
    return jsonify(alert.to_dict()), 201

@alerts_bp.route('/api/alerts/<int:aid>', methods=['DELETE'])
@admin_required
def delete_alert(aid):
    alert = Alert.query.get_or_404(aid)
    db.session.delete(alert)
    db.session.commit()
    return jsonify({'status': 'deleted'})

@alerts_bp.route('/api/alerts/stats', methods=['GET'])
@login_required
def alert_stats():
    from sqlalchemy import func
    query = Alert.query
    if _get_current_role() != 'admin':
        uid = _get_current_user_id()
        assigned = UserCameraMapping.query.filter_by(user_id=uid).all()
        allowed_ids = [m.camera_id for m in assigned]
        query = query.filter(Alert.camera_id.in_(allowed_ids))

    stats = query.with_entities(
        Alert.alert_type, func.count(Alert.id)
    ).group_by(Alert.alert_type).all()
    return jsonify({row[0]: row[1] for row in stats})

@alerts_bp.route('/api/events')
@login_required
def sse_events():
    """Server-Sent Events stream for real-time alerts."""
    client_id = f"user_{_get_current_user_id()}_{uuid.uuid4().hex[:8]}"
    return Response(
        broadcaster.generate_sse(client_id),
        mimetype='text/event-stream',
        headers={
            'Cache-Control': 'no-cache',
            'X-Accel-Buffering': 'no'
        }
    )

@alerts_bp.route('/api/events/camera/<int:cid>')
@login_required
def sse_camera_events(cid):
    """SSE stream for a specific camera."""
    if _get_current_role() != 'admin':
        mapping = UserCameraMapping.query.filter_by(
            user_id=_get_current_user_id(), camera_id=cid
        ).first()
        if not mapping:
            return jsonify({'error': 'Access denied'}), 403

    client_id = f"cam_{cid}_{uuid.uuid4().hex[:8]}"
    return Response(
        broadcaster.generate_sse(client_id),
        mimetype='text/event-stream',
        headers={'Cache-Control': 'no-cache', 'X-Accel-Buffering': 'no'}
    )
