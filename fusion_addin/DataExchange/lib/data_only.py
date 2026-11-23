"""Data-only exchanges (AEC Data Model based): recognise them and remember them. No Fusion imports.

An exchange built on the AEC Data Model holds element data only, with no geometry. The Data Exchange
service refuses to read one through the SDK ("This API does not support AECDM based exchange"), so
there is nothing to load into Fusion. The service only says so when a load is tried, so the add-in
remembers each one it meets and flags it in the explorer from then on.

Stored per Windows user in %LOCALAPPDATA%\\FusionDataExchange\\data-only.json (data-only.mock.json in
mock mode): {"exchanges": {"<file urn>": "<name>"}}. Only identifiers and names. No tokens.
"""
import json
import os

CODE = 'DATA_ONLY_EXCHANGE'
MESSAGE = ('This is an AEC Data Model exchange: it holds data only, with no geometry, '
           'so there is nothing to load into Fusion. Nothing in your design changed.')
SHORT = 'Data only, no geometry (AEC Data Model exchange). It cannot be loaded into Fusion.'
MAX_REMEMBERED = 500


def is_error(code, message):
    """True when a helper error means the exchange is AEC Data Model based.

    The helper reports CODE; a helper from before that code existed passes the service text through.
    """
    if code == CODE:
        return True
    text = (message or '').lower()
    return 'aecdm' in text or 'aec data model' in text


def file_path(data_dir, mock=False):
    return os.path.join(data_dir, 'data-only.mock.json' if mock else 'data-only.json')


def load(path):
    """{file urn: name} of the exchanges seen to be data only. A missing or damaged file is empty."""
    try:
        with open(path, 'r', encoding='utf-8') as f:
            raw = json.load(f)
    except (OSError, ValueError):
        return {}
    items = raw.get('exchanges') if isinstance(raw, dict) else None
    if not isinstance(items, dict):
        return {}
    return {str(urn): str(name or '') for urn, name in items.items() if urn}


def add(path, file_urn, name=''):
    """Remember one exchange as data only. Returns what is now stored; never raises on a write error."""
    known = load(path)
    if not file_urn:
        return known
    known.pop(file_urn, None)
    known[file_urn] = str(name or '')
    while len(known) > MAX_REMEMBERED:
        known.pop(next(iter(known)))
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, 'w', encoding='utf-8') as f:
            json.dump({'exchanges': known}, f, indent=2)
    except OSError:
        pass
    return known
