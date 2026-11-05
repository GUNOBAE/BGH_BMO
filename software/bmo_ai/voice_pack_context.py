"""Local, reviewed v10 script metadata; never audio generation or approval.

The public handoff transcript identifies translations and semantic pools. PCM
identity remains the OrganizedVoicePack loader's responsibility. A translated
caption is usable only for the exact selected clip ID and English transcript.
"""
from collections import Counter
from copy import deepcopy
from functools import lru_cache
import json
from pathlib import Path

from .captions import clean_caption


_PATH = Path(__file__).with_name('policies') / 'voice-pack-v10-context.json'
_STATUSES = frozenset(('connected', 'conditional', 'excluded'))


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
