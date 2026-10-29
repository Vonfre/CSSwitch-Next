"""Keep tool call/result pairs valid when a conversation changes API protocol."""
import hashlib
import re

VALID = re.compile(r'[A-Za-z0-9_-]{1,64}\Z')


def normalize(messages):
    if not isinstance(messages, list):
        raise ValueError('messages must be an array')
    identifiers = set()
    for message in messages:
        if not isinstance(message, dict):
            raise ValueError('messages must contain objects')
        content = message.get('content')
        if not isinstance(content, list):
            continue
        for block in content:
            if not isinstance(block, dict):
                continue
            field = 'id' if block.get('type') == 'tool_use' else 'tool_use_id' if block.get('type') == 'tool_result' else None
            if field:
                value = block.get(field)
                if not isinstance(value, str) or not value:
                    raise ValueError('Tool calls and results require a nonempty string ID')
                identifiers.add(value)
    reserved = {value for value in identifiers if VALID.fullmatch(value)}
    mapped = {value: value for value in reserved}
    for value in sorted(identifiers - reserved):
        salt = ''
        while True:
            candidate = 'call_cs_' + hashlib.sha256((salt + value).encode()).hexdigest()[:56]
            if candidate not in reserved:
                break
            salt += ':'
        mapped[value] = candidate
        reserved.add(candidate)
    result = []
    for message in messages:
        content = message.get('content')
        if not isinstance(content, list):
            result.append(message)
            continue
        blocks = []
        for block in content:
            field = 'id' if isinstance(block, dict) and block.get('type') == 'tool_use' else 'tool_use_id' if isinstance(block, dict) and block.get('type') == 'tool_result' else None
            blocks.append({**block, field: mapped[block[field]]} if field else block)
        result.append({**message, 'content': blocks})
    return result
