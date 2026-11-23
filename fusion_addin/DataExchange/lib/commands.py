"""Toolbar commands: Account, Load Exchange, Refresh Exchange, Publish Exchange."""
import base64
import json
import os
import shutil
import threading
import time
import traceback
import webbrowser

import adsk.core
import adsk.fusion

from . import data_only, doc_meta, favorites, fusion_ops, jobs, publish_scope
from .helper_client import (OUTDATED_HELPER, HelperClient, HelperError, data_dir, find_helper_exe,
                            helper_is_current, load_config, set_addin_dir)

PANEL_ID = 'DX_Panel'
PANEL_NAME = 'Data Exchange'
TAB_ID = 'SolidTab'
WORKSPACE_ID = 'FusionSolidEnvironment'
CMD_ACCOUNT = 'DX_Account'
CMD_LOAD = 'DX_Load'
CMD_REFRESH = 'DX_Refresh'
CMD_PUBLISH = 'DX_Publish'
TITLE = 'Data Exchange'

_handlers = []       # keeps event handlers alive for the life of the add-in
DEFERRED_EVENT_ID = 'DX_DeferredWork'
_pending = []        # document work queued by a command, run after the command has closed
_client = None
_addin_dir = None

_FRIENDLY = {
    'AUTH_REQUIRED': 'You are not signed in, or your sign-in has expired. Use Account to sign in again.',
    'FORBIDDEN': 'Your Autodesk account does not have access to this project or exchange. '
                 'Ask a project admin for access.',
    'NOT_FOUND': 'That exchange (or its project or folder) could not be found. It may have been moved or deleted.',
    'NETWORK_ERROR': 'Could not reach Autodesk. Check your connection and try again. Nothing in your design changed.',
    'NOT_CONFIGURED': 'No APS client ID is configured. This copy of the add-in was packaged without one: '
                      'put a config.json with "clientId" in the add-in folder (see INSTALL.md).',
    'UNSUPPORTED_GEOMETRY': 'The exchange geometry could not be converted for Fusion.',
    'DATA_ONLY_EXCHANGE': data_only.MESSAGE,
}


def _ui():
    return adsk.core.Application.get().userInterface


def client():
    global _client
    if _client is None:
        config = load_config()
        _client = HelperClient(find_helper_exe(_addin_dir, config), mock=bool(config.get('mock')))
    return _client


def _describe(error):
    if isinstance(error, HelperError):
        if data_only.is_error(error.code, error.message):
            return data_only.MESSAGE
        text = _FRIENDLY.get(error.code, error.message)
        detail = error.message if error.code in ('UNSUPPORTED_GEOMETRY', 'NOT_FOUND', 'FORBIDDEN') else ''
        reference = '\n\nReference: %s' % error.correlation_id if error.correlation_id else ''
        return text + ('\n\n' + detail if detail and detail != text else '') + reference
    if isinstance(error, (fusion_ops.FusionOpError, doc_meta.MetadataError)):
        return str(error)
    return 'Unexpected error:\n%s' % ''.join(traceback.format_exception(type(error), error, error.__traceback__))


def _show_error(error, title=TITLE):
    _ui().messageBox(_describe(error), title, adsk.core.MessageBoxButtonTypes.OKButtonType,
                     adsk.core.MessageBoxIconTypes.WarningIconType)


def _mode_note(health):
    return '\n\nMOCK MODE: fixture data, not a real cloud exchange.' if health.get('mode') == 'mock' else ''


def _ensure_signed_in():
    """True when signed in, signing in first if the user agrees. Shows progress while waiting."""
    progress = fusion_ops.Progress(TITLE, 1)
    try:
        progress.step(1, 'Starting the Data Exchange helper')
        account = fusion_ops.run_blocking(client().account)
    finally:
        progress.close()
    if account:
        return True
    answer = _ui().messageBox('You are not signed in to Autodesk. Sign in now?', TITLE,
                              adsk.core.MessageBoxButtonTypes.YesNoButtonType)
    if answer != adsk.core.DialogResults.DialogYes:
        return False
    return _sign_in() is not None


def _sign_in():
    progress = fusion_ops.Progress(TITLE, 1)
    try:
        progress.step(1, 'Waiting for you to sign in in your browser')
        return fusion_ops.run_blocking(client().login)
    finally:
        progress.close()


# ---- hub / project / folder explorer shared by Load and Publish ----------------------------------

_network = threading.Lock()  # explorer requests run one at a time (SDK thread safety is unverified)


def _background(work, done, locked=True):
    """Run network work on a thread, then call done(value, error) on the main thread.

    Used while a command dialog is open, so the dialog never freezes: the result comes back through
    the same custom event that runs deferred document work. locked=False is for work that takes the
    network lock itself, one request at a time, so it does not hold everything else up.
    """
    application = adsk.core.Application.get()

    def target():
        try:
            if locked:
                with _network:
                    outcome = (work(), None)
            else:
                outcome = (work(), None)
        except BaseException as e:
            outcome = (None, e)
        _pending.append(lambda: done(*outcome))
        application.fireCustomEvent(DEFERRED_EVENT_ID, '')

    threading.Thread(target=target, daemon=True).start()


def _is_dark_theme():
    try:
        themes = adsk.core.UserInterfaceThemes
        theme = adsk.core.Application.get().preferences.generalPreferences.activeUserInterfaceTheme
        return theme in (themes.DarkBlueUserInterfaceTheme, themes.DarkGrayUserInterfaceTheme)
    except Exception:
        return False


def _favorites_path():
    return favorites.file_path(data_dir(), bool(load_config().get('mock')))


def _data_only_path():
    return data_only.file_path(data_dir(), bool(load_config().get('mock')))


def _by_name(items):
    return sorted(items, key=lambda item: (item.get('name') or '').lower())


