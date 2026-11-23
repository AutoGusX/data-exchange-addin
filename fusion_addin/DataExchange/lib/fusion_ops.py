"""Everything that touches the Fusion document. Main thread only.

Imported exchange content lives in one dedicated component per exchange, named
'DX Reference — <exchange name>', tagged with a 'reference' attribute. Refresh is a full, staged
replacement of that component and never touches anything else in the design.
"""
import os
import shutil
import tempfile
import threading

import adsk.core
import adsk.fusion

from . import doc_meta, publish_scope


class FusionOpError(Exception):
    pass


def app():
    return adsk.core.Application.get()


def active_design():
    design = adsk.fusion.Design.cast(app().activeProduct)
    if design is None:
        raise FusionOpError('Open a design (the Design workspace) first.')
    return design


def design_of(document):
    """The design in a specific document (a background job's target), which may not be the active one."""
    try:
        valid = document is not None and document.isValid
    except Exception:
        valid = False
    if not valid:
        raise FusionOpError('The design was closed before this job could finish.')
    design = adsk.fusion.Design.cast(document.products.itemByProductType('DesignProductType'))
    if design is None:
        raise FusionOpError('This document has no design.')
    return design


def saved_version(document):
    """The saved file version of a document, or None when it has never been saved."""
    try:
        return int(document.dataFile.versionNumber) if document.isSaved else None
    except Exception:
        return None


def has_unsaved_changes(document):
    try:
        return bool(document.isModified)
    except Exception:
        return False


SAVE_BY_HAND = 'Save it yourself to keep the link to the exchange.'


def save_document(document, description):
    """Save the design so the exchange link just written into it is kept. Returns (saved, note).

    Only a design that already has a place in the cloud can be saved without asking the user
    where; a never-saved design is left alone and the note says so.
    """
    try:
        if not document.isSaved:
            return False, 'This design has never been saved, so it was not saved automatically. ' + SAVE_BY_HAND
        if document.save(description):
            return True, 'Design saved as a new version, so the link to the exchange is kept.'
        return False, 'Fusion did not save the design. ' + SAVE_BY_HAND
    except Exception as e:
        return False, 'The design could not be saved automatically (%s). %s' % (e, SAVE_BY_HAND)


def up_axis():
    """'Y' or 'Z': the user's modeling orientation preference, or None when it cannot be read.

    Fusion has no per-design up axis in its API; the preference is what new designs and the view
    cube follow, so it is the best available answer.
    """
    try:
        orientation = app().preferences.generalPreferences.defaultModelingOrientation
        orientations = adsk.core.DefaultModelingOrientations
        if orientation == orientations.ZUpModelingOrientation:
            return 'Z'
        if orientation == orientations.YUpModelingOrientation:
            return 'Y'
    except Exception:
        pass
    return None


def is_saved(document):
    try:
        return bool(document.isSaved)
    except Exception:
        return False


# ---- part / assembly ---------------------------------------------------------------------------

PART_MESSAGE = ('This is a Part document, and Fusion lets a Part hold only one component. '
                'An exchange is loaded as its own reference component, so the document has to be a '
                'Hybrid or Assembly design.')


def is_part_design(design):
    """True for a Part-intent document (Fusion builds without design intent count as not a Part)."""
    try:
        return design.designIntent == adsk.fusion.DesignIntentTypes.PartDesignIntentType
    except AttributeError:
        return False


def convert_part_to_hybrid(design):
    """Part to Hybrid keeps the existing bodies and allows more components (a supported change)."""
    if not is_part_design(design):
        return
    try:
        design.designIntent = adsk.fusion.DesignIntentTypes.HybridDesignIntentType
    except RuntimeError as e:
        raise FusionOpError('%s\n\nFusion could not convert it automatically: %s' % (PART_MESSAGE, e))
    if is_part_design(design):
        raise FusionOpError(PART_MESSAGE + '\n\nFusion did not convert it. Change the design type by hand, '
                            'or load the exchange into an Assembly.')


# ---- waiting ---------------------------------------------------------------------------------

def run_blocking(work):
    """Run network work on a thread while Fusion keeps painting. Fusion API calls stay on this thread."""
    box = {}

    def target():
        try:
            box['value'] = work()
        except BaseException as e:  # re-raised on the main thread below
            box['error'] = e

    thread = threading.Thread(target=target, daemon=True)
    thread.start()
    while thread.is_alive():
        adsk.doEvents()
        thread.join(0.05)
    if 'error' in box:
        raise box['error']
    return box.get('value')


