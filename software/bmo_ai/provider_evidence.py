"""Bounded provider request/response evidence: counts and enums, never content.

Adapters call this at their FINAL transport boundary, before decoding or stripping
structured metadata. No retries, logging, provider IDs or raw error fields live here.
"""
import json


_STATUSES = frozenset(('completed', 'incomplete', 'failed', 'cancelled', 'queued', 'in_progress'))
_REASONS = frozenset(('max_output_tokens', 'content_filter'))


def _field(value, key, default=None):
    return value.get(key, default) if type(value) is dict else getattr(value, key, default)


def _count(value, ceiling=1000000):
    return value if type(value) is int and 0 <= value <= ceiling else None


def request_evidence(request):
    result = {}
    maximum = _count(request.get('max_output_tokens'), 8192)
    if maximum is not None:
        result['max_output_tokens'] = maximum
    instructions = request.get('instructions')
    if type(instructions) is str:
        result['request_instruction_chars'] = min(len(instructions), 1000000)
    inputs = request.get('input')
    if type(inputs) in (list, tuple):
        result['request_input_chars'] = min(sum(len(x['content']) for x in inputs
            if type(x) is dict and type(x.get('content')) is str), 1000000)
    text = request.get('text')
    format = text.get('format') if type(text) is dict else None
    schema = format.get('schema') if type(format) is dict else None
    if type(schema) is dict:
        try:
            result['request_schema_chars'] = min(len(json.dumps(schema, separators=(',', ':'),
                ensure_ascii=False, allow_nan=False)), 1000000)
        except (ValueError, TypeError, RecursionError):
            pass
    return result


def response_evidence(response):
    status = _field(response, 'status')
    result = {'response_status': status if type(status) is str and status in _STATUSES else 'unknown'}
    incomplete = _field(response, 'incomplete_details')
    reason = _field(incomplete, 'reason')
    result['incomplete_reason'] = (reason if type(reason) is str and reason in _REASONS
        else 'unknown' if incomplete else 'none')
    output = _field(response, 'output_text')
    if type(output) is str:
        result['response_output_chars'] = min(len(output), 1000000)
    usage = _field(response, 'usage')
    for name in ('input_tokens', 'output_tokens'):
        count = _count(_field(usage, name))
        if count is not None:
            result[name] = count
    count = _count(_field(_field(usage, 'output_tokens_details'), 'reasoning_tokens'))
    if count is not None:
        result['reasoning_tokens'] = count
    return result