class Explorer:
    """A tree of hubs, projects, folders and (for Load) exchanges, shown inside the command dialog.

    The tree is an HTML page (resources/explorer.html) in a BrowserCommandInput. Each level loads
    when it is opened. The page reports what is selected; this class keeps that selection and tells
    the command through on_change.
    """

    def __init__(self, command, inputs, prefix, show_exchanges, on_change, height=300):
        page = os.path.join(_addin_dir, 'resources', 'explorer.html').replace('\\', '/')
        self.input = inputs.addBrowserCommandInput(prefix + 'explorer', '', page, height, 0)
        self.show_exchanges = show_exchanges
        self.on_change = on_change
        self.selection = {}
        self.alive = True
        self._previews = {}
        self._search_id = 0
        _on(command.incomingFromHTML, _Html(self._incoming))
        _on(command.destroy, _Destroyed(self._closed))

    def _closed(self):
        self.alive = False

    # ---- what is selected ------------------------------------------------------------------

    def current_hub(self):
        return self.selection.get('hub')

    def current_project(self):
        return self.selection.get('project')

    def current_folder(self):
        return self.selection.get('folder')

    def current_exchange(self):
        return self.selection.get('exchange') if self.selection.get('kind') == 'exchange' else None

    def path_text(self):
        return ' / '.join(self.selection.get('path') or [])

    def remember(self):
        """Keep the selected folder (or the folder of the selected exchange) as the place to reopen at."""
        try:
            favorites.save_last(_favorites_path(), self.selection)
        except OSError:
            pass  # only a convenience

    # ---- messages from the page ------------------------------------------------------------

    def _incoming(self, event):
        source = event.browserCommandInput
        if source is None or source.id != self.input.id:
            return
        try:
            data = json.loads(event.data) if event.data else {}
        except ValueError:
            data = {}
        if not isinstance(data, dict):
            data = {}
        action = event.action
        if action == 'ready':
            event.returnData = json.dumps({'showExchanges': self.show_exchanges, 'dark': _is_dark_theme(),
                                           'favorites': favorites.load(_favorites_path()),
                                           'last': favorites.load_last(_favorites_path()),
                                           'dataOnly': sorted(data_only.load(_data_only_path())),
                                           'dataOnlyNote': data_only.SHORT})
            self._expand({'nodeId': 'root', 'kind': 'root'})
        elif action == 'expand':
            event.returnData = 'OK'
            self._expand(data)
        elif action == 'favorite':
            # Favorites are one list per user, shared by Load and Publish.
            path = _favorites_path()
            try:
                change = favorites.add if data.get('on') else favorites.remove
                event.returnData = json.dumps(change(path, data.get('location')))
            except OSError as e:
                event.returnData = json.dumps(favorites.load(path))
                _show_error(fusion_ops.FusionOpError('The favorite could not be saved: %s' % e))
        elif action == 'delete':
            event.returnData = 'OK'
            self._delete(data)
        elif action == 'search':
            event.returnData = 'OK'
            self._search(data)
        elif action == 'searchCancel':
            event.returnData = 'OK'
            self._search_id += 1
        elif action == 'select':
            event.returnData = 'OK'
            self.selection = data
            self.on_change()
            exchange = self.current_exchange()
            if exchange and self.show_exchanges:
                self._preview(exchange.get('fileUrn'))

    def _send(self, action, payload):
        if not self.alive:
            return
        try:
            self.input.sendInfoToHTML(action, json.dumps(payload))
        except Exception:
            pass  # the dialog closed while a request was still running

    def _expand(self, node):
        node_id, kind = node.get('nodeId'), node.get('kind')
        hub_id, project_id, folder_id = node.get('hubId'), node.get('projectId'), node.get('folderId')
        helper = client()
        show_exchanges = self.show_exchanges

        def work():
            if kind == 'root':
                return [{'kind': 'hub', 'id': h['hubId'], 'name': h['name'], 'data': h}
                        for h in _by_name(helper.hubs() or [])]
            if kind == 'hub':
                return [{'kind': 'project', 'id': p['projectId'], 'name': p['name'], 'data': p}
                        for p in _by_name(helper.projects(hub_id) or [])]
            parent = folder_id if kind == 'folder' else None
            items = [{'kind': 'folder', 'id': f['folderId'], 'name': f['name'], 'data': f}
                     for f in _by_name(helper.folders(hub_id, project_id, parent) or [])]
            if show_exchanges and kind == 'folder':
                items += [{'kind': 'exchange', 'id': e['fileUrn'], 'name': e['name'], 'data': e}
                          for e in _by_name(helper.exchanges(hub_id, project_id, folder_id) or [])]
            return items

        def done(items, error):
            if error is not None:
                message = _FRIENDLY.get(getattr(error, 'code', None)) or str(error)
                self._send('children', {'nodeId': node_id, 'error': message})
            else:
                self._send('children', {'nodeId': node_id, 'items': items})

        _background(work, done)

    SEARCH_MAX_FOLDERS = 150
    SEARCH_MAX_RESULTS = 100

    def _search(self, data):
        """Look through every folder under a location for exchanges (Load) or folders (Publish) by name.

        Breadth first, bounded, one request at a time so jobs and the tree keep working meanwhile.
        A newer search, Close, or closing the dialog stops it.
        """
        text = str(data.get('text') or '').strip()
        hub, project, folder = data.get('hub') or {}, data.get('project') or {}, data.get('folder') or None
        hub_id, project_id = hub.get('hubId'), project.get('projectId')
        if not (text and hub_id and project_id):
            return
        needle = text.lower()
        self._search_id += 1
        mine = self._search_id
        helper = client()
        show_exchanges = self.show_exchanges
        start_path = [str(name) for name in (data.get('path') or [])]

        def current():
            return self.alive and self._search_id == mine

        def context(path, in_folder):
            return {'hub': hub, 'project': project, 'folder': in_folder, 'path': path}

        def work():
            results, searched = [], 0
            waiting = [(folder, start_path)]
            while waiting and current() and searched < self.SEARCH_MAX_FOLDERS and len(results) < self.SEARCH_MAX_RESULTS:
                parent, path = waiting.pop(0)
                parent_id = parent.get('folderId') if parent else None
                searched += 1
                with _network:
                    subfolders = _by_name(helper.folders(hub_id, project_id, parent_id) or [])
                for sub in subfolders:
                    sub_path = path + [sub['name']]
                    if not show_exchanges and needle in sub['name'].lower():
                        results.append({'kind': 'folder', 'id': sub['folderId'], 'name': sub['name'], 'data': sub,
                                        'ctx': context(sub_path, sub)})
                    waiting.append((sub, sub_path))
                if show_exchanges and parent_id and current():
                    with _network:
                        exchanges = _by_name(helper.exchanges(hub_id, project_id, parent_id) or [])
                    for exchange in exchanges:
                        if needle in exchange['name'].lower():
                            results.append({'kind': 'exchange', 'id': exchange['fileUrn'], 'name': exchange['name'],
                                            'data': exchange, 'ctx': context(path + [exchange['name']], parent)})
            return {'items': results[:self.SEARCH_MAX_RESULTS], 'folders': searched, 'truncated': bool(waiting)}

        def done(found, error):
            if not current():
                return
            if error is not None:
                self._send('searchResults', {'text': text, 'error': _FRIENDLY.get(getattr(error, 'code', None)) or str(error)})
            else:
                self._send('searchResults', dict(found, text=text))

        _background(work, done, locked=False)

    def _delete(self, location):
        """Delete an exchange from its folder, after the user confirms. It cannot be undone."""
        exchange, hub, project = location.get('exchange'), location.get('hub'), location.get('project')
        if not (isinstance(exchange, dict) and isinstance(hub, dict) and isinstance(project, dict)):
            return
        file_urn, name = exchange.get('fileUrn'), exchange.get('name') or 'this exchange'
        answer = _ui().messageBox(
            'Delete the exchange "%s" from Autodesk Forma?\n\nIt is removed for everyone who uses it, and '
            'this cannot be undone. Designs that loaded it keep their geometry but can no longer refresh.'
            % name, 'Delete exchange', adsk.core.MessageBoxButtonTypes.YesNoButtonType,
            adsk.core.MessageBoxIconTypes.WarningIconType)
        if answer != adsk.core.DialogResults.DialogYes:
            return
        helper = client()

        def remove():
            if not helper_is_current(helper.health()):
                raise fusion_ops.FusionOpError(OUTDATED_HELPER)
            return helper.delete_exchange(hub.get('hubId'), project.get('projectId'), file_urn)

        def done(_value, error):
            if error is not None:
                self._send('deleted', {'fileUrn': file_urn, 'error': True})
                _show_error(error, 'Delete exchange')
                return
            self._send('deleted', {'fileUrn': file_urn})
            if self.current_exchange() and self.current_exchange().get('fileUrn') == file_urn:
                self.selection = {}
                self.on_change()
            # A design that published to it must not try to update something that is gone.
            _, design = _active_design_or_none()
            try:
                if design is not None and fusion_ops.clear_published(design, file_urn):
                    _ui().messageBox('This design published to that exchange. The link was removed, so the '
                                     'next publish creates a new exchange.', 'Delete exchange')
            except Exception:
                pass
            _push_summary()

        _background(remove, done)

    def _preview(self, file_urn):
        """Send the exchange thumbnail to the page. A missing or failed preview never blocks loading."""
        if file_urn in self._previews:
            self._send('preview', {'fileUrn': file_urn, 'dataUrl': self._previews[file_urn]})
            return
        hub, project = self.current_hub() or {}, self.current_project() or {}
        hub_id, project_id = hub.get('hubId'), project.get('projectId')
        helper = client()

        def work():
            info = helper.thumbnail(hub_id, project_id, file_urn) or {}
            path = info.get('path') if info.get('available') else None
            if not path or not os.path.isfile(path):
                return None
            with open(path, 'rb') as f:
                return 'data:image/png;base64,' + base64.b64encode(f.read()).decode('ascii')

        def done(data_url, error):
            self._previews[file_urn] = None if error is not None else data_url
            self._send('preview', {'fileUrn': file_urn, 'dataUrl': self._previews[file_urn]})

        _background(work, done)


# ---- command plumbing ---------------------------------------------------------------------------

class _Created(adsk.core.CommandCreatedEventHandler):
    def __init__(self, build):
        super().__init__()
        self._build = build

    def notify(self, args):
        try:
            self._build(adsk.core.CommandCreatedEventArgs.cast(args))
        except Exception as e:
            _show_error(e)


