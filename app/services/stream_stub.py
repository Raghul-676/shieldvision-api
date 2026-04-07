"""
stream_stub.py
──────────────
Lightweight stub for stream_manager used when deploying API-only
to cloud platforms (Render, Railway, etc.) without cv2/torch installed.

All streaming methods return safe no-op responses so the API endpoints
that reference stream_manager still work — they just report the stream
as not running, which is correct since there is no local GPU/camera.
"""

import logging
logger = logging.getLogger(__name__)


class _StubStreamManager:
    """No-op stream manager for API-only deployments."""

    def start_camera(self, *args, **kwargs):
        logger.info('[Stub] start_camera called — streaming not available in API-only mode')

    def stop_camera(self, camera_id):
        pass

    def stop_all(self):
        pass

    def get_frame(self, camera_id):
        return None

    def is_running(self, camera_id):
        return False

    def update_roi(self, camera_id, roi):
        pass

    def update_models(self, camera_id, model_types):
        pass

    def active_cameras(self):
        return []

    def status(self):
        return {}


stream_manager = _StubStreamManager()
