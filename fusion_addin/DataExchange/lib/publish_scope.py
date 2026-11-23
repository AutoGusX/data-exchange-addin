"""What a publish includes, and what must be hidden while the STEP file is written. No Fusion imports.

A design is described by plain data so this can be tested without Fusion:

- an occurrence is its full path name, e.g. 'Frame:1+Bracket:2' (parents joined with '+');
- a body is (owner path, body name), with owner path '' for a body directly in the root component.

The stored scope (in the design's `published` record) is
    {'mode': 'all'}  or  {'mode': 'selection', 'items': [{'token', 'kind', 'name'}, ...]}
"""
import re

MODE_ALL = 'all'
MODE_SELECTION = 'selection'
SEPARATOR = '+'


def _inside(path, container):
    """True when path is container itself or anything below it."""
    return path == container or path.startswith(container + SEPARATOR)


def plan(occurrences, bodies, excluded, selected_occurrences=None, selected_bodies=None):
    """Decide what to hide for one export.

    occurrences: every occurrence path in the design.
    bodies: every (owner path, name).
    excluded: occurrence paths never published (the DX references).
    selected_occurrences / selected_bodies: None for the whole design; otherwise the selection.

    Returns {'hide_occurrences': [...], 'hide_bodies': [...], 'kept_bodies': [...]}, each in input order.
    """
    whole = selected_occurrences is None and selected_bodies is None
    picked_occurrences = list(selected_occurrences or [])
    picked_bodies = set(selected_bodies or [])

    def is_excluded(path):
        return any(_inside(path, e) for e in excluded)

    def in_selected(path):
        return any(_inside(path, s) for s in picked_occurrences)

    def leads_to_selection(path):
        below = path + SEPARATOR
        return (any(s.startswith(below) for s in picked_occurrences)
                or any(owner == path or owner.startswith(below) for owner, _ in picked_bodies))

    hidden_occurrences = []
    for path in occurrences:
        if is_excluded(path) or not (whole or in_selected(path) or leads_to_selection(path)):
            hidden_occurrences.append(path)
    hidden = set(hidden_occurrences)

    kept, hide_bodies = [], []
    for owner, name in bodies:
        owner_gone = owner != '' and (owner in hidden or is_excluded(owner))
        keep = not owner_gone and (whole or (owner, name) in picked_bodies or (owner != '' and in_selected(owner)))
        if keep:
            kept.append((owner, name))
        elif not owner_gone:
            hide_bodies.append((owner, name))  # its component stays visible, so the body itself is hidden
    return {'hide_occurrences': hidden_occurrences, 'hide_bodies': hide_bodies, 'kept_bodies': kept}


MAX_UNITS = 30   # more than this and the publish goes out as one element (one STEP export per unit)


def plan_units(occurrences, bodies, excluded, selected_occurrences=None, selected_bodies=None):
    """Split one publish into units; each unit becomes one exchange element with its own properties.

    Whole design: one unit per top-level component, plus one for bodies directly in the root.
    Selection: one unit per picked component, plus one per component that owns picked bodies.
    Returns [{'kind': 'occurrence' | 'bodies', 'path': ..., 'occurrences': [...], 'bodies': [...]}].
    """
    def is_excluded(path):
        return any(_inside(path, e) for e in excluded)

    units = []
    if selected_occurrences is None and selected_bodies is None:
        root_bodies = [(owner, name) for owner, name in bodies if owner == '']
        if root_bodies:
            units.append({'kind': 'bodies', 'path': '', 'occurrences': [], 'bodies': root_bodies})
        for path in occurrences:
            if SEPARATOR not in path and not is_excluded(path):
                units.append({'kind': 'occurrence', 'path': path, 'occurrences': [path], 'bodies': []})
        return units

    picked = [p for p in dict.fromkeys(selected_occurrences or []) if not is_excluded(p)]
    # A component picked together with one of its parents is already part of the parent's unit.
    picked = [p for p in picked if not any(p != other and _inside(p, other) for other in picked)]
    by_owner = {}
    for owner, name in dict.fromkeys(selected_bodies or []):
        if (owner and is_excluded(owner)) or any(owner and _inside(owner, p) for p in picked):
            continue
        by_owner.setdefault(owner, []).append((owner, name))
    for owner, owned in by_owner.items():
        units.append({'kind': 'bodies', 'path': owner, 'occurrences': [], 'bodies': owned})
    for path in picked:
        units.append({'kind': 'occurrence', 'path': path, 'occurrences': [path], 'bodies': []})
    return units


def unit_id(component_id, path, bodies_only=False):
    """A source id that stays the same from one publish to the next for the same component instance.

    Built from the component's permanent id and the instance numbers along its path, so renaming a
    component keeps the id. Bodies directly in the root keep the id used before units existed.
    """
    instances = '.'.join(part.rsplit(':', 1)[-1] for part in path.split(SEPARATOR)) if path else ''
    return 'fusion:' + component_id + (':' + instances if instances else '') + (
        ':bodies' if bodies_only and path else '')


_SOLID = re.compile(r'=\s*(?:MANIFOLD_SOLID_BREP|BREP_WITH_VOIDS)\s*\(')


def count_step_solids(path):
    """How many solid definitions a STEP file holds (the guard against publishing hidden geometry)."""
    count = 0
    with open(path, 'r', encoding='utf-8', errors='replace') as f:
        for line in f:
            count += len(_SOLID.findall(line))
    return count


_SHAPE = re.compile(r'=\s*(?:MANIFOLD_SOLID_BREP|BREP_WITH_VOIDS|FACETED_BREP|SHELL_BASED_SURFACE_MODEL)\s*\(')


def count_step_shapes(path):
    """Solid and surface definitions in a STEP file. Zero means it holds nothing Fusion imports as bodies."""
    count = 0
    with open(path, 'r', encoding='utf-8', errors='replace') as f:
        for line in f:
            count += len(_SHAPE.findall(line))
    return count


def check_export(solids_in_file, solid_instances_kept):
    """None when the export is plausible, else why it must not be published.

    A STEP file defines each solid at most once per place it is used, so more definitions than
    solids that were meant to be published means something hidden was written too.
    """
    if solids_in_file > solid_instances_kept:
        return ('Fusion exported more geometry than was meant to be published (%d solids in the file, '
                'at most %d expected). Nothing was published.' % (solids_in_file, solid_instances_kept))
    return None


def clean_scope(scope):
    """A storable scope from whatever was recorded or collected; anything unusable becomes 'all'."""
    if not isinstance(scope, dict) or scope.get('mode') != MODE_SELECTION:
        return {'mode': MODE_ALL}
    items = []
    for item in scope.get('items') or []:
        if isinstance(item, dict) and isinstance(item.get('token'), str) and item['token']:
            items.append({'token': item['token'], 'kind': str(item.get('kind') or ''),
                          'name': str(item.get('name') or '')})
    return {'mode': MODE_SELECTION, 'items': items} if items else {'mode': MODE_ALL}


def describe(scope, missing=0):
    scope = clean_scope(scope)
    if scope['mode'] == MODE_ALL:
        return 'Whole design'
    count = len(scope['items'])
    text = '%d selected item%s' % (count, '' if count == 1 else 's')
    return text + (' (%d no longer exist)' % missing if missing else '')
