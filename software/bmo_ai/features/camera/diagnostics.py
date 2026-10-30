"""Bounded camera failure evidence, never SDK exception text or private paths."""
from ..contracts import FeatureError

PHOTO_STAGES = frozenset({'unknown', 'storage_prepare', 'camera_open', 'preview',
    'focus', 'still_capture', 'capture_result', 'frame_validation', 'storage',
    'review', 'complete', 'cancel'})
PHOTO_CODES = frozenset({'photo_failed', 'photo_running', 'photo_busy', 'photo_saved',
    'photo_saved_journal_pending', 'photo_saved_state_unknown', 'photo_interrupted',
    'photo_cancelled', 'camera_cancelled', 'camera_timeout', 'camera_off',
    'camera_open_failed', 'camera_unavailable', 'camera_busy', 'camera_real_disabled',
    'camera_close_unverified', 'camera_capture_failed', 'camera_focus_failed',
    'camera_timestamp_unavailable', 'photo_focus_failed', 'stale_photo',
    'invalid_camera_frame', 'invalid_photo', 'invalid_png', 'photo_storage_failed',
    'photo_store_unconfigured', 'photo_disk_full', 'photo_storage_limit',
    'photo_byte_limit', 'unsafe_photo_root', 'photo_journal_invalid',
    'photo_journal_full', 'photo_reservation_missing', 'photo_verification_failed',
    'request_id_conflict', 'photo_preview_unavailable', 'photo_review_unavailable',
    'photo_unavailable', 'journal_unavailable'})


class CameraOperationError(FeatureError):
    def __init__(self, code, stage):
        super().__init__(code if type(code) is str and code in PHOTO_CODES else 'photo_failed')
        self.stage = stage if type(stage) is str and stage in PHOTO_STAGES else 'unknown'


def photo_diagnostic(result, *, storage_saved=None):
    """Project a receipt into a safe, fixed-vocabulary UI/diagnostic contract.

    Older journal records have no stage. Do not invent focus/still evidence for
    their shared camera_timeout code.
    """
    if type(result) is not dict:
        return None
    code = result.get('code')
    code = code if type(code) is str and code in PHOTO_CODES else 'photo_failed'
    stage = result.get('stage')
    stage = stage if type(stage) is str and stage in PHOTO_STAGES else 'unknown'
    status = result.get('status')
    status = status if status in ('running', 'simulated', 'succeeded', 'failed',
        'cancelled', 'rejected', 'unknown') else 'unknown'
    return dict(stage=stage, code=code, status=status,
                saved=storage_saved if type(storage_saved) is bool else result.get('completed') is True,
                automatic_retry=False)
