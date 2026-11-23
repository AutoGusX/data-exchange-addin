"""Client for the local .NET helper. No Fusion imports here, so this runs under plain Python tests.

The add-in starts the helper on demand, hands it a random per-launch secret through the environment,
reads the port from the helper's single READY line, and sends the secret as a bearer token.
"""
import json
import os
import secrets
import subprocess
import threading
import urllib.error
import urllib.parse
import urllib.request

READY_PREFIX = 'DXHELPER_READY '
SECRET_ENV = 'DXHELPER_SECRET'
START_TIMEOUT_S = 30
DEFAULT_TIMEOUT_S = 120
LOGIN_TIMEOUT_S = 330      # the user is in a browser
TRANSFER_TIMEOUT_S = 1800  # export / publish of a large exchange


# The oldest helper this add-in can work with. An older one silently ignores request fields it does
# not know (for example the extra elements of a publish), so the add-in refuses to use it for those.
REQUIRED_HELPER = (0, 3, 0)


def version_tuple(text):
    parts = []
    for piece in str(text or '').split('.')[:3]:
        digits = ''.join(ch for ch in piece if ch.isdigit())
        parts.append(int(digits) if digits else 0)
    return tuple(parts + [0] * (3 - len(parts)))


def helper_is_current(health):
    return version_tuple((health or {}).get('helperVersion')) >= REQUIRED_HELPER


OUTDATED_HELPER = ('The Data Exchange helper next to the add-in is older than the add-in. Stop the add-in in '
                   'Fusion, run scripts\\deploy.ps1 (or reinstall from the zip), then run it again.')


