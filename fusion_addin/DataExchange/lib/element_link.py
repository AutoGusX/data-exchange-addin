"""Link the elements of a loaded exchange to the geometry Fusion made from it. No Fusion imports.

A loaded exchange arrives as one STEP file; Fusion names the components and bodies after what the file
calls them, and the exchange lists its elements by name. Nothing else ties the two together, so the
link is by name, after removing the decorations each side adds:

  exchange element   "Wall (Right):15"                          instance number after a colon
  STEP product       "Wall (Right)_852F4FD0050AA6441B68E6B9F8592230"   32-digit id after an underscore
  Fusion component   "Wall (Right) v2", "Wall (Right) (1)"      version, or a counter for a duplicate name
  Fusion occurrence  "Wall (Right):1"                           instance number after a colon
  STEP product       "Nut, Hex Regular_2"                       a counter for a repeated part name
  Fusion body        "00-Bracket_1"                             an index before, a counter after

A name that several elements share (one type placed many times) links to all of them together. Where
no name matches, nothing is linked: the add-in never guesses.
"""
import re

_HEX_ID = re.compile(r'_[0-9A-Fa-f]{32}$')
_VERSION = re.compile(r'\s+v\d+$')
_COUNTER = re.compile(r'\s*\(\d+\)$')
_INSTANCE = re.compile(r':\d+$')
_REPEAT = re.compile(r'_\d{1,3}$')
_BODY_INDEX = re.compile(r'^\d{2}-')


def name_key(text):
    """The comparable form of an element, component or occurrence name ('' when there is none)."""
    name = str(text or '').strip()
    for pattern in (_INSTANCE, _COUNTER, _VERSION, _HEX_ID, _REPEAT):
        name = pattern.sub('', name).strip()
    return name.lower()


def body_key(text):
    """The comparable form of a body name: as name_key, and without the index STEP export puts in front."""
    return name_key(_BODY_INDEX.sub('', str(text or '').strip()))


def matching(element_name, names, bodies=False):
    """Indexes of the names that belong to an element. Empty when the element has no usable name."""
    key = name_key(element_name)
    if not key:
        return []
    return [i for i, name in enumerate(names)
            if name_key(name) == key or (bodies and body_key(name) == key)]


def elements_named(candidates, elements):
    """Element names (in list order, no repeats) for the first candidate name that any element matches.

    candidates run from the most specific geometry name (a body) outwards (its component, then the
    components above it); each is (name, is_body).
    """
    keys = {}
    for element in elements or []:
        name = (element.get('name') if isinstance(element, dict) else element) or ''
        key = name_key(name)
        if key:
            keys.setdefault(key, [])
            if name not in keys[key]:
                keys[key].append(name)
    for name, is_body in candidates:
        for key in ([body_key(name)] if is_body else []) + [name_key(name)]:
            if key and key in keys:
                return keys[key]
    return []