class _Execute(adsk.core.CommandEventHandler):
    def __init__(self, run):
        super().__init__()
        self._run = run

    def notify(self, args):
        event = adsk.core.CommandEventArgs.cast(args)
        try:
            self._run(event)
        except Exception as e:
            # Failing the command rolls back every change it made to the design.
            event.executeFailed = True
            event.executeFailedMessage = _describe(e)


class _Changed(adsk.core.InputChangedEventHandler):
    def __init__(self, changed):
        super().__init__()
        self._changed = changed

    def notify(self, args):
        try:
            self._changed(adsk.core.InputChangedEventArgs.cast(args))
        except Exception as e:
            _show_error(e)


class _Validate(adsk.core.ValidateInputsEventHandler):
    def __init__(self, valid):
        super().__init__()
        self._valid = valid

    def notify(self, args):
        event = adsk.core.ValidateInputsEventArgs.cast(args)
        try:
            event.areInputsValid = bool(self._valid())
        except Exception:
            event.areInputsValid = False


class _Html(adsk.core.HTMLEventHandler):
    def __init__(self, incoming):
        super().__init__()
        self._incoming = incoming

    def notify(self, args):
        try:
            self._incoming(adsk.core.HTMLEventArgs.cast(args))
        except Exception as e:
            _show_error(e)


class _Destroyed(adsk.core.CommandEventHandler):
    def __init__(self, closed):
        super().__init__()
        self._closed = closed

    def notify(self, args):
        try:
            self._closed()
        except Exception:
            pass


class _Deferred(adsk.core.CustomEventHandler):
    def notify(self, args):
        while _pending:
            work = _pending.pop(0)
            try:
                work()
            except Exception as e:
                _show_error(e)


def _defer(work):
    """Run document work right after the current command ends.

    Fusion's importToTarget cannot be used inside any command event (documented limitation; it fails
    with InternalValidationError). A command's execute handler therefore only reads its inputs and
    queues the real work here; the custom event fires on the main thread once the command is closed.
    """
    _pending.append(work)
    adsk.core.Application.get().fireCustomEvent(DEFERRED_EVENT_ID, '')


def _on(event, handler):
    event.add(handler)
    _handlers.append(handler)


def _status_box(inputs, key):
    box = inputs.addTextBoxCommandInput(key, '', '', 2, True)
    box.isFullWidth = True
    return box


# ---- job queue: Load, Refresh and Publish run here while the user keeps working --------------------

JOBS_PALETTE_ID = 'DX_JobsPalette'
CMD_JOBS = 'DX_Jobs'
_queue = None
_application = None
_app_events = []      # (event, handler) pairs on the application and UI, removed on stop
_failures_shown = set()


def _post_to_main(work):
    """Thread-safe: run work on the main thread through the custom event."""
    _pending.append(work)
    _application.fireCustomEvent(DEFERRED_EVENT_ID, '')


def _job_gate(job):
    """None when the job may touch its document now, else the reason it has to wait."""
    document = job.context
    job.can_continue = False
    try:
        open_ = document.isValid
    except Exception:
        open_ = False
    if not open_:
        raise fusion_ops.FusionOpError('The design was closed before this job could finish.')
    if adsk.core.Application.get().activeDocument != document:
        return 'Waiting: switch back to "%s" to finish' % job.detail
    active = _ui().activeCommand
    if active and active != 'SelectCommand' and not job.force:
        job.can_continue = True
        return 'Waiting for the current Fusion command to finish'
    return None


def _jobs_palette(create=True):
    palettes = _ui().palettes
    palette = palettes.itemById(JOBS_PALETTE_ID)
    if palette is None and create:
        page = os.path.join(_addin_dir, 'resources', 'jobs.html').replace('\\', '/')
        palette = palettes.add(JOBS_PALETTE_ID, 'Data Exchange', page, False, True, True, 340, 520)
        try:
            palette.dockingState = adsk.core.PaletteDockingStates.PaletteDockStateRight
        except Exception:
            pass
        _on(palette.incomingFromHTML, _Html(_jobs_incoming))
    return palette


def _show_jobs():
    palette = _jobs_palette()
    if palette is not None and not palette.isVisible:
        palette.isVisible = True
    _push_jobs()


def _push_jobs():
    palette = _jobs_palette(create=False)
    if palette is None or _queue is None:
        return
    try:
        palette.sendInfoToHTML('jobs', json.dumps({'jobs': _queue.snapshot(), 'dark': _is_dark_theme()}))
    except Exception:
        pass  # the page is not ready yet; it asks for the list when it is


def _jobs_changed():
    _push_jobs()
    _push_summary()
    # A failure should not go unnoticed if the panel was closed. Shown once per failed job.
    failed = {job.id for job in _queue.jobs if job.state == jobs.FAILED}
    if failed - _failures_shown:
        _failures_shown.update(failed)
        _show_jobs()


def _event_data(event):
    try:
        data = json.loads(event.data or '{}')
    except ValueError:
        data = {}
    return data if isinstance(data, dict) else {}


def _jobs_incoming(event):
    event.returnData = 'OK'
    if event.action == 'ready':
        _push_jobs()
        _push_summary()
    elif event.action == 'check':
        _check_alignment()
    elif event.action == 'elements':
        # The element list stored with a loaded reference; read from the design, not the cloud.
        _, design = _active_design_or_none()
        key = _event_data(event).get('key')
        packed = fusion_ops.read_elements(design, key) if design else None
        if packed is None and design:
            packed = (fusion_ops.read_data_only(design).get(key) or {}).get('elements')
        event.returnData = json.dumps(packed or {'total': 0, 'listed': [], 'missing': True})
    elif event.action in ('selectElement', 'findSelected'):
        data, find = _event_data(event), event.action == 'findSelected'
        # Selection work is document work: not from inside the page event.
        _post_to_main(lambda: _element_link(data.get('key'), data.get('name'), find))
    elif event.action in ('rereadData', 'removeData'):
        key, again = _event_data(event).get('key'), event.action == 'rereadData'
        _post_to_main(lambda: _data_only_action(key, again))
    elif event.action == 'refresh':
        key = _event_data(event).get('key')
        _post_to_main(lambda: _queue_refresh(only=key))
    elif event.action == 'openExchange':
        link = _summary_links.get(_event_data(event).get('key'))
        if doc_meta.is_forma_link(link):
            webbrowser.open(link)
    elif event.action == 'clear':
        _queue.clear_finished()
    elif event.action == 'open':
        # The page sends only a job id; the address opened is the one this add-in built.
        try:
            job_id = int(json.loads(event.data or '{}').get('id'))
        except (ValueError, TypeError):
            return
        link = next((job.link for job in _queue.jobs if job.id == job_id), None)
        if doc_meta.is_forma_link(link):
            webbrowser.open(link)
    elif event.action == 'continue':
        try:
            job_id = int(json.loads(event.data or '{}').get('id'))
        except (ValueError, TypeError):
            return
        # Not from inside the page event: document work runs from the custom event only.
        _post_to_main(lambda: _queue.continue_now(job_id))


# ---- helper status: live progress and pushed update notices ------------------------------------------

_watching = set()     # exchange ids the helper has subscribed to
_watch_refs = {}      # exchange id -> exchange reference, for the periodic revision check
RECHECK_SECONDS = 300.0   # pushed notices were not seen live, so revisions are also rechecked this often
_poller = {'stop': None}
POLL_SECONDS = 2.0


def _element_link(key, name, find):
    """Select the geometry of an element, or name the elements of what is selected. Answers the panel."""
    _, design = _active_design_or_none()
    reply = {'key': key, 'names': [], 'message': ''}
    if design is None:
        reply['message'] = 'Open the design first.'
    elif find:
        found_key, names = fusion_ops.elements_of_selection(design)
        reply['key'] = found_key or key
        reply['names'] = names
        if names:
            reply['message'] = 'Selected geometry belongs to: %s.' % ', '.join(names[:5])
        elif found_key:
            reply['message'] = ('The selection is part of this exchange, but no element is named like it. '
                                'The link is by name only.')
        else:
            reply['message'] = 'Select a body or component of a loaded exchange in the model first.'
    else:
        entities, kind = fusion_ops.element_geometry(design, key, name)
        count = fusion_ops.select_entities(entities) if entities else 0
        if count:
            reply['names'] = [name]
            noun = kind if count == 1 else ('bodies' if kind == 'body' else 'components')
            reply['message'] = 'Selected %d %s named like "%s".' % (count, noun, name)
        else:
            reply['message'] = ('Nothing in the model is named like "%s", so it cannot be selected. '
                                'The link is by name only.' % name)
    palette = _jobs_palette(create=False)
    if palette is not None:
        try:
            palette.sendInfoToHTML('elementLink', json.dumps(reply))
        except Exception:
            pass