class HelperError(Exception):
    """A structured helper error (contracts/errors.schema.json)."""

    def __init__(self, code, message, correlation_id=None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.correlation_id = correlation_id

    def __str__(self):
        ref = ' (ref %s)' % self.correlation_id if self.correlation_id else ''
        return '%s%s' % (self.message, ref)


def data_dir():
    return os.path.join(os.environ.get('LOCALAPPDATA', os.path.expanduser('~')), 'FusionDataExchange')


_bundled_config_dir = None


def set_addin_dir(addin_dir):
    """Tell the config loader where the add-in is installed, so it finds the config shipped with it."""
    global _bundled_config_dir
    _bundled_config_dir = addin_dir


def _read_json(path):
    try:
        with open(path, 'r', encoding='utf-8-sig') as f:
            config = json.load(f)
            return config if isinstance(config, dict) else {}
    except (OSError, ValueError):
        return {}


def load_config():
    """Settings: clientId, callbackUrl, mock, helperPath, autoSave.

    Two files, the second overriding the first key by key:
    1. config.json inside the add-in folder (shipped with a packaged add-in, so no setup is needed);
    2. %LOCALAPPDATA%\\FusionDataExchange\\config.json (this user's own settings).
    """
    config = _read_json(os.path.join(_bundled_config_dir, 'config.json')) if _bundled_config_dir else {}
    config.update(_read_json(os.path.join(data_dir(), 'config.json')))
    return config


def find_helper_exe(addin_dir, config=None):
    candidates = [
        os.environ.get('DX_HELPER_EXE'),
        (config or {}).get('helperPath'),
        os.path.join(addin_dir, 'helper', 'FusionDxHelper.exe'),
    ]
    for candidate in candidates:
        if candidate and os.path.isfile(candidate):
            return candidate
    raise HelperError('SDK_UNAVAILABLE',
                      'The Data Exchange helper is not installed next to the add-in. Run scripts/deploy.ps1.')


class HelperClient:
    def __init__(self, exe_path, mock=False, fixtures_dir=None):
        self.exe_path = exe_path
        self.mock = mock
        self.fixtures_dir = fixtures_dir
        self._process = None
        self._port = None
        self._secret = None
        self._lock = threading.Lock()

    # ---- process -------------------------------------------------------------------------

    def is_running(self):
        return self._process is not None and self._process.poll() is None

    def ensure_started(self):
        with self._lock:
            if self.is_running():
                return
            self._secret = secrets.token_hex(32)
            env = dict(os.environ)
            env[SECRET_ENV] = self._secret
            args = [self.exe_path, 'serve', '--parent-pid', str(os.getpid())]
            if self.mock:
                args.append('--mock')
                if self.fixtures_dir:
                    args += ['--fixtures', self.fixtures_dir]
            flags = getattr(subprocess, 'CREATE_NO_WINDOW', 0)
            self._process = subprocess.Popen(
                args, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, stdin=subprocess.DEVNULL,
                creationflags=flags, text=True, cwd=os.path.dirname(self.exe_path))
            self._port = self._read_ready(self._process)

    @staticmethod
    def _read_ready(process):
        found = {}
        ready = threading.Event()
        errors = []

        # Both pipes are read until the helper exits. The SDK writes progress lines to stdout
        # (seen live); an unread pipe would fill up and freeze the helper mid-request.
        def read_out():
            try:
                for line in process.stdout:
                    if not ready.is_set() and line.startswith(READY_PREFIX):
                        try:
                            found['port'] = int(json.loads(line[len(READY_PREFIX):])['port'])
                        except (ValueError, KeyError):
                            pass
                        ready.set()
            except (OSError, ValueError):
                pass
            ready.set()

        def read_err():
            try:
                for line in process.stderr:
                    if len(errors) < 20:
                        errors.append(line.strip())
            except (OSError, ValueError):
                pass

        threading.Thread(target=read_out, daemon=True).start()
        threading.Thread(target=read_err, daemon=True).start()
        ready.wait(START_TIMEOUT_S)
        if 'port' not in found:
            if process.poll() is None:
                process.kill()
            detail = ' '.join(errors)[:300]
            raise HelperError('SDK_UNAVAILABLE', 'The Data Exchange helper did not start. %s' % detail)
        return found['port']

    def stop(self):
        with self._lock:
            if self._process is not None and self._process.poll() is None:
                self._process.terminate()
                try:
                    self._process.wait(5)
                except subprocess.TimeoutExpired:
                    self._process.kill()
            if self._process is not None:
                for pipe in (self._process.stdout, self._process.stderr):
                    if pipe:
                        pipe.close()
            self._process = None
            self._port = None
            self._secret = None

    # ---- transport -----------------------------------------------------------------------

    def _request(self, method, path, body=None, query=None, timeout=DEFAULT_TIMEOUT_S, auth=True):
        self.ensure_started()
        url = 'http://127.0.0.1:%d%s' % (self._port, path)
        if query:
            url += '?' + urllib.parse.urlencode({k: v for k, v in query.items() if v is not None})
        data = None
        headers = {'Accept': 'application/json'}
        if body is not None:
            data = json.dumps(body).encode('utf-8')
            headers['Content-Type'] = 'application/json'
        elif method == 'POST':
            data = b''
        if auth:
            headers['Authorization'] = 'Bearer ' + self._secret
        request = urllib.request.Request(url, data=data, headers=headers, method=method)
        # The helper is on loopback: never route through a system proxy.
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        try:
            with opener.open(request, timeout=timeout) as response:
                raw = response.read()
                return json.loads(raw) if raw else None
        except urllib.error.HTTPError as e:
            raise self._to_error(e)
        except (urllib.error.URLError, OSError) as e:
            raise HelperError('SDK_UNAVAILABLE', 'Lost contact with the Data Exchange helper: %s' % e)

    @staticmethod
    def _to_error(http_error):
        try:
            payload = json.loads(http_error.read() or b'{}')
        except ValueError:
            payload = {}
        return HelperError(payload.get('code') or 'INTERNAL_ERROR',
                           payload.get('message') or 'Helper returned HTTP %d.' % http_error.code,
                           payload.get('correlationId'))

    # ---- API -----------------------------------------------------------------------------

    def health(self):
        return self._request('GET', '/v1/health', auth=False, timeout=10)

    def login(self):
        return self._request('POST', '/v1/auth/login', timeout=LOGIN_TIMEOUT_S)

    def logout(self):
        return self._request('POST', '/v1/auth/logout')

    def account(self):
        """The signed-in account, or None."""
        try:
            return self._request('GET', '/v1/auth/account', timeout=10)
        except HelperError as e:
            if e.code == 'AUTH_REQUIRED':
                return None
            raise

    def hubs(self):
        return self._request('GET', '/v1/hubs')

    def projects(self, hub_id):
        return self._request('GET', '/v1/projects', query={'hubId': hub_id})

    def folders(self, hub_id, project_id, parent_folder_id=None):
        return self._request('GET', '/v1/folders', query={
            'hubId': hub_id, 'projectId': project_id, 'parentFolderId': parent_folder_id})

    def exchanges(self, hub_id, project_id, folder_id):
        return self._request('GET', '/v1/exchanges', query={
            'hubId': hub_id, 'projectId': project_id, 'folderId': folder_id})

    def thumbnail(self, hub_id, project_id, file_urn):
        """{'available': bool, 'path': PNG file or None} for an exchange listed in a folder."""
        return self._request('GET', '/v1/exchanges/thumbnail', query={
            'hubId': hub_id, 'projectId': project_id, 'fileUrn': file_urn})

    def resolve(self, hub_id, project_id, file_urn):
        return self._request('POST', '/v1/exchanges/resolve', body={
            'hubId': hub_id, 'projectId': project_id, 'fileUrn': file_urn})

    def latest_revision(self, exchange):
        return self._request('POST', '/v1/exchanges/latest-revision', body={'exchange': exchange})

    def changes(self, exchange, from_revision_id):
        """Elements created, modified and deleted after from_revision_id (counts plus the first names)."""
        return self._request('POST', '/v1/exchanges/changes',
                             body={'exchange': exchange, 'fromRevisionId': from_revision_id},
                             timeout=TRANSFER_TIMEOUT_S)

    def export(self, exchange):
        return self._request('POST', '/v1/exchanges/export', body={'exchange': exchange},
                             timeout=TRANSFER_TIMEOUT_S)

    def export_mesh(self, exchange):
        """Like export, but the geometry is one mesh (OBJ) file: the fallback when STEP holds no bodies."""
        return self._request('POST', '/v1/exchanges/export-mesh', body={'exchange': exchange},
                             timeout=TRANSFER_TIMEOUT_S)

    def cleanup(self, output_directory):
        try:
            self._request('POST', '/v1/exports/cleanup', body={'outputDirectory': output_directory}, timeout=30)
        except HelperError:
            pass  # the helper also clears its export folder on start and stop

    def status(self):
        """Transfer progress and updates for watched exchanges. Answered from the helper's memory."""
        return self._request('GET', '/v1/status', timeout=5)

    def watch(self, exchange_ids):
        return self._request('POST', '/v1/events/watch', body={'exchangeIds': list(exchange_ids)}, timeout=30)

    def data_only_elements(self, hub_id, project_id, file_urn):
        """Element data of a data-only (AEC Data Model based) exchange: {'name', 'total', 'listed', 'source'}."""
        return self._request('POST', '/v1/exchanges/data',
                             body={'hubId': hub_id, 'projectId': project_id, 'fileUrn': file_urn}, timeout=300)

    def delete_exchange(self, hub_id, project_id, file_urn):
        """Permanently delete an exchange. Callers confirm with the user first."""
        return self._request('POST', '/v1/exchanges/delete', body={
            'hubId': hub_id, 'projectId': project_id, 'fileUrn': file_urn})

    def publish(self, step_path, element_source_id, element_name, existing=None, new=None, category=None,
                up_axis=None, properties=None, more_elements=None, replace_owned=False):
        body = {'stepPath': step_path, 'elementSourceId': element_source_id, 'elementName': element_name}
        if existing is not None:
            body['existing'] = existing
        if new is not None:
            body['new'] = new
        if category:
            body['category'] = category
        if up_axis:
            body['upAxis'] = up_axis
        if properties:
            body['properties'] = properties
        if more_elements:
            body['moreElements'] = list(more_elements)
        if replace_owned:
            body['replaceOwned'] = True
        return self._request('POST', '/v1/exchanges/publish', body=body, timeout=TRANSFER_TIMEOUT_S)

    def mock_advance(self, exchange_id):
        return self._request('POST', '/v1/mock/advance', body={'exchangeId': exchange_id})
