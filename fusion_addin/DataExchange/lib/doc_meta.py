"""Per-document metadata (spec 5.1). Pure functions: no Fusion imports, tested with plain Python.

Stored as Fusion attributes (group ATTR_GROUP):
  - 'reference' on each imported root component: which exchange revision it shows.
  - 'published' on the design's root component: which exchange this design publishes to.
Never holds tokens. Exchange ids stay opaque strings.
"""
import datetime
import json

ATTR_GROUP = 'DataExchangeAddin'
ATTR_REFERENCE = 'reference'
ATTR_PUBLISHED = 'published'
ATTR_ELEMENTS = 'elements'      # on the reference component: what the exchange contains (names, properties)
MAX_ELEMENTS_STORED = 300
ATTR_DATA_ONLY = 'dataOnly'     # on the root component: data-only exchanges read into this design
MAX_DATA_ONLY = 10              # data-only exchanges kept per design
SCHEMA_VERSION = 1
IMPORT_MODE = 'read-only-reference'
PLACEMENT_AS_PUBLISHED = 'as-published'
PLACEMENT_AT_ORIGIN = 'at-origin'
REFERENCE_PREFIX = 'DX Reference — '
STAGING_SUFFIX = ' (updating)'

_EXCHANGE_KEYS = ('exchangeId', 'collectionId', 'hubId')


class MetadataError(ValueError):
    pass