def _data_only_action(key, again):
    """Read a data-only exchange's element data again, or forget it. Runs on the main thread."""
    document, design = _active_design_or_none()
    record = fusion_ops.read_data_only(design).get(key) if design else None
    if record is None:
        return
    exchange = record['exchange']
    if not again:
        fusion_ops.remove_data_only(design, key)
        _push_summary()
        return
    helper = client()

    def reread(job):
        return (yield from _data_only_part(job, document, helper, exchange, record.get('hubId'),
                                           record.get('projectId'), record.get('fileUrn')))

    _submit(('data', key), 'Read data of "%s"' % (exchange.get('name') or 'exchange'), document, reread)


def _apply_status(status):
    """Main thread: put the SDK's progress on the running job, and note pushed revisions."""
    if _queue is None or not isinstance(status, dict):
        return
    progress = status.get('progress')
    if isinstance(progress, dict) and progress.get('stepId'):
        _queue.set_progress(jobs.progress_text(progress.get('stepId'), progress.get('percent')))
    changed = False
    for update in status.get('updates') or []:
        key, revision = update.get('exchangeId'), update.get('revisionId')
        if key and revision and (_alignment.get(key) or {}).get('latest') != revision:
            _alignment[key] = {'latest': revision}
            changed = True
    if changed:
        _push_summary()


def _start_poller():
    """Ask the helper for its status every couple of seconds, only while there is something to watch.

    The status route answers from memory, so this never adds cloud traffic. The helper is never
    started just to be polled.
    """
    stop = threading.Event()
    _poller['stop'] = stop

    def recheck(helper):
        """Ask the cloud for the latest revision of each watched exchange. Quiet on any failure."""
        found = []
        with _network:
            if helper.account() is None:
                return
            for key, exchange in list(_watch_refs.items()):
                try:
                    found.append({'exchangeId': key, 'revisionId': helper.latest_revision(exchange)['revisionId']})
                except HelperError:
                    pass
        if found:
            _post_to_main(lambda: _apply_status({'updates': found}))

    def loop():
        last = None
        checked = time.time()
        while not stop.wait(POLL_SECONDS):
            try:
                helper, queue = _client, _queue
                if helper is None or queue is None or not helper.is_running():
                    continue
                if _watch_refs and not queue.active_count() and time.time() - checked > RECHECK_SECONDS:
                    checked = time.time()
                    recheck(helper)
                if not (queue.active_count() or _watching):
                    continue
                status = helper.status()
                key = json.dumps(status, sort_keys=True)
                if key != last:
                    last = key
                    _post_to_main(lambda s=status: _apply_status(s))
            except Exception:
                continue  # a missed poll is harmless

    threading.Thread(target=loop, daemon=True).start()


def _stop_poller():
    if _poller['stop'] is not None:
        _poller['stop'].set()
        _poller['stop'] = None
    _watching.clear()
    _watch_refs.clear()


def _poke_later():
    if _queue is not None and any(job.state == jobs.WAITING for job in _queue.jobs):
        _post_to_main(_queue.poke)


class _DocumentActivated(adsk.core.DocumentEventHandler):
    def notify(self, args):
        _poke_later()
        try:
            _push_summary()
        except Exception:
            pass


class _CommandTerminated(adsk.core.ApplicationCommandEventHandler):
    def notify(self, args):
        _poke_later()


def _listen(event, handler):
    event.add(handler)
    _app_events.append((event, handler))


def _auto_save():
    """On unless config.json says "autoSave": false."""
    return load_config().get('autoSave', True) is not False


def _keep_link(document, description):
    """After a job wrote exchange identifiers into the design: save it, or say that a save is needed.

    Returns (saved, note for the job result).
    """
    if _auto_save():
        return fusion_ops.save_document(document, description)
    return False, 'Automatic save is turned off. ' + fusion_ops.SAVE_BY_HAND


def _document_name(document):
    try:
        return document.name
    except Exception:
        return 'this design'


def _submit(key, title, document, factory):
    """Queue a job unless the same one is already in progress for this document."""
    for job in _queue.jobs:
        if not job.is_finished and getattr(job, 'key', None) == key and job.context == document:
            _show_jobs()
            return None
    job = _queue.submit(title, _document_name(document), document, factory)
    job.key = key
    _show_jobs()
    return job


def _jobs_created(args):
    _on(args.command.execute, _Execute(lambda _event: _show_summary()))


# ---- summary: what this design consumes and publishes, and whether it matches the cloud ----------

_alignment = {}        # exchange id -> check result (see doc_meta), kept for this Fusion session
_summary_links = {}    # link key -> Docs address, rebuilt with every summary
_check = {'running': False, 'at': None}


def _active_design_or_none():
    try:
        document = adsk.core.Application.get().activeDocument
        if document is None:
            return None, None
        return document, adsk.fusion.Design.cast(document.products.itemByProductType('DesignProductType'))
    except Exception:
        return None, None


def _location(exchange):
    return exchange.get('projectName') or ''


def _summary():
    document, design = _active_design_or_none()
    state = {'document': None, 'consumed': [], 'dataOnly': [], 'published': None, 'checking': _check['running'],
             'checkedAt': _check['at'], 'dark': _is_dark_theme()}
    if design is None:
        return state
    state['document'] = _document_name(document)
    _summary_links.clear()

    for component, metadata in fusion_ops.find_references(design):
        exchange, key = metadata['exchange'], metadata['exchangeId']
        check = _alignment.get(key)
        status, text = doc_meta.consumed_alignment(metadata.get('importedRevisionId'), check)
        link = doc_meta.docs_url(exchange, metadata.get('source', 'sdk'))
        if link:
            _summary_links[key] = link
        state['consumed'].append({
            'key': key, 'linkKey': key, 'hasLink': bool(link),
            'name': exchange.get('name') or component.name, 'location': _location(exchange),
            'component': component.name, 'revision': metadata.get('importedRevisionId'),
            'latest': (check or {}).get('latest'), 'at': metadata.get('importedAtUtc'),
            'placement': metadata.get('placement'), 'elements': metadata.get('elementCount', 0),
            'bodies': metadata.get('bodyCount', 0), 'mock': metadata.get('source') == 'mock',
            'mesh': metadata.get('geometryKind') == 'mesh',
            'state': status, 'statusText': text, 'canRefresh': status == 'behind',
            'checking': _check['running'],
            'notes': [n for n in [doc_meta.changes_note((check or {}).get('changes'))] if n and status == 'behind'],
        })

    for key, record in sorted(fusion_ops.read_data_only(design).items(), key=lambda kv: kv[1].get('readAtUtc') or ''):
        exchange = record['exchange']
        link = doc_meta.docs_url(exchange, record.get('source', 'aecdm'))
        if link:
            _summary_links['data:' + key] = link
        state['dataOnly'].append({
            'key': key, 'linkKey': 'data:' + key, 'hasLink': bool(link),
            'name': exchange.get('name') or 'Exchange', 'location': _location(exchange),
            'revision': None, 'at': record.get('readAtUtc'), 'mock': record.get('source') == 'mock',
            'elements': (record.get('elements') or {}).get('total', 0),
            'state': 'dataonly', 'shortStatus': 'Data only', 'checking': False, 'canRefresh': False,
            'statusText': 'AEC Data Model exchange: data only, no geometry. The element data is a copy read on the date shown.',
            'notes': [],
        })

    published = fusion_ops.read_published(design)
    if published:
        exchange, key = published['exchange'], published['exchangeId']
        check = _alignment.get(key)
        status, text = doc_meta.published_alignment(published.get('publishedRevisionId'), check)
        link = doc_meta.docs_url(exchange, published.get('source', 'sdk'))
        if link:
            _summary_links['pub:' + key] = link
        state['published'] = {
            'key': key, 'linkKey': 'pub:' + key, 'hasLink': bool(link),
            'name': exchange.get('name') or 'Exchange', 'location': _location(exchange),
            'revision': published.get('publishedRevisionId'), 'latest': (check or {}).get('latest'),
            'at': published.get('publishedAtUtc'), 'mock': published.get('source') == 'mock',
            'content': publish_scope.describe(published.get('scope')),
            'state': status, 'statusText': text, 'canRefresh': False, 'checking': _check['running'],
            'notes': doc_meta.local_change_notes(published.get('publishedFromVersion'),
                                                 fusion_ops.saved_version(document),
                                                 fusion_ops.has_unsaved_changes(document)),
        }
    return state


