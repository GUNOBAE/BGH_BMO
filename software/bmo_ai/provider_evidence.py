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


# Names are matched, never serialized from an arbitrary exception object. Read
# the original exception before the cloud adapter replaces its public message.
_EXCEPTION_CLASSES = {
    'CloudError': 'cloud_error', 'ExpressionFormatError': 'expression_format_error',
    'BrainResponseError': 'brain_response_error', 'TypeError': 'type_error',
    'ValueError': 'value_error', 'KeyError': 'key_error', 'AttributeError': 'attribute_error',
    'IndexError': 'index_error', 'RuntimeError': 'runtime_error', 'TimeoutError': 'timeout_error',
    'ConnectionError': 'connection_error', 'OSError': 'os_error', 'AssertionError': 'assertion_error',
    'AuthenticationError': 'authentication_error', 'PermissionDeniedError': 'permission_denied_error',
    'APITimeoutError': 'api_timeout_error', 'APIConnectionError': 'api_connection_error',
    'APIStatusError': 'api_status_error', 'RateLimitError': 'rate_limit_error',
    'BadRequestError': 'bad_request_error', 'InternalServerError': 'internal_server_error',
    'NotFoundError': 'not_found_error', 'UnprocessableEntityError': 'unprocessable_entity_error',
}


def exception_evidence(error):
    result = {'exception_class': _EXCEPTION_CLASSES.get(type(error).__name__, 'other')}
    status = getattr(error, 'status_code', None)
    if type(status) is int and 100 <= status <= 599:
        result['http_status'] = status
    return result


def observed_call(operation, evidence, substage):
    """One operation, recording its bounded phase/cost even if it raises.

    No response bodies, exception strings/chains or transport request IDs escape.
    This is observation only: no retry and no exception suppression.
    """
    import time
    if substage not in ('provider_request', 'response_parse', 'reply_apply'):
        raise ValueError('Unsupported provider observation stage')
    evidence['ai_substage'] = substage
    began = time.monotonic()
    try:
        return operation()
    except Exception as error:
        evidence.update(exception_evidence(error))
        raise
    finally:
        elapsed = max(0., (time.monotonic() - began) * 1000)
        previous = evidence.get(substage + '_ms', 0.)
        evidence[substage + '_ms'] = round(min(300000., elapsed + previous), 4)
