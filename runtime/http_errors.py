"""Bounded provider diagnostics; never persist raw HTTP bodies or request text."""
import json
import re
import hashlib

MAX_BODY = 16384
MAX_MESSAGE = 1600
STATUSES = set('OK CANCELLED UNKNOWN INVALID_ARGUMENT DEADLINE_EXCEEDED NOT_FOUND ALREADY_EXISTS PERMISSION_DENIED UNAUTHENTICATED RESOURCE_EXHAUSTED FAILED_PRECONDITION ABORTED OUT_OF_RANGE UNIMPLEMENTED INTERNAL UNAVAILABLE DATA_LOSS'.split())
CODES = STATUSES | set('invalid_request_error context_length_exceeded unsupported_parameter model_not_found rate_limit_exceeded invalid_api_key insufficient_quota server_error'.split())
PARAMETERS = set('model messages content contents parts text role system systemInstruction generationConfig generation_config parameters max_tokens max_completion_tokens max_output_tokens maxOutputTokens reasoning_effort thinkingConfig thinkingLevel temperature top_p top_k stream stream_options include_usage n stop presence_penalty frequency_penalty logprobs top_logprobs tools tool_choice response_format seed'.split())
# Provider prose is untrusted and can echo arbitrary source fragments (including
# very short or normalized text). Persist only a fixed diagnostic vocabulary;
# retain word order/negation, but replace other words and numeric values.
WORDS = {word.casefold() for word in PARAMETERS | STATUSES | CODES} | set('''a an the this that these those is are was were be been being must should can cannot may could
not no none all any and or but if then than to of for from in on at with without by as per
it its you your we our requested request requests response responses value values number numbers
invalid valid unsupported supported support supports missing required requires require received expected
parameter parameters argument arguments field fields input inputs output outputs token tokens maximum
minimum max min limit limits limited exceeded exceeds exceed exceeding length size total context window
remaining available reserved reserve capacity budget too large small long short fit fits within greater
less equal between above below up over under both sum combined including includes include plus minus
please reduce increase set use specified provided allowed accepted rejected endpoint path route location
project model models found unknown incorrect bad error errors failed failure quota rate billing permission
permissions denied authentication authorized unauthorized enabled disabled retry again later because however
only currently request_id status code count counts completion completions prompt prompts message messages
redacted credential address omitted private data server service resource resources unavailable supported
'''.split())


def _contents(value):
    if isinstance(value, dict):
        for key, item in value.items():
            if key in {'content', 'text', 'system'} and isinstance(item, str):
                yield item
            elif isinstance(item, (dict, list)):
                yield from _contents(item)
    elif isinstance(value, list):
        for item in value:
            yield from _contents(item)


def _safe(value, secrets, contents):
    if not isinstance(value, str):
        return None
    for secret in secrets:
        if secret:
            value = value.replace(str(secret), '[redacted]')
    value = re.sub(r'(?i)\b(?:(?:api[_-]?key|access_token|authorization)\s*[=:]\s*(?:bearer\s+)?|bearer\s+)[^\s,;]+', '[redacted credential]', value)
    value = re.sub(r'\b(?:sk-|AIza)[A-Za-z0-9_-]{16,}', '[redacted credential]', value)
    value = re.sub(r'https?://[^\s<>]+|\bprojects/[^\s/]+|\b[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}\b', '[redacted address]', value)
    value = re.sub(r'(?:/Users/|/home/)[^\s]+', '[redacted path]', value)
    value = value[:MAX_MESSAGE]
    # Provider messages can reflect all or part of a prompt. Omit that message
    # entirely when a fragment matches; retain separate status/field diagnostics.
    for content in contents:
        if ((8 <= len(content) < 24 and content in value)
                or any(value[i:i+24] in content for i in range(max(0, len(value)-23)))):
            return '[message omitted: includes request content]'
    value = re.sub(r'[\x00-\x1f\x7f]', ' ', value)
    value = re.sub(r'\w+', lambda match: match[0] if match[0].casefold() in WORDS else '[number]' if match[0].isdigit() else '[redacted]', value)
    value = re.sub(r'(?:\[redacted\][\s_-]*){2,}', '[redacted] ', value)
    return value[:MAX_MESSAGE].strip()


def diagnostic(response, payload, secrets=()):
    result = {'http_status': response.status}
    contents = tuple(_contents(payload))
    for header in ('x-request-id', 'x-goog-request-id'):
        request_id = getattr(response, 'getheader', lambda key: None)(header)
        if isinstance(request_id, str) and re.fullmatch(r'[A-Za-z0-9_.-]{1,128}', request_id):
            result['request_id_sha256'] = hashlib.sha256(request_id.encode()).hexdigest()
            break
    try:
        raw = response.read(MAX_BODY + 1)
        if len(raw) > MAX_BODY:
            result['body_note'] = 'Error body exceeded diagnostic limit; omitted'
            return result
        body = json.loads(raw)
    except Exception:
        result['body_note'] = 'Error body unavailable or not JSON; omitted'
        return result
    error = body.get('error', body) if isinstance(body, dict) else None
    if not isinstance(error, dict):
        result['body_note'] = 'Unrecognized error structure; omitted'
        return result
    code = error.get('code')
    if (type(code) is int and 100 <= code <= 599
            or isinstance(code, str) and code in CODES and _safe(code, secrets, contents) == code):
        result['provider_code'] = code
    status = error.get('status')
    if isinstance(status, str) and status in STATUSES and _safe(status, secrets, contents) == status:
        result['provider_status'] = status
    message = _safe(error.get('message'), secrets, contents)
    if message:
        result['message'] = message
        result['message_note'] = 'Only recognized diagnostic vocabulary is retained; other text and numeric values are redacted'
    # Google BadRequest details are useful for parameter errors; arbitrary debug
    # details and metadata can contain credentials or supplied source material.
    violations = []
    details = error.get('details', [])
    for detail in details[:8] if isinstance(details, list) else []:
        if not isinstance(detail, dict):
            continue
        fields = detail.get('fieldViolations', [])
        for item in fields[:8] if isinstance(fields, list) else []:
            if not isinstance(item, dict):
                continue
            field = item.get('field')
            if (isinstance(field, str) and len(field) <= 128
                    and all(part in PARAMETERS for part in field.split('.'))
                    and _safe(field, secrets, contents) == field):
                violations.append({'field': field, 'description': _safe(item.get('description'), secrets, contents)})
    if violations:
        result['field_violations'] = violations[:8]
    return result