def _push_summary():
    palette = _jobs_palette(create=False)
    if palette is None:
        return
    try:
        palette.sendInfoToHTML('summary', json.dumps(_summary()))
    except Exception:
        pass  # the page is not ready yet, or Fusion is between documents


def _check_alignment():
    """Ask the cloud for the latest revision of every exchange in the active design (in the background)."""
    _, design = _active_design_or_none()
    if design is None or _check['running']:
        _push_summary()
        return
    references = fusion_ops.find_references(design)
    exchanges = {m['exchangeId']: m['exchange'] for _, m in references}
    loaded = {m['exchangeId']: m.get('importedRevisionId') for _, m in references}
    published = fusion_ops.read_published(design)
    if published:
        exchanges[published['exchangeId']] = published['exchange']
    if not exchanges:
        _push_summary()
        return
    try:
        helper = client()
    except HelperError as e:
        _show_error(e)
        return
    _check['running'] = True
    _push_summary()

    def work():
        if helper.account() is None:
            return None
        found = {}
        for key, exchange in exchanges.items():
            try:
                found[key] = {'latest': helper.latest_revision(exchange)['revisionId']}
                if loaded.get(key) and loaded[key] != found[key]['latest']:
                    try:  # what the newer revision changed; the check still stands without it
                        found[key]['changes'] = helper.changes(exchange, loaded[key])
                    except HelperError:
                        pass
            except HelperError as e:
                found[key] = {'error': 'Could not check: ' + _FRIENDLY.get(e.code, e.message)}
        try:  # from now on the service tells us about new revisions of these exchanges
            helper.watch(list(exchanges))
            _watching.update(exchanges)
            _watch_refs.update(exchanges)
        except HelperError:
            pass
        return found

    def done(found, error):
        _check['running'] = False
        if error is not None:
            found = {key: {'error': 'Could not check: %s' % error} for key in exchanges}
        elif found is None:
            found = {key: {'error': 'Sign in (Account) to check for newer revisions'} for key in exchanges}
        else:
            _check['at'] = doc_meta.utc_now()
        _alignment.update(found)
        _push_summary()

    _background(work, done)


def _show_summary():
    _show_jobs()
    _check_alignment()


# ---- Account ------------------------------------------------------------------------------------

def _account_created(args):
    def run(_event):
        progress = fusion_ops.Progress(TITLE, 1)
        try:
            progress.step(1, 'Starting the Data Exchange helper')
            health = fusion_ops.run_blocking(client().health)
            account = fusion_ops.run_blocking(client().account)
        finally:
            progress.close()

        if account:
            answer = _ui().messageBox(
                'Signed in as %s (%s).%s\n\nSign out?' % (account.get('displayName'), account.get('email'), _mode_note(health)),
                TITLE, adsk.core.MessageBoxButtonTypes.YesNoButtonType)
            if answer == adsk.core.DialogResults.DialogYes:
                fusion_ops.run_blocking(client().logout)
                _ui().messageBox('Signed out.', TITLE)
            return

        account = _sign_in()
        _ui().messageBox('Signed in as %s (%s).%s' % (account.get('displayName'), account.get('email'), _mode_note(health)), TITLE)

    _on(args.command.execute, _Execute(run))


# ---- Load ---------------------------------------------------------------------------------------

def _load_created(args):
    command = args.command
    design = fusion_ops.active_design()
    document = adsk.core.Application.get().activeDocument
    # Ask before any browsing or download, so nobody waits and then hits Fusion's one-component limit.
    convert = False
    if fusion_ops.is_part_design(design):
        answer = _ui().messageBox(
            fusion_ops.PART_MESSAGE + '\n\nConvert this document to a Hybrid design and continue? '
            'Your existing geometry is kept.', 'Load Exchange', adsk.core.MessageBoxButtonTypes.YesNoButtonType)
        if answer != adsk.core.DialogResults.DialogYes:
            command.doExecute(True)
            return
        convert = True
    if not _ensure_signed_in():
        command.doExecute(True)  # nothing to show: end the command quietly
        return

    inputs = command.commandInputs
    command.okButtonText = 'Load'
    command.setDialogInitialSize(440, 640)
    command.setDialogMinimumSize(340, 480)
    status = _status_box(inputs, 'dxLoadStatus')
    status.text = 'Open a hub, project and folder, then pick an exchange. Or paste a link from Autodesk Forma.'
    link = inputs.addStringValueInput('dxLoad_link', 'Forma link', '')
    link.tooltip = ('Optional. Paste the address of an exchange copied from Autodesk Forma to load it '
                    'without browsing. Leave empty to pick from the list.')

    def linked():
        """(ids from the pasted link, problem). Both None when the box is empty."""
        text = link.value.strip()
        if not text:
            return None, None
        found = doc_meta.parse_forma_link(text)
        if found is None:
            return None, 'That is not an Autodesk Forma file link.'
        if not found['fileUrn']:
            return None, ('That link opens a folder, not an exchange. Open the exchange in Autodesk Forma '
                          'and copy its link.')
        return found, None

    def selection_changed():
        found, problem = linked()
        if problem:
            status.text = problem
        elif found:
            status.text = 'Load will use the pasted link. Clear the link to pick from the list instead.'
        elif explorer.current_exchange():
            status.text = 'Selected: ' + explorer.path_text()
        else:
            status.text = 'Pick an exchange (the items with the blue arrows) inside a folder.'

    explorer = Explorer(command, inputs, 'dxLoad_', True, selection_changed, height=380)
    placement = inputs.addDropDownCommandInput('dxLoad_placement', 'Placement', adsk.core.DropDownStyles.TextListDropDownStyle)
    placement.listItems.add('As published', True)
    placement.listItems.add('Move to origin', False)
    placement.tooltip = ('As published keeps the source coordinates. Move to origin centres the reference '
                         'on the Fusion origin, useful when the source model sits far from it.')

    def picked_ids():
        found, problem = linked()
        if problem:
            return None
        if found:
            return None, found['projectId'], found['fileUrn']   # the job finds the hub
        picked, hub, project = explorer.current_exchange(), explorer.current_hub(), explorer.current_project()
        if not (picked and hub and project):
            return None
        return hub['hubId'], project['projectId'], picked['fileUrn']

    def run(_event):
        ids = picked_ids()
        if ids is None:
            raise fusion_ops.FusionOpError('No exchange was selected. Run Load Exchange again and pick one.')
        hub_id, project_id, file_urn = ids
        if hub_id is not None:
            explorer.remember()
        mode = doc_meta.PLACEMENT_AT_ORIGIN if placement.selectedItem.name == 'Move to origin' else doc_meta.PLACEMENT_AS_PUBLISHED
        _defer(lambda: _submit(
            ('load', file_urn), 'Load exchange', document,
            lambda job: _load_job(job, document, hub_id, project_id, file_urn, mode, convert)))

    _on(command.inputChanged, _Changed(lambda event: selection_changed() if event.input.id == 'dxLoad_link' else None))
    _on(command.validateInputs, _Validate(lambda: picked_ids() is not None))
    _on(command.execute, _Execute(run))


