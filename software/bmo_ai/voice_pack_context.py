"""Local, reviewed v10 script metadata; never audio generation or approval.

The public handoff transcript identifies translations and semantic pools. PCM
identity remains the OrganizedVoicePack loader's responsibility. A translated
caption is usable only for the exact selected clip ID and English transcript.
"""
from collections import Counter
from copy import deepcopy
from functools import lru_cache
import json
import math
from pathlib import Path
import re

from .captions import clean_caption


_PATH = Path(__file__).with_name('policies') / 'voice-pack-v10-context.json'
_STATUSES = frozenset(('connected', 'conditional', 'excluded'))
_PERFORMANCE_EMOTIONS = frozenset(('neutral', 'happy', 'sad', 'surprised', 'angry', 'crying'))
_IDENTIFIER = re.compile(r'[A-Za-z0-9_-]{1,96}\Z')
_SHA = re.compile(r'[a-f0-9]{64}\Z')


def _performance_valid(value):
    """A small static pose; archive directions and guessed phrase cues never run."""
    if (type(value) is not dict or set(value) - {'face_emotion', 'gaze'}
            or value.get('face_emotion') not in _PERFORMANCE_EMOTIONS):
        return False
    if 'gaze' in value:
        gaze = value['gaze']
        if (type(gaze) is not dict or set(gaze) != {'x', 'y', 'hold_seconds'}
                or any(type(gaze[k]) not in (int, float) or not math.isfinite(gaze[k]) for k in gaze)
                or abs(gaze['x']) > .12 or abs(gaze['y']) > .08
                or not .2 <= gaze['hold_seconds'] <= 2.):
            return False
    return True


@lru_cache(maxsize=1)
def _catalog():
    value = json.loads(_PATH.read_text(encoding='utf-8'))
    clips = value['clips']
    if value.get('version') != 1 or type(clips) is not dict or len(clips) != 142:
        raise ValueError('invalid_v10_context_catalog')
    for clip_id, row in clips.items():
        if (type(clip_id) is not str or type(row) is not dict
                or row.get('status') not in _STATUSES
                or type(row.get('reason')) is not str or not row['reason']
                or type(row.get('events')) is not list or not row['events']
                or any(type(event) is not str or not event for event in row['events'])
                or type(row.get('contexts')) is not list or not row['contexts']
                or type(row.get('emotions')) is not list or not row['emotions']
                or type(row.get('duration_seconds')) not in (int, float)
                or not 0 < row['duration_seconds'] <= 30):
            raise ValueError('invalid_v10_context_entry')
        if clip_id == 'RX01':
            if row.get('en') != '' or row.get('ko') != '':
                raise ValueError('nonverbal_caption_must_be_empty')
        elif not clean_caption(row.get('en')) or not clean_caption(row.get('ko')):
            raise ValueError('invalid_v10_caption')
        if 'performance' in row and not _performance_valid(row['performance']):
            raise ValueError('invalid_v10_performance')
    return clips


def context_catalog():
    """A caller-owned copy; changing a report cannot change runtime policy."""
    return deepcopy(_catalog())


def context_for(clip_id):
    return deepcopy(_catalog().get(clip_id))


def captions_for(clip_id, text):
    """Use the selected asset's own text; never translate a different take.

    A legacy/synthetic/custom transcript keeps its actual English caption but
    receives no guessed translation. RX01 remains nonverbal in both modes.
    """
    english = clean_caption(text)
    row = _catalog().get(clip_id)
    return dict(en=english, ko=clean_caption(row['ko'])
                if row is not None and type(text) is str and text == row['en'] else '')


def performance_for(clip_id, text, *, candidate_id, sha256):
    """Bind a reviewed local pose to this selected, hash-verified audio asset.

    The loader supplies the verified PCM SHA. No pose is guessed for a custom
    take or a changed transcript, and source archive drafts are never executed.
    The identity accompanies captions so the runtime can admit it only at the
    same player's actual start. This adds no phrase timing or listening approval.
    """
    row = _catalog().get(clip_id)
    if (row is None or type(text) is not str or text != row['en']
            or 'performance' not in row
            or type(candidate_id) is not str or not _IDENTIFIER.fullmatch(candidate_id)
            or type(sha256) is not str or not _SHA.fullmatch(sha256)):
        return None
    return dict(version=1, source='local_clip_plan', clip_id=clip_id,
                candidate_id=candidate_id, sha256=sha256, **deepcopy(row['performance']))


def validated_performance(value, identity):
    """Accept only the local plan for the exact accompanying caption identity."""
    if type(value) is not dict or type(identity) is not dict:
        return None
    if set(identity) != {'clip_id', 'candidate_id', 'sha256'}:
        return None
    clip = identity.get('clip_id')
    if type(clip) is not str:
        return None
    row = _catalog().get(clip)
    if row is None:
        return None
    expected = performance_for(clip, row['en'], candidate_id=identity.get('candidate_id'),
                               sha256=identity.get('sha256'))
    return deepcopy(expected) if expected is not None and value == expected else None


def event_groups():
    result = {}
    for clip_id, row in _catalog().items():
        if row['status'] == 'excluded':
            continue
        for event in row['events']:
            result.setdefault(event, []).append(clip_id)
    return {event: tuple(ids) for event, ids in result.items()}


def eligible(clip_id, event, *, emotion='neutral', context='settled'):
    """Additional v10 context gates, not proof of an observed device event."""
    row = _catalog().get(clip_id)
    if row is None:
        return True  # Preserve the legacy pack's independent matching policy.
    if row['status'] == 'excluded' or event not in row['events']:
        return False
    if row['emotions'] != ['any'] and emotion not in row['emotions']:
        return False
    if event in ('photo_saved', 'video_saved') and clip_id == 'REACTION_LINE_TADA_SUCCESS':
        return True  # The Host emits these only after verified save success.
    if row['contexts'] != ['any'] and context not in row['contexts']:
        return False
    if event == 'idle' and row['duration_seconds'] > row['max_duration_seconds']:
        return False
    return True


def usage_report():
    rows = [dict(clip_id=clip_id, status=row['status'], reason=row['reason'],
                 events=list(row['events']), contexts=list(row['contexts']),
                 duration_seconds=row['duration_seconds'])
            for clip_id, row in _catalog().items()]
    counts = Counter(row['status'] for row in rows)
    return dict(total=len(rows), counts={status: counts[status] for status in
                ('connected', 'conditional', 'excluded')}, clips=rows,
                basis='reviewed_script_and_runtime_event_policy',
                individual_listening_approval=False, physical_playback_verified=False)
