import queue
import threading
import json
import time

class AlertBroadcaster:
    """SSE broadcaster for real-time alerts to connected clients."""

    def __init__(self):
        self._clients = {}  # client_id -> queue
        self._lock = threading.Lock()

    def subscribe(self, client_id):
        q = queue.Queue(maxsize=50)
        with self._lock:
            self._clients[client_id] = q
        return q

    def unsubscribe(self, client_id):
        with self._lock:
            self._clients.pop(client_id, None)

    def broadcast(self, data: dict):
        with self._lock:
            dead = []
            for cid, q in self._clients.items():
                try:
                    q.put_nowait(data)
                except queue.Full:
                    dead.append(cid)
            for cid in dead:
                del self._clients[cid]

    def broadcast_to_user(self, user_id, camera_ids, data: dict):
        """Broadcast only to clients subscribed to specific cameras."""
        with self._lock:
            for cid, q in self._clients.items():
                # cid format: "user_{user_id}" or "cam_{camera_id}"
                send = False
                if cid == f"user_{user_id}":
                    send = True
                elif cid.startswith("cam_") and int(cid.split("_")[1]) in camera_ids:
                    send = True
                if send:
                    try:
                        q.put_nowait(data)
                    except queue.Full:
                        pass

    def generate_sse(self, client_id):
        q = self.subscribe(client_id)
        try:
            while True:
                try:
                    data = q.get(timeout=15)
                    yield f"data: {json.dumps(data)}\n\n"
                except queue.Empty:
                    yield "data: {}\n\n"  # heartbeat
        finally:
            self.unsubscribe(client_id)


broadcaster = AlertBroadcaster()