def _find_hub(helper, project_id):
    """The hub that holds a project, for a pasted link (which names the project but not the hub)."""
    for hub in helper.hubs() or []:
        try:
            projects = helper.projects(hub['hubId']) or []
        except HelperError:
            continue  # a hub this account cannot list is not the one
        if any((p.get('projectId') or '').lower() == project_id.lower() for p in projects):
            return hub['hubId']
    raise fusion_ops.FusionOpError(
        'The project in that link is not in any hub your account can see. Check that you are signed in '
        'with the right account.')


def _import_reference(exports, document, helper, exchange, placement, replace=None, convert_part=False):
    """Part of a job (use with `yield from`): import the downloaded export, falling back to a mesh.

    exports[-1] is the STEP export. When it gives Fusion no bodies, the exchange is downloaded again
    as a mesh and that is imported instead; the extra export is appended to exports for cleanup.
    Returns (component, metadata).
    """
    design = fusion_ops.design_of(document)
    if convert_part:
        fusion_ops.convert_part_to_hybrid(design)
    try:
        return fusion_ops.import_reference(design, exports[-1], exchange, placement, replace_component=replace)
    except fusion_ops.NoSolidGeometry:
        pass
    exports.append((yield jobs.Background('No solid geometry: downloading it as a mesh',
                                          lambda: helper.export_mesh(exchange))))
    yield jobs.WhenIdle('Importing the mesh')
    design = fusion_ops.design_of(document)
    if replace is not None and not replace.isValid:
        raise fusion_ops.FusionOpError('The reference was removed from the design.')
    return fusion_ops.import_reference(design, exports[-1], exchange, placement, replace_component=replace)


def _data_only_part(job, document, helper, exchange, hub_id, project_id, file_urn):
    """Part of a load job: a data-only exchange has no geometry, so read and keep its element data."""
    if not helper_is_current((yield jobs.Background('Checking the helper', helper.health))):
        raise fusion_ops.FusionOpError(data_only.MESSAGE + '\n\n' + OUTDATED_HELPER)
    try:
        data = yield jobs.Background('Data only, no geometry: reading its element data',
                                     lambda: helper.data_only_elements(hub_id, project_id, file_urn))
    except HelperError as e:
        raise fusion_ops.FusionOpError('%s\n\nIts element data could not be read either: %s' % (
            data_only.MESSAGE, _FRIENDLY.get(e.code) if e.code in ('AUTH_REQUIRED', 'NETWORK_ERROR') else e.message))
    record = doc_meta.build_data_only(exchange, data, hub_id, project_id, file_urn)
    yield jobs.WhenIdle('Keeping the element data with the design')
    fusion_ops.write_data_only(fusion_ops.design_of(document), record)
    job.link = doc_meta.docs_url(exchange, record['source'])
    listed, total = len(record['elements']['listed']), record['elements']['total']
    return ('%sData only, no geometry: this is an AEC Data Model exchange, so nothing was added to the model.\n'
            'Its element data was read instead: %d element%s%s. Open Elements on its row in the panel.\n%s' % (
                'MOCK MODE: fixture data. ' if record['source'] == 'mock' else '',
                total, '' if total == 1 else 's',
                ' (the first %d are kept)' % listed if total > listed else '',
                _keep_link(document, 'Data Exchange: read data of "%s"' % (exchange.get('name') or 'exchange'))[1]))


def _mesh_note(metadata):
    if metadata.get('geometryKind') == 'mesh':
        return 'Imported as a mesh: this exchange has no solid geometry.'
    return ''


def _load_job(job, document, hub_id, project_id, file_urn, placement, convert_part):
    started = time.time()
    exports = []
    helper = client()
    try:
        if hub_id is None:
            hub_id = yield jobs.Background('Finding the project from the link', lambda: _find_hub(helper, project_id))
        exchange = yield jobs.Background('Finding the exchange', lambda: helper.resolve(hub_id, project_id, file_urn))
        if exchange.get('name'):
            job.title = 'Load "%s"' % exchange['name']

        design = fusion_ops.design_of(document)
        existing = [c for c, m in fusion_ops.find_references(design) if m['exchangeId'] == exchange['exchangeId']]
        if existing:
            raise fusion_ops.FusionOpError(
                'This design already contains "%s". Use Refresh Exchange to update it.' % existing[0].name)

        try:
            exports.append((yield jobs.Background('Downloading the latest revision',
                                                  lambda: helper.export(exchange))))
        except HelperError as e:
            if not data_only.is_error(e.code, e.message):
                raise
            # An AEC Data Model exchange: data only. Remember it so the explorer flags it from now on,
            # then read its element data instead of geometry.
            data_only.add(_data_only_path(), file_urn, exchange.get('name'))
            return (yield from _data_only_part(job, document, helper, exchange, hub_id, project_id, file_urn))
        yield jobs.WhenIdle('Importing into Fusion')
        component, metadata = yield from _import_reference(
            exports, document, helper, exchange, placement, convert_part=convert_part)
    finally:
        for export in exports:
            helper.cleanup(export['outputDirectory'])

    manifest = exports[-1]['manifest']
    _alignment[exchange['exchangeId']] = {'latest': metadata['importedRevisionId']}
    job.link = doc_meta.docs_url(exchange, manifest.get('source', 'sdk'))
    lines = [
        'Loaded as read-only reference "%s".' % component.name,
        'Revision: %s' % metadata['importedRevisionId'],
        'Elements in exchange: %d' % metadata['elementCount'],
        'Elements skipped (no geometry): %d' % fusion_ops.skipped_elements(manifest),
        'Bodies imported: %d' % metadata['bodyCount'],
        _mesh_note(metadata),
        fusion_ops.grounding_note(0, metadata['partCount'], metadata['partsNotGrounded']),
        'Time: %.1f s' % (time.time() - started),
    ]
    lines = [line for line in lines if line]
    if manifest.get('source') == 'mock':
        lines.append('MOCK MODE: fixture data, not a real cloud exchange.')
    lines.append(_keep_link(document, 'Data Exchange: loaded "%s"' % (exchange.get('name') or 'exchange'))[1])
    return '\n'.join(lines)


# ---- Refresh ------------------------------------------------------------------------------------

def _refresh_created(args):
    _on(args.command.execute, _Execute(lambda _event: _defer(_queue_refresh)))


def _queue_refresh(only=None):
    document = adsk.core.Application.get().activeDocument
    design = fusion_ops.active_design()
    references = fusion_ops.find_references(design)
    if not references:
        _ui().messageBox('This design has no loaded exchange. Use Load Exchange first.', 'Refresh Exchange')
        return
    if not _ensure_signed_in():
        return
    for component, metadata in references:
        if only is not None and metadata['exchangeId'] != only:
            continue
        _submit(('refresh', metadata['exchangeId']), 'Refresh "%s"' % component.name, document,
                lambda job, c=component, m=metadata: _refresh_job(job, document, c, m))


def _refresh_job(job, document, component, metadata):
    exchange = metadata['exchange']
    exports = []
    helper = client()
    try:
        latest = yield jobs.Background('Checking for a newer revision', lambda: helper.latest_revision(exchange))
        _alignment[metadata['exchangeId']] = {'latest': latest['revisionId']}
        if not doc_meta.needs_refresh(metadata, latest['revisionId']):
            # References loaded before parts were grounded get grounded here, without a re-import.
            yield jobs.WhenIdle('Grounding the parts')
            if not component.isValid:
                return 'Already up to date.'
            changed, _, failed = fusion_ops.ground_reference(component)
            if changed or failed:
                note = _keep_link(document, 'Data Exchange: grounded "%s"' % component.name)[1] if changed else ''
                return 'Already up to date. Grounded %d parts in the reference%s.\n%s' % (
                    changed, ' (%d could not be grounded)' % failed if failed else '', note)
            return 'Already up to date.'

        def compare():
            try:
                return helper.changes(exchange, metadata.get('importedRevisionId'))
            except HelperError:
                return None  # the comparison is extra information; the refresh does not depend on it

        changed = doc_meta.changes_note((yield jobs.Background('Comparing revisions', compare)))

        # Everything is downloaded and validated by the helper before the design is touched.
        exports.append((yield jobs.Background('Downloading the new revision', lambda: helper.export(exchange))))
        yield jobs.WhenIdle('Replacing the reference')
        if not component.isValid:
            raise fusion_ops.FusionOpError('The reference was removed from the design.')
        _, updated = yield from _import_reference(
            exports, document, helper, exchange, metadata.get('placement', doc_meta.PLACEMENT_AS_PUBLISHED),
            replace=component)
        return 'Updated to revision %s (%d bodies). %s %s\n%s%s' % (
            updated['importedRevisionId'], updated['bodyCount'], _mesh_note(updated),
            fusion_ops.grounding_note(0, updated['partCount'], updated['partsNotGrounded']),
            changed + '\n' if changed else '',
            _keep_link(document, 'Data Exchange: refreshed "%s"' % (exchange.get('name') or 'exchange'))[1])
    except GeneratorExit:
        raise
    except Exception as e:
        # import_reference already removed its staged component; the old reference is untouched.
        raise fusion_ops.FusionOpError('Not updated, the previous revision is kept.\n%s' % _describe(e))
    finally:
        for export in exports:
            helper.cleanup(export['outputDirectory'])


