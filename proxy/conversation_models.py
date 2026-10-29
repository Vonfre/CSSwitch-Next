"""Normalize retired web-picker IDs without adding selectable model entries."""
import re
from science_models import family

ALIAS = re.compile(r'claude-(opus|sonnet|haiku)-99-\d{2}-20\d{6}\Z')
MODEL_KEYS = ('operon-default-model', 'operon-subagent-model')


def is_binding(model):
    return isinstance(model, str) and bool(ALIAS.fullmatch(model))


def native_id(model, models):
    if not is_binding(model):
        return model
    kind = 'sonnet' if family(model) == 'haiku' else family(model)
    candidates = [m for m in models if family(m.get('id')) == kind and not is_binding(m.get('id'))]
    primary = [m for m in candidates if not m.get('overflow')]
    return (primary or candidates)[0]['id'] if candidates else model


def normalize_response(payload, path, models):
    if path == '/api/preferences/carried' and isinstance(payload, dict):
        values = payload.get('values')
        if isinstance(values, dict):
            return {**payload, 'values': {key: native_id(value, models) if key in MODEL_KEYS else value
                                        for key, value in values.items()}}
    if path == '/api/preferences/reviewer-model' and isinstance(payload, dict) and 'model' in payload:
        return {**payload, 'model': native_id(payload['model'], models)}
    if path == '/api/frames' or path.startswith('/api/frames/'):
        def frame(value):
            if isinstance(value, list):
                return [frame(item) for item in value]
            if not isinstance(value, dict):
                return value
            result = dict(value)
            if 'model' in result:
                result['model'] = native_id(result['model'], models)
            if isinstance(result.get('context_data'), dict) and '_model' in result['context_data']:
                result['context_data'] = {**result['context_data'], '_model': native_id(result['context_data']['_model'], models)}
            for key in ('frames', 'children', 'child_frames', 'frame', 'root'):
                if key in result:
                    result[key] = frame(result[key])
            return result
        return frame(payload)
    return payload
