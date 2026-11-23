"""Favorite folder locations, shared by Load Exchange and Publish Exchange. No Fusion imports.

Stored per Windows user in %LOCALAPPDATA%\\FusionDataExchange\\favorites.json (favorites.mock.json in
mock mode, so fixture folders never mix with real ones). A favorite is a folder plus the hub and
project it sits in, with the names needed to show it without asking the cloud:

    {"hub": {"hubId", "name"}, "project": {"projectId", "name"},
     "folder": {"folderId", "name", "readOnly"}, "path": ["Hub", "Project", "Folder", ...]}

Only identifiers and names are stored. No tokens.
"""
import json
import os

MAX_FAVORITES = 50


def file_path(data_dir, mock=False):
    return os.path.join(data_dir, 'favorites.mock.json' if mock else 'favorites.json')


def _clean(location):
    """A storable favorite from what the explorer reports for a folder, or None when it is not one."""
    if not isinstance(location, dict):
        return None
    hub, project, folder = location.get('hub'), location.get('project'), location.get('folder')
    if not all(isinstance(part, dict) for part in (hub, project, folder)):
        return None
    ids = (hub.get('hubId'), project.get('projectId'), folder.get('folderId'))
    if not all(isinstance(value, str) and value for value in ids):
        return None
    path = [str(name) for name in (location.get('path') or []) if name]
    folder_name = str(folder.get('name') or (path[-1] if path else 'Folder'))
    return {
        'hub': {'hubId': ids[0], 'name': str(hub.get('name') or '')},
        'project': {'projectId': ids[1], 'name': str(project.get('name') or '')},
        'folder': {'folderId': ids[2], 'name': folder_name, 'readOnly': bool(folder.get('readOnly'))},
        'path': path or [folder_name],
    }


def _key(favorite):
    return favorite['project']['projectId'], favorite['folder']['folderId']


def load(path):
    """The saved favorites, oldest first. A missing or damaged file is an empty list."""
    try:
        with open(path, 'r', encoding='utf-8') as f:
            raw = json.load(f)
    except (OSError, ValueError):
        return []
    items = raw.get('favorites') if isinstance(raw, dict) else None
    cleaned, seen = [], set()
    for item in items if isinstance(items, list) else []:
        favorite = _clean(item)
        if favorite and _key(favorite) not in seen:
            seen.add(_key(favorite))
            cleaned.append(favorite)
    return cleaned


def load_last(path):
    """The folder last used to load or publish (same shape as a favorite), or None."""
    try:
        with open(path, 'r', encoding='utf-8') as f:
            raw = json.load(f)
    except (OSError, ValueError):
        return None
    return _clean(raw.get('last')) if isinstance(raw, dict) else None


def save_last(path, location):
    """Remember the folder of a load or publish. An exchange inside the folder is dropped from the path."""
    if isinstance(location, dict) and location.get('kind') == 'exchange':
        location = dict(location, path=list(location.get('path') or [])[:-1])
    last = _clean(location)
    if last is None:
        return None
    _save(path, load(path), last)
    return last


_KEEP = object()


def _save(path, favorites, last=_KEEP):
    if last is _KEEP:
        last = load_last(path)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    temp = path + '.tmp'
    with open(temp, 'w', encoding='utf-8') as f:
        json.dump({'version': 1, 'favorites': favorites, 'last': last}, f, indent=2)
    os.replace(temp, path)


def add(path, location):
    """Add a folder (no-op if already there, or if location is not a folder). Returns the new list."""
    favorites = load(path)
    favorite = _clean(location)
    if favorite is None:
        return favorites
    favorites = [f for f in favorites if _key(f) != _key(favorite)] + [favorite]
    favorites = favorites[-MAX_FAVORITES:]
    _save(path, favorites)
    return favorites


def remove(path, location):
    """Remove a folder by project and folder id. Returns the new list."""
    favorites = load(path)
    favorite = _clean(location)
    if favorite is None:
        return favorites
    kept = [f for f in favorites if _key(f) != _key(favorite)]
    if len(kept) != len(favorites):
        _save(path, kept)
    return kept