# ---- Publish ------------------------------------------------------------------------------------

def _publish_created(args):
    command = args.command
    design = fusion_ops.active_design()
    document = adsk.core.Application.get().activeDocument
    if not _ensure_signed_in():
        command.doExecute(True)
        return

    published = fusion_ops.read_published(design)
    inputs = command.commandInputs
    command.okButtonText = 'Publish'
    status = _status_box(inputs, 'dxPubStatus')

    target = inputs.addDropDownCommandInput('dxPub_target', 'Publish to', adsk.core.DropDownStyles.TextListDropDownStyle)
    update_label = None
    if published:
        update_label = 'Update "%s"' % (published['exchange'].get('name') or 'existing exchange')
        target.listItems.add(update_label, True)
    target.listItems.add('New exchange', not published)

    name = inputs.addStringValueInput('dxPub_name', 'Exchange name', design.rootComponent.name)

    def selection_changed():
        folder = explorer.current_folder()
        if not folder:
            status.text = 'Pick the folder to publish into.'
        elif folder.get('readOnly'):
            status.text = 'You cannot publish to "%s" (read-only). Pick another folder.' % folder.get('name')
        else:
            status.text = 'Publish to: ' + explorer.path_text()

    # What to publish: the whole design, or picked components (remembered for next time).
    stored_scope = publish_scope.clean_scope((published or {}).get('scope'))
    stored_entities, stored_missing = fusion_ops.resolve_scope(design, stored_scope)
    pick_mode = stored_scope['mode'] == publish_scope.MODE_SELECTION
    scope_choice = inputs.addDropDownCommandInput('dxPub_scope', 'What to publish',
                                                  adsk.core.DropDownStyles.TextListDropDownStyle)
    scope_choice.listItems.add('Whole design', not pick_mode)
    scope_choice.listItems.add('Selected components', pick_mode)
    picker = inputs.addSelectionInput('dxPub_pick', 'Components', 'Pick the components to publish')
    # Components only. Picking single bodies failed in Fusion (2026-11-16) and was dropped on purpose:
    # a component is also what becomes one element of the exchange.
    picker.addSelectionFilter('Occurrences')
    picker.setSelectionLimits(0, 0)   # any number; the dialog checks for at least one itself
    picker.tooltip = ('Picking a component publishes everything inside it. The selection is remembered '
                      'in this design for the next publish.')
    scope_note = inputs.addTextBoxCommandInput('dxPub_scopeNote', '', '', 2, True)
    scope_note.isFullWidth = True

    def picking():
        return scope_choice.selectedItem is not None and scope_choice.selectedItem.name != 'Whole design'

    def picked_entities():
        return [picker.selection(i).entity for i in range(picker.selectionCount)]

    def scope_problem():
        """Why the current selection cannot be published, or '' when it can."""
        if not picking():
            return ''
        entities = picked_entities()
        if not entities:
            return 'Pick at least one component to publish.'
        if any(fusion_ops.inside_reference(design, entity) for entity in entities):
            return 'A loaded exchange (DX Reference) cannot be republished. Remove it from the selection.'
        return ''

    def sync_scope():
        picker.isVisible = picking()
        problem = scope_problem()
        if not picking():
            scope_note.text = 'Everything in this design except loaded exchanges.'
        elif problem:
            scope_note.text = problem
        else:
            count = picker.selectionCount
            scope_note.text = '%d item%s selected. New geometry outside the selection is not published.' % (
                count, '' if count == 1 else 's')
        if picking() and stored_missing and not problem:
            scope_note.text += ' Not found any more: %s.' % ', '.join(stored_missing[:5])

    def restore_selection():
        # Selections can only be added once the command is active, not while it is being created.
        for entity in stored_entities:
            try:
                picker.addSelection(entity)
            except Exception:
                pass
        sync_scope()

    _on(command.activate, _Destroyed(restore_selection))

    explorer = Explorer(command, inputs, 'dxPub_', False, selection_changed, height=280)
    new_inputs = [name, explorer.input]

    def is_new():
        return target.selectedItem is not None and target.selectedItem.name == 'New exchange'

    def sync_visibility():
        for control in new_inputs:
            control.isVisible = is_new()
        if is_new():
            selection_changed()
        else:
            status.text = ''

    def changed(event):
        if event.input.id == target.id:
            sync_visibility()
        elif event.input.id in (scope_choice.id, picker.id):
            sync_scope()

    def new_target():
        hub, project, folder = explorer.current_hub(), explorer.current_project(), explorer.current_folder()
        if not (name.value.strip() and hub and project and folder and not folder.get('readOnly')):
            return None
        return {'hubId': hub['hubId'], 'projectId': project['projectId'], 'folderId': folder['folderId'],
                'name': name.value.strip()}

    def run(_event):
        problem = scope_problem()
        if problem:
            raise fusion_ops.FusionOpError(problem)
        # Tokens are read now, while the dialog still holds the selection; the job resolves them again.
        scope = publish_scope.clean_scope(
            {'mode': publish_scope.MODE_SELECTION, 'items': fusion_ops.selection_items(picked_entities())}
            if picking() else None)
        if is_new():
            explorer.remember()
            target_new = new_target()
            if target_new is None:
                raise fusion_ops.FusionOpError(
                    'No folder was selected. Run Publish Exchange again and pick a folder you can write to.')
            _defer(lambda: _submit(('publish',), 'Publish "%s"' % target_new['name'], document,
                                   lambda job: _publish_job(job, document, None, target_new, scope)))
        else:
            existing = published['exchange']
            _defer(lambda: _submit(('publish',), 'Publish "%s"' % (existing.get('name') or 'exchange'), document,
                                   lambda job: _publish_job(job, document, existing, None, scope)))

    _on(command.inputChanged, _Changed(changed))
    _on(command.validateInputs, _Validate(
        lambda: (not is_new() or new_target() is not None) and not scope_problem()))
    _on(command.execute, _Execute(run))
    command.setDialogInitialSize(440, 680)
    command.setDialogMinimumSize(340, 440)
    sync_visibility()
    sync_scope()