class Progress:
    """A progress dialog that is shown before the wait starts (step n of total, with a message)."""

    def __init__(self, title, total_steps):
        self._dialog = app().userInterface.createProgressDialog()
        self._dialog.isCancelButtonShown = False
        self._dialog.isBackgroundTranslucent = False
        self._title = title
        self._total = total_steps
        self._shown = False

    def step(self, number, message):
        text = '%s (step %d of %d)' % (message, number, self._total)
        if not self._shown:
            self._dialog.show(self._title, text, 0, self._total, 0)
            self._shown = True
        self._dialog.message = text
        self._dialog.progressValue = number - 1
        adsk.doEvents()

    def close(self):
        if self._shown:
            self._dialog.hide()
            self._shown = False


# ---- metadata --------------------------------------------------------------------------------

def find_references(design):
    """[(component, metadata)] for every imported exchange in the design. Skips unreadable entries."""
    found = []
    for attribute in design.findAttributes(doc_meta.ATTR_GROUP, doc_meta.ATTR_REFERENCE):
        component = adsk.fusion.Component.cast(attribute.parent)
        if component is None:
            continue
        try:
            found.append((component, doc_meta.loads(attribute.value)))
        except doc_meta.MetadataError:
            continue
    return found


def read_published(design):
    attribute = design.rootComponent.attributes.itemByName(doc_meta.ATTR_GROUP, doc_meta.ATTR_PUBLISHED)
    if attribute is None:
        return None
    try:
        return doc_meta.loads(attribute.value)
    except doc_meta.MetadataError:
        return None


def write_published(design, metadata):
    design.rootComponent.attributes.add(doc_meta.ATTR_GROUP, doc_meta.ATTR_PUBLISHED, doc_meta.dumps(metadata))


# ---- import ----------------------------------------------------------------------------------

def _count_bodies(component):
    count = component.bRepBodies.count + component.meshBodies.count
    for occurrence in component.allOccurrences:
        count += occurrence.component.bRepBodies.count + occurrence.component.meshBodies.count
    return count


def _occurrences_of(design, component):
    occurrences = design.rootComponent.allOccurrencesByComponent(component)
    return [occurrences.item(i) for i in range(occurrences.count)]


def _delete_component(design, component):
    for occurrence in _occurrences_of(design, component):
        if occurrence.isValid:
            occurrence.deleteMe()


def ground_reference(component):
    """Ground every part inside a reference to its parent, so the exchange cannot be dragged apart.

    The reference's own top-level occurrence is left free, so the whole exchange still moves as one.
    Returns (newly_grounded, total_parts, failed).
    """
    occurrences = component.allOccurrences
    changed = failed = 0
    for index in range(occurrences.count):
        occurrence = occurrences.item(index)
        try:
            if not occurrence.isGroundToParent:
                occurrence.isGroundToParent = True
                changed += 1
        except Exception:
            failed += 1
    return changed, occurrences.count, failed


def grounding_note(changed, total, failed):
    if total == 0:
        return ''
    if failed:
        return 'Parts grounded in the reference: %d of %d (%d could not be grounded).' % (total - failed, total, failed)
    return 'Parts grounded in the reference: %d.' % total


def _move_to_origin(design, occurrence):
    """Centre the reference on the origin in X/Y and sit it on Z = 0. Deterministic per revision."""
    box = occurrence.boundingBox
    if box is None:
        return
    low, high = box.minPoint, box.maxPoint
    move = adsk.core.Matrix3D.create()
    move.translation = adsk.core.Vector3D.create(-(low.x + high.x) / 2.0, -(low.y + high.y) / 2.0, -low.z)
    occurrence.transform2 = move
    if design.snapshots.hasPendingSnapshot:
        design.snapshots.add()


class NoSolidGeometry(FusionOpError):
    """The STEP form of the exchange gave Fusion no bodies; a mesh download may still work."""


def _import_mesh(design, component, path, source_length_unit):
    name = doc_meta.mesh_unit(source_length_unit)
    if name is None:
        raise FusionOpError('This exchange has only mesh geometry, and its length unit (%s) is not one the '
                            'add-in can scale reliably, so it was not imported.' % (source_length_unit or 'unknown'))
    units = getattr(adsk.fusion.MeshUnits, name + 'MeshUnit')
    base = None
    if design.designType == adsk.fusion.DesignTypes.ParametricDesignType:
        # In a parametric design a mesh has to live in a base feature.
        base = component.features.baseFeatures.add()
        base.startEdit()
    try:
        if base is not None:
            component.meshBodies.add(path, units, base)
        else:
            component.meshBodies.add(path, units)
    finally:
        if base is not None:
            base.finishEdit()