def _utc_now():
    return datetime.datetime.now(datetime.timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')


def _check_exchange(exchange):
    if not isinstance(exchange, dict):
        raise MetadataError('exchange must be an object')
    for key in _EXCHANGE_KEYS:
        if not isinstance(exchange.get(key), str) or not exchange[key]:
            raise MetadataError('exchange.%s is missing' % key)


DOCS_FILES_URL = 'https://acc.autodesk.com/docs/files/projects/'
# Web host of Autodesk Forma (ACC) per hub region
# (https://aps.autodesk.com/en/docs/acc/v1/overview/acc-regions). Unknown or missing: the US host.
REGION_HOSTS = {'US': 'acc.autodesk.com', 'EMEA': 'acc.autodesk.eu', 'EU': 'acc.autodesk.eu',
                'AUS': 'acc.aus.autodesk.com', 'APAC': 'acc.aus.autodesk.com', 'CAN': 'acc.can.autodesk.com',
                'DEU': 'acc.deu.autodesk.com', 'IND': 'acc.ind.autodesk.com', 'JPN': 'acc.jpn.autodesk.com',
                'GBR': 'acc.gbr.autodesk.com'}


def forma_host(region):
    return REGION_HOSTS.get(str(region or '').strip().upper(), REGION_HOSTS['US'])


def is_forma_link(url):
    """True for an address this add-in may open in the browser: https on a known Autodesk Forma host."""
    from urllib.parse import urlparse
    try:
        parts = urlparse(url or '')
    except ValueError:
        return False
    return parts.scheme == 'https' and (parts.hostname or '').lower() in set(REGION_HOSTS.values())


def docs_url(exchange, source='sdk'):
    """The Autodesk Forma (ACC) web page for an exchange, or None when it cannot be built.

    Same shape as the link Docs itself gives: the project, the folder, and the exchange item opened
    in the detail view, on the web host of the hub's region (exchange['region']; US when not known). Without a usable item id the link opens the folder. Mock data has no page.
    """
    from urllib.parse import urlencode
    if source == 'mock' or not isinstance(exchange, dict):
        return None
    project, folder = exchange.get('projectId') or '', exchange.get('folderUrn') or ''
    if project.startswith('b.'):
        project = project[2:]
    if not project or not folder.startswith('urn:'):
        return None
    query = [('folderUrn', folder)]
    item = (exchange.get('fileUrn') or '').split('?')[0]
    if item.startswith('urn:') and ':dm.lineage:' in item:
        query += [('entityId', item), ('viewModel', 'detail')]
    query.append(('moduleId', 'folders'))
    return 'https://%s/docs/files/projects/%s?%s' % (forma_host(exchange.get('region')), project, urlencode(query))


def parse_forma_link(text):
    """The ids in a pasted Autodesk Forma (ACC) file link, or None when it is not one.

    Returns {'projectId': 'b.<guid>', 'folderUrn': str or None, 'fileUrn': str or None}. fileUrn is
    set only when the link opens an item (entityId is a lineage URN); a folder link has none.
    """
    import re
    from urllib.parse import parse_qs, urlparse
    try:
        parts = urlparse((text or '').strip())
    except ValueError:
        return None
    host = (parts.hostname or '').lower()
    if parts.scheme != 'https' or not (host.endswith('.autodesk.com') or host.endswith('.autodesk.eu')):
        return None
    match = re.search(r'/projects/([0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12})(?:/|$)', parts.path)
    if not match:
        return None
    query = parse_qs(parts.query)
    folder = (query.get('folderUrn') or [None])[0]
    item = ((query.get('entityId') or [''])[0]).split('?')[0]
    return {'projectId': 'b.' + match.group(1).lower(),
            'folderUrn': folder if folder and folder.startswith('urn:') else None,
            'fileUrn': item if item.startswith('urn:') and ':dm.lineage:' in item else None}


def slim_exchange(exchange):
    """Keep only what the helper needs to address the exchange again, plus display names."""
    _check_exchange(exchange)
    keep = _EXCHANGE_KEYS + ('projectId', 'fileUrn', 'name', 'projectName', 'folderUrn', 'region')
    return {k: exchange[k] for k in keep if exchange.get(k) is not None}


def build_reference(exchange, revision_id, source, placement=PLACEMENT_AS_PUBLISHED,
                    element_count=0, body_count=0, now=None):
    if not revision_id:
        raise MetadataError('revision id is missing')
    if placement not in (PLACEMENT_AS_PUBLISHED, PLACEMENT_AT_ORIGIN):
        raise MetadataError('unknown placement %r' % placement)
    slim = slim_exchange(exchange)
    return {
        'schemaVersion': SCHEMA_VERSION,
        'exchangeId': slim['exchangeId'],
        'projectId': slim.get('projectId'),
        'importedRevisionId': revision_id,
        'importMode': IMPORT_MODE,
        'importedAtUtc': now or _utc_now(),
        'exchange': slim,
        'placement': placement,
        'source': source,
        'elementCount': element_count,
        'bodyCount': body_count,
    }


def build_published(exchange, revision_id, source, now=None):
    if not revision_id:
        raise MetadataError('revision id is missing')
    slim = slim_exchange(exchange)
    return {
        'schemaVersion': SCHEMA_VERSION,
        'exchangeId': slim['exchangeId'],
        'publishedRevisionId': revision_id,
        'publishedAtUtc': now or _utc_now(),
        'exchange': slim,
        'source': source,
    }


utc_now = _utc_now


# ---- alignment with the cloud ------------------------------------------------------------------
# A "check" is what the last comparison with the cloud found for one exchange:
# None (not checked this session), {'latest': revision id} or {'error': text}.

def consumed_alignment(imported_revision, check):
    """(state, text) for a loaded reference: is the design on the exchange's latest revision?"""
    if not check:
        return 'unknown', 'Not checked against the cloud yet'
    if check.get('error'):
        return 'error', check['error']
    if check.get('latest') == imported_revision:
        return 'current', 'Up to date with the latest revision'
    return 'behind', 'A newer revision is available'


def published_alignment(published_revision, check):
    """(state, text) for the exchange this design publishes to."""
    if not check:
        return 'unknown', 'Not checked against the cloud yet'
    if check.get('error'):
        return 'error', check['error']
    if check.get('latest') == published_revision:
        return 'current', 'The exchange is at the revision you published'
    return 'diverged', 'The exchange has a newer revision than the last publish from this design'


def pack_elements(elements):
    """The element list kept with a loaded reference: name, category and properties of each element.

    Capped so a very large exchange does not bloat the design; 'total' keeps the real count.
    """
    listed = []
    for element in (elements or [])[:MAX_ELEMENTS_STORED]:
        properties = {str(k): str(v) for k, v in (element.get('properties') or {}).items()
                      if k != 'sourceCategory' and v not in (None, '')}
        listed.append({'name': element.get('displayName') or element.get('sourceElementId') or '',
                       'category': element.get('category') or '', 'properties': properties})
    return {'total': len(elements or []), 'listed': listed}


_MESH_UNITS = {'millimeter': 'Millimeter', 'millimeters': 'Millimeter', 'mm': 'Millimeter',
               'centimeter': 'Centimeter', 'centimeters': 'Centimeter', 'cm': 'Centimeter',
               'meter': 'Meter', 'meters': 'Meter', 'm': 'Meter',
               'inch': 'Inch', 'inches': 'Inch', 'in': 'Inch',
               'foot': 'Foot', 'feet': 'Foot', 'ft': 'Foot'}


def mesh_unit(source_length_unit):
    """'Millimeter', 'Centimeter', 'Meter', 'Inch' or 'Foot' for the unit text the SDK reports, else None.

    A mesh file carries no units. Seen live: the SDK writes it in the unit the exchange's elements
    report, so that text decides the scale. An unknown unit means the mesh is not imported at all.
    """
    return _MESH_UNITS.get(str(source_length_unit or '').strip().lower())


def build_data_only(exchange, data, hub_id, project_id, file_urn, now=None):
    """The stored record of a data-only (AEC Data Model based) exchange: where it is and its element data.

    There is no geometry and no revision id to track; readAtUtc says how old the copy is.
    """
    elements = [{'displayName': e.get('name'), 'category': e.get('category'), 'properties': e.get('properties')}
                for e in (data.get('listed') or []) if isinstance(e, dict)]
    packed = pack_elements(elements)
    packed['total'] = max(int(data.get('total') or 0), len(packed['listed']))
    return {'exchange': slim_exchange(exchange), 'exchangeId': exchange['exchangeId'],
            'hubId': hub_id, 'projectId': project_id, 'fileUrn': file_urn,
            'readAtUtc': now or _utc_now(), 'source': data.get('source') or 'aecdm', 'elements': packed}


def merge_data_only(stored, record):
    """stored ({exchangeId: record}) with one record added or replaced; the oldest go past the cap."""
    merged = {k: v for k, v in (stored or {}).items() if isinstance(v, dict) and k != record['exchangeId']}
    merged[record['exchangeId']] = record
    while len(merged) > MAX_DATA_ONLY:
        merged.pop(min(merged, key=lambda k: merged[k].get('readAtUtc') or ''))
    return merged


def changes_note(changes):
    """One line saying what a newer revision changed, or '' when that is not known."""
    if not isinstance(changes, dict):
        return ''
    created, modified, deleted = (int(changes.get(k) or 0) for k in ('createdCount', 'modifiedCount', 'deletedCount'))
    behind = int(changes.get('revisionsBehind') or 0)
    if behind == 0:
        return ''
    span = '%d revision%s' % (behind, '' if behind == 1 else 's')
    if created + modified + deleted == 0:
        return 'Since the loaded revision (%s): no element changes reported.' % span
    text = 'Since the loaded revision (%s): %d added, %d modified, %d deleted.' % (span, created, modified, deleted)
    names = [c.get('name') or c.get('sourceElementId') for c in (changes.get('created') or [])[:3]]
    names = [n for n in names if n]
    if names:
        text += ' Added: %s%s.' % (', '.join(names), ', …' if created > len(names) else '')
    return text


def local_change_notes(published_from_version, current_version, has_unsaved_changes):
    """Hints that the design has moved on since it was last published. Versions are saved-file versions."""
    notes = []
    if published_from_version and current_version and current_version > published_from_version:
        notes.append('Saved %d time(s) since the last publish (v%d, now v%d). Publish to share the changes.'
                     % (current_version - published_from_version, published_from_version, current_version))
    if has_unsaved_changes:
        notes.append('This design has unsaved changes.')
    return notes


def dumps(metadata):
    return json.dumps(metadata, separators=(',', ':'), sort_keys=True)


def loads(text):
    """Parse stored metadata. Raises MetadataError on anything this version cannot trust."""
    try:
        metadata = json.loads(text)
    except (TypeError, ValueError):
        raise MetadataError('metadata is not valid JSON')
    if not isinstance(metadata, dict):
        raise MetadataError('metadata is not an object')
    if metadata.get('schemaVersion') != SCHEMA_VERSION:
        raise MetadataError('unsupported metadata schemaVersion %r' % metadata.get('schemaVersion'))
    _check_exchange(metadata.get('exchange'))
    if metadata['exchange']['exchangeId'] != metadata.get('exchangeId'):
        raise MetadataError('metadata exchange ids disagree')
    return metadata


def needs_refresh(metadata, latest_revision_id):
    return metadata.get('importedRevisionId') != latest_revision_id


def reference_name(exchange_name):
    return REFERENCE_PREFIX + (exchange_name or 'Exchange')


def safe_file_stem(name, fallback='design'):
    stem = ''.join(c if c.isalnum() or c in ' -_.' else '_' for c in (name or '')).strip(' .')
    return stem[:80] or fallback