def _publish_job(job, document, existing, new, scope=None):
    started = time.time()
    temp_dir = None
    helper = client()
    try:
        # An older helper would publish only the first component and drop the rest without a word.
        if not helper_is_current((yield jobs.Background('Checking the helper', helper.health))):
            raise fusion_ops.FusionOpError(OUTDATED_HELPER)
        yield jobs.WhenIdle('Exporting this design')
        design = fusion_ops.design_of(document)
        scope = publish_scope.clean_scope(scope)
        selection, missing = None, []
        if scope['mode'] == publish_scope.MODE_SELECTION:
            selection, missing = fusion_ops.resolve_scope(design, scope)
            if not selection:
                raise fusion_ops.FusionOpError(
                    'None of the selected components exist any more. Choose what to publish again.')
        # One element per published component, each with its own geometry file and properties.
        units, temp_dir, exported = fusion_ops.export_units(design, selection)
        first, others = units[0], units[1:]
        from_version = fusion_ops.saved_version(document)
        up_axis = fusion_ops.up_axis()

        def upload():
            published = helper.publish(first['stepPath'], element_source_id=first['sourceId'],
                                       element_name=first['name'], properties=first['properties'],
                                       more_elements=others, replace_owned=True,
                                       existing=existing, new=new, category='Generic Models', up_axis=up_axis)
            return published, helper.health()

        result, health = yield jobs.Background('Publishing to Autodesk', upload)
        if result['exchange'].get('name'):
            job.title = 'Publish "%s"' % result['exchange']['name']
        job.link = doc_meta.docs_url(result['exchange'], health.get('mode', 'sdk'))
        link_saved, save_note = True, ''
        try:
            # Writing the link and saving wait for a safe moment in this design, like an import does.
            yield jobs.WhenIdle('Saving the link in the design')
            design = fusion_ops.design_of(document)
            record = doc_meta.build_published(
                result['exchange'], result['revision']['revisionId'], health.get('mode', 'sdk'))
            # The automatic save below makes one more file version that holds exactly what was
            # published, so that is the version on record (unless the user saved in between).
            will_save = _auto_save() and from_version and fusion_ops.saved_version(document) == from_version
            if from_version:
                record['publishedFromVersion'] = from_version + 1 if will_save else from_version
            record['scope'] = scope
            fusion_ops.write_published(design, record)
            _alignment[record['exchangeId']] = {'latest': record['publishedRevisionId']}
            saved, save_note = _keep_link(document, 'Data Exchange: published "%s"' % (
                result['exchange'].get('name') or 'exchange'))
            if will_save and not saved:
                record['publishedFromVersion'] = from_version
                fusion_ops.write_published(design, record)
        except GeneratorExit:
            raise
        except Exception:
            link_saved = False
    finally:
        if temp_dir and os.path.isdir(temp_dir):
            shutil.rmtree(temp_dir, ignore_errors=True)

    lines = [
        '%s the exchange.' % ('Created' if result.get('created') else 'Updated'),
        'Published: %s, %d bodies in %d element%s, %d properties.' % (
            publish_scope.describe(scope, len(missing)).lower(), exported['bodies'],
            result.get('elementCount', 1), '' if result.get('elementCount', 1) == 1 else 's',
            result.get('propertyCount', 0)),
        'Revision: %s' % result['revision']['revisionId'],
        'Time: %.1f s' % (time.time() - started),
    ]
    if exported.get('combined'):
        lines.append('Sent as one element: this publish has %d components, more than the %d that are '
                     'published separately.' % (exported['combined'], publish_scope.MAX_UNITS))
    lines.extend(result.get('warnings') or [])
    if health.get('mode') == 'mock':
        lines.append('MOCK MODE: fixture data, not a real cloud exchange.')
    if not link_saved:
        lines.append('The design could not be updated with the link to this exchange (was it closed?). '
                     'The next publish from it will create a new exchange.')
    else:
        lines.append(save_note)
    return '\n'.join(lines)


# ---- Mock only: simulate a new published revision -----------------------------------------------

def _mock_advance_created(args):
    def run(_event):
        references = fusion_ops.find_references(fusion_ops.active_design())
        if not references:
            _ui().messageBox('Load the mock exchange first.', TITLE)
            return
        lines = []
        for component, metadata in references:
            advanced = fusion_ops.run_blocking(lambda: client().mock_advance(metadata['exchangeId']))
            lines.append('%s: %s' % (component.name, 'new revision published (mock). Now use Refresh Exchange.'
                                     if advanced.get('advanced') else 'no further mock revision.'))
        _ui().messageBox('\n'.join(lines), TITLE)

    _on(args.command.execute, _Execute(run))


# ---- registration -------------------------------------------------------------------------------

CMD_MOCK_ADVANCE = 'DX_MockAdvance'
_MOCK_COMMAND = (CMD_MOCK_ADVANCE, 'Mock: publish next revision',
                 'Mock mode only. Simulates a new revision of the loaded fixture exchange.', _mock_advance_created)

_COMMANDS = [
    (CMD_ACCOUNT, 'Account', 'Sign in to or out of Autodesk for Data Exchange.', _account_created),
    (CMD_LOAD, 'Load Exchange', 'Import a Data Exchange into this design as a read-only reference.', _load_created),
    (CMD_REFRESH, 'Refresh Exchange', 'Update loaded exchanges to their latest published revision.', _refresh_created),
    (CMD_PUBLISH, 'Publish Exchange', 'Publish this design as a Data Exchange.', _publish_created),
    (CMD_JOBS, 'Exchanges and Jobs',
     'Show the exchanges this design consumes and publishes, whether they match the cloud, and jobs in progress.',
     _jobs_created),
]


def start(addin_dir):
    global _addin_dir, _queue, _application
    _addin_dir = addin_dir
    set_addin_dir(addin_dir)
    ui = _ui()
    stop_ui()  # a crashed earlier run can leave controls behind
    application = adsk.core.Application.get()
    application.unregisterCustomEvent(DEFERRED_EVENT_ID)
    _on(application.registerCustomEvent(DEFERRED_EVENT_ID), _Deferred())
    _application = application
    _queue = jobs.JobQueue(_post_to_main, _job_gate, _describe, _jobs_changed, lock=_network)
    _listen(application.documentActivated, _DocumentActivated())
    _listen(ui.commandTerminated, _CommandTerminated())
    _start_poller()
    _jobs_palette()
    workspace = ui.workspaces.itemById(WORKSPACE_ID)
    tab = _automate_tab(workspace) or workspace.toolbarTabs.itemById(TAB_ID)
    panel = tab.toolbarPanels.add(PANEL_ID, PANEL_NAME)
    commands = _COMMANDS + ([_MOCK_COMMAND] if load_config().get('mock') else [])
    icons = os.path.join(addin_dir, 'resources', 'dx')
    for command_id, label, tooltip, build in commands:
        if os.path.isfile(os.path.join(icons, '32x32.png')):
            definition = ui.commandDefinitions.addButtonDefinition(command_id, label, tooltip, icons)
        else:
            definition = ui.commandDefinitions.addButtonDefinition(command_id, label, tooltip)
        _on(definition.commandCreated, _Created(build))
        control = panel.controls.addCommand(definition)
        control.isPromoted = command_id in (CMD_LOAD, CMD_REFRESH)


def _tabs(workspace):
    tabs = workspace.toolbarTabs
    return [tabs.item(i) for i in range(tabs.count)]


def _automate_tab(workspace):
    """The Design workspace's Automate tab, found by id or by name; None when this Fusion has none.

    The tab id is not documented, so nothing is assumed: any tab whose id or name starts with
    'automat' (Automate / Automation) matches. start() falls back to the Solid tab.
    """
    for tab in _tabs(workspace):
        try:
            if tab.id.lower().startswith('automat') or tab.name.strip().lower().startswith('automat'):
                return tab
        except Exception:
            continue
    return None


def stop_ui():
    ui = _ui()
    workspace = ui.workspaces.itemById(WORKSPACE_ID)
    every = _COMMANDS + [_MOCK_COMMAND]
    palette = ui.palettes.itemById(JOBS_PALETTE_ID)
    if palette:
        palette.deleteMe()
    # The panel may sit on the Automate tab or, on older builds or earlier versions, the Solid tab.
    for tab in (_tabs(workspace) if workspace else []):
        panel = tab.toolbarPanels.itemById(PANEL_ID)
        if not panel:
            continue
        for command_id, _, _, _ in every:
            control = panel.controls.itemById(command_id)
            if control:
                control.deleteMe()
        panel.deleteMe()
    for command_id, _, _, _ in every:
        definition = ui.commandDefinitions.itemById(command_id)
        if definition:
            definition.deleteMe()


def stop():
    global _client, _queue
    for event, handler in _app_events:
        try:
            event.remove(handler)
        except Exception:
            pass
    del _app_events[:]
    _stop_poller()
    if _queue is not None:
        _queue.stop()  # closes unfinished jobs so their temp files are cleaned up
        _queue = None
    _failures_shown.clear()
    stop_ui()
    adsk.core.Application.get().unregisterCustomEvent(DEFERRED_EVENT_ID)
    del _pending[:]
    _handlers.clear()
    if _client is not None:
        _client.stop()
        _client = None