def import_reference(design, export, exchange, placement, replace_component=None):
    """Import one validated helper export as a reference component.

    The export holds either a STEP file (solids) or a mesh file. Staged: the new component is built
    first; the old one (replace_component) is deleted only after the new one holds geometry. On any
    failure the new component is removed and the old one is kept. Raises NoSolidGeometry when a STEP
    export gives no bodies, so the caller can retry with a mesh export.
    Returns (component, metadata).
    """
    manifest = export['manifest']
    geometry = manifest.get('exchangeGeometry') or {}
    kind = geometry.get('kind')
    if kind not in ('step', 'mesh') or not geometry.get('blobRef'):
        raise FusionOpError('This exchange has no geometry the add-in can import yet.')
    path = os.path.normpath(os.path.join(export['outputDirectory'], geometry['blobRef']))
    if not os.path.isfile(path):
        raise FusionOpError('The exported geometry file is missing.')

    final_name = doc_meta.reference_name(manifest.get('exchangeName') or exchange.get('name'))
    if is_part_design(design):
        raise FusionOpError(PART_MESSAGE)
    root = design.rootComponent
    occurrence = root.occurrences.addNewComponent(adsk.core.Matrix3D.create())
    component = occurrence.component
    try:
        component.name = final_name + (doc_meta.STAGING_SUFFIX if replace_component else '')
        if kind == 'step':
            empty = publish_scope.count_step_shapes(path) == 0
            manager = app().importManager
            options = manager.createSTEPImportOptions(path)
            options.isViewFit = False
            try:
                imported = manager.importToTarget(options, component)
            except RuntimeError:
                if empty:
                    raise NoSolidGeometry('The exchange holds no solid or surface geometry.')
                raise
            if not imported and not empty:
                raise FusionOpError('Fusion could not import the exchange geometry.')
        else:
            _import_mesh(design, component, path, manifest.get('sourceLengthUnit'))
        bodies = _count_bodies(component)
        if bodies == 0:
            if kind == 'step':
                raise NoSolidGeometry('The exchange imported with no geometry.')
            raise FusionOpError('The exchange imported with no geometry, as solids or as a mesh.')
        _, parts, not_grounded = ground_reference(component)
        if placement == doc_meta.PLACEMENT_AT_ORIGIN:
            _move_to_origin(design, occurrence)

        elements = manifest.get('elements') or []
        metadata = doc_meta.build_reference(
            exchange, manifest['revisionId'], manifest.get('source', 'sdk'), placement,
            element_count=len(elements), body_count=bodies)
        metadata['referenceGroupId'] = component.id
        metadata['partCount'] = parts
        metadata['partsNotGrounded'] = not_grounded
        metadata['geometryKind'] = kind
        component.attributes.add(doc_meta.ATTR_GROUP, doc_meta.ATTR_REFERENCE, doc_meta.dumps(metadata))
        # What the exchange contains, kept with the reference so it can be read without the cloud.
        component.attributes.add(doc_meta.ATTR_GROUP, doc_meta.ATTR_ELEMENTS,
                                 doc_meta.dumps(doc_meta.pack_elements(elements)))
    except Exception:
        if occurrence.isValid:
            occurrence.deleteMe()
        raise

    if replace_component is not None:
        _delete_component(design, replace_component)
        component.name = final_name
    return component, metadata


def read_elements(design, exchange_id):
    """The stored element list of a loaded exchange: {'total', 'listed'}, or None when there is none."""
    import json
    for component, metadata in find_references(design):
        if metadata.get('exchangeId') != exchange_id:
            continue
        attribute = component.attributes.itemByName(doc_meta.ATTR_GROUP, doc_meta.ATTR_ELEMENTS)
        if attribute is None:
            return None
        try:
            packed = json.loads(attribute.value)
        except ValueError:
            return None
        return packed if isinstance(packed, dict) else None
    return None


def skipped_elements(manifest):
    return sum(1 for e in (manifest.get('elements') or []) if not e.get('hasGeometry'))


# ---- export for publish ----------------------------------------------------------------------

def _all_occurrences(root):
    occurrences = root.allOccurrences
    return [occurrences.item(i) for i in range(occurrences.count)]


def _body_owner(body):
    context = body.assemblyContext
    return context.fullPathName if context else ''


def _reference_paths(design, occurrences):
    components = [component for component, _ in find_references(design)]
    return [o.fullPathName for o in occurrences if any(o.component == c for c in components)]


