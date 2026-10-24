"""Approved shared-window limits for the Vertex chat-completions adapters."""
from .core import BoardError, canonical

POLICY = 'shared-context-bytes-v1'
FRAMING_RESERVE = 4096


def applies(member):
    model = member.get('catalog_snapshot', {})
    return (model.get('execution_provider', 'vertex') == 'vertex'
            and model.get('api_family') in {'openai_maas', 'mistral'})


def wire_messages(messages):
    system = '\n\n'.join(m['content'] for m in messages if m['role'] == 'system')
    user = '\n\n'.join(m['content'] for m in messages if m['role'] != 'system')
    return [{'role': 'user', 'content': system + '\n\n' + user}]


def settings(member, messages):
    if not applies(member):
        return None
    if member.get('output_policy') != POLICY:
        raise BoardError('Vertex chat output capacity requires approval of the shared-context policy')
    context = member['catalog_snapshot']['context_tokens']
    # A conservative text-token allowance, not a tokenizer or evidence that the
    # input really overflows. Keep the full packet and disclose the calculation.
    bound = len(canonical(wire_messages(messages)).encode('utf-8')) + FRAMING_RESERVE
    effective = min(member['max_output_tokens'], context - bound)
    if effective < 128:
        raise BoardError('The conservative input estimate leaves no verified output headroom. '
                         'This is not a measured token overflow. A verified token count or a newly '
                         'approved smaller packet is needed; no content was truncated or sent.')
    return {'policy': POLICY, 'context_tokens': context,
            'requested_max_output_tokens': member['max_output_tokens'],
            'input_token_upper_estimate': bound, 'framing_reserve': FRAMING_RESERVE,
            'effective_max_output_tokens': effective,
            'estimate_basis': 'UTF-8 bytes of the complete wire messages plus framing; not actual tokens'}
