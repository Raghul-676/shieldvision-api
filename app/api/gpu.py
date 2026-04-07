from flask import Blueprint, jsonify
from app.api.auth import login_required

gpu_bp = Blueprint('gpu', __name__)

@gpu_bp.route('/api/gpu/status')
@login_required
def gpu_status():
    try:
        from app.services.model_registry import GPU_INFO
        info = dict(GPU_INFO)
        cc = info.get('compute_capability', (0, 0))
        info['compute_capability'] = f"{cc[0]}.{cc[1]}"
        if info.get('available'):
            try:
                import torch
                info['vram_used_gb']     = round(torch.cuda.memory_allocated(0) / 1024**3, 2)
                info['vram_reserved_gb'] = round(torch.cuda.memory_reserved(0)  / 1024**3, 2)
                info['vram_free_gb']     = round(
                    (torch.cuda.get_device_properties(0).total_memory
                     - torch.cuda.memory_reserved(0)) / 1024**3, 2)
            except Exception:
                pass
        return jsonify(info)
    except ImportError:
        return jsonify({
            'available':    False,
            'name':         'API-only deployment',
            'vram_gb':      0,
            'fp16_enabled': False,
            'message':      'GPU not available in cloud deployment'
        })