def selection_items(entities):
    """The storable form of a publish selection: one {'token', 'kind', 'name'} per picked component.

    Only components can be selected for publishing; anything else is ignored.
    """
    items = []
    for entity in entities:
        occurrence = adsk.fusion.Occurrence.cast(entity)
        if occurrence is not None:
            items.append({'token': occurrence.entityToken, 'kind': 'occurrence', 'name': occurrence.fullPathName})
    return items


def resolve_scope(design, scope):
    """(components, names of stored items that cannot be used) for a stored selection scope.

    A stored item is unusable when its component no longer exists, or when it is a single body from
    a publish made before selection was limited to components.
    """
    entities, missing = [], []
    for item in publish_scope.clean_scope(scope).get('items', []):
        try:
            found = design.findEntityByToken(item['token'])
        except Exception:
            found = []
        entity = None
        for candidate in found or []:
            entity = adsk.fusion.Occurrence.cast(candidate)
            if entity is not None and entity.isValid:
                break
            entity = None
        if entity is None:
            missing.append(item.get('name') or 'an item')
        else:
            entities.append(entity)
    return entities, missing


def inside_reference(design, entity):
    """True when a picked entity belongs to a loaded exchange (consumed data, never republished)."""
    occurrence = adsk.fusion.Occurrence.cast(entity)
    path = occurrence.fullPathName if occurrence is not None else _body_owner(entity)
    if not path:
        return False
    references = _reference_paths(design, _all_occurrences(design.rootComponent))
    return any(path == r or path.startswith(r + publish_scope.SEPARATOR) for r in references)


def _inventory(design, selection):
    """Everything the export needs to know about the design, as Fusion objects keyed by plain data."""
    root = design.rootComponent
    occurrences = _all_occurrences(root)
    by_path = {o.fullPathName: o for o in occurrences}
    bodies = {}
    for index in range(root.bRepBodies.count):
        body = root.bRepBodies.item(index)
        bodies[('', body.name)] = body
    for occurrence in occurrences:
        for index in range(occurrence.bRepBodies.count):
            body = occurrence.bRepBodies.item(index)
            bodies[(occurrence.fullPathName, body.name)] = body

    selected_occurrences = selected_bodies = None
    if selection is not None:
        selected_occurrences, selected_bodies = [], []
        for entity in selection:
            occurrence = adsk.fusion.Occurrence.cast(entity)
            if occurrence is not None:
                selected_occurrences.append(occurrence.fullPathName)
            else:
                body = adsk.fusion.BRepBody.cast(entity)
                if body is not None:
                    selected_bodies.append((_body_owner(body), body.name))
    return {'root': root, 'by_path': by_path, 'bodies': bodies,
            'references': _reference_paths(design, occurrences),
            'selected_occurrences': selected_occurrences, 'selected_bodies': selected_bodies}


def _export_hidden(design, inventory, steps, step_path):
    """Hide what the plan says, write the STEP file, show everything again, check the file.

    Returns (bodies kept, solids in the file). Raises when the file holds more than was meant.
    """
    by_path, bodies = inventory['by_path'], inventory['bodies']
    kept = [bodies[key] for key in steps['kept_bodies']]
    kept_solids = sum(1 for body in kept if body.isSolid)
    hidden = []
    try:
        for entity in [by_path[p] for p in steps['hide_occurrences']] + [bodies[k] for k in steps['hide_bodies']]:
            if entity.isLightBulbOn:
                entity.isLightBulbOn = False
                hidden.append(entity)
        manager = design.exportManager
        options = manager.createSTEPExportOptions(step_path, inventory['root'])
        if not manager.execute(options) or not os.path.isfile(step_path):
            raise FusionOpError('Fusion could not export the design to STEP.')
    finally:
        for entity in hidden:
            try:
                if entity.isValid:
                    entity.isLightBulbOn = True
            except Exception:
                pass
    solids = publish_scope.count_step_solids(step_path)
    problem = publish_scope.check_export(solids, kept_solids)
    if problem:
        raise FusionOpError(problem)
    return len(kept), solids


def _component_properties(design, component, occurrence, body_count):
    """What a consumer of the exchange should see about one published component. Empty values are left out."""
    properties = {'Fusion component': component.name, 'Fusion design': design.rootComponent.name,
                  'Body count': str(body_count)}
    for label, read_value in (('Part number', lambda: component.partNumber),
                              ('Description', lambda: component.description),
                              ('Material', lambda: component.material.name)):
        try:
            properties[label] = read_value() or ''
        except Exception:
            pass
    if occurrence is not None:
        try:
            physical = occurrence.getPhysicalProperties(
                adsk.fusion.CalculationAccuracy.LowCalculationAccuracy)
            properties['Mass (kg)'] = '%.3f' % physical.mass
        except Exception:
            pass
    return {name: str(value) for name, value in properties.items() if value}


def _no_geometry(selection):
    return FusionOpError('There is no geometry to publish: the selection holds no bodies of this design.'
                         if selection is not None else 'This design has no geometry of its own to publish.')


def export_design_step(design, selection=None):
    """Write everything being published to ONE temporary STEP file.

    selection: None for the whole design, else the occurrences and bodies to publish. Imported
    references are always left out. Everything not being published is hidden for the moment of the
    export and shown again afterwards; nothing is copied or changed. The file is then checked: if
    it holds more solids than were meant to go out, the export is refused.

    Returns (step_path, temp_dir, info). The caller removes temp_dir.
    """
    inventory = _inventory(design, selection)
    steps = publish_scope.plan(list(inventory['by_path']), list(inventory['bodies']), inventory['references'],
                               inventory['selected_occurrences'], inventory['selected_bodies'])
    if not steps['kept_bodies']:
        raise _no_geometry(selection)
    temp_dir = tempfile.mkdtemp(prefix='dxpublish-')
    try:
        step_path = os.path.join(temp_dir, doc_meta.safe_file_stem(inventory['root'].name) + '.step')
        kept, solids = _export_hidden(design, inventory, steps, step_path)
        return step_path, temp_dir, {'bodies': kept, 'solidsInFile': solids}
    except Exception:
        shutil.rmtree(temp_dir, ignore_errors=True)
        raise


def export_units(design, selection=None):
    """Write what is being published as one STEP file per unit (see publish_scope.plan_units).

    Each unit becomes one exchange element with its own name and properties. Every file is written
    from the root with only that unit visible, so each keeps its true position in the design.
    With more than publish_scope.MAX_UNITS units the design goes out as a single element instead.

    Returns (units, temp_dir, info); units are {'sourceId', 'name', 'stepPath', 'properties'}.
    """
    inventory = _inventory(design, selection)
    root, by_path, bodies = inventory['root'], inventory['by_path'], inventory['bodies']
    planned = publish_scope.plan_units(list(by_path), list(bodies), inventory['references'],
                                       inventory['selected_occurrences'], inventory['selected_bodies'])
    if not planned:
        raise _no_geometry(selection)

    if len(planned) > publish_scope.MAX_UNITS:
        step_path, temp_dir, info = export_design_step(design, selection)
        unit = {'sourceId': publish_scope.unit_id(root.id, '', True), 'name': root.name, 'stepPath': step_path,
                'properties': _component_properties(design, root, None, info['bodies'])}
        return [unit], temp_dir, dict(info, elements=1, combined=len(planned))

    temp_dir = tempfile.mkdtemp(prefix='dxpublish-')
    try:
        units, total = [], 0
        for index, plan_unit in enumerate(planned):
            steps = publish_scope.plan(list(by_path), list(bodies), inventory['references'],
                                       plan_unit['occurrences'], plan_unit['bodies'])
            if not steps['kept_bodies']:
                continue  # an empty component: nothing to publish for it
            occurrence = by_path.get(plan_unit['path'])
            component = occurrence.component if occurrence is not None else root
            bodies_only = plan_unit['kind'] == 'bodies'
            if occurrence is None:
                name = root.name
            else:
                name = occurrence.name + (' (bodies)' if bodies_only else '')
            step_path = os.path.join(temp_dir, '%02d-%s.step' % (index, doc_meta.safe_file_stem(name)))
            kept, _ = _export_hidden(design, inventory, steps, step_path)
            total += kept
            units.append({
                'sourceId': publish_scope.unit_id(component.id, plan_unit['path'], bodies_only),
                'name': name, 'stepPath': step_path,
                'properties': _component_properties(design, component, None if bodies_only else occurrence, kept),
            })
        if not units:
            raise _no_geometry(selection)
        return units, temp_dir, {'bodies': total, 'elements': len(units), 'combined': 0}
    except Exception:
        shutil.rmtree(temp_dir, ignore_errors=True)
        raise


def clear_published(design, file_urn):
    """Forget the publish link when the exchange it points to was deleted. True when a link was removed."""
    attribute = design.rootComponent.attributes.itemByName(doc_meta.ATTR_GROUP, doc_meta.ATTR_PUBLISHED)
    published = read_published(design)
    if attribute is None or not published or (published.get('exchange') or {}).get('fileUrn') != file_urn:
        return False
    attribute.deleteMe()
    return True
