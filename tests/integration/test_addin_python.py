"""Plain-Python tests for the add-in code that does not need Fusion.

Run:  python -m unittest discover -s tests/integration -v
The helper tests start the built helper in mock mode (run scripts/build.ps1 first, or set DX_HELPER_EXE).
"""
import importlib.util
import os
import queue
import sys
import tempfile
import unittest

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..'))
LIB = os.path.join(REPO, 'fusion_addin', 'DataExchange', 'lib')


def _load(name):
    # Load single modules by path: importing the package would pull in adsk, which only exists in Fusion.
    spec = importlib.util.spec_from_file_location(name, os.path.join(LIB, name + '.py'))
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


doc_meta = _load('doc_meta')
helper_client = _load('helper_client')
jobs = _load('jobs')
favorites = _load('favorites')
data_only = _load('data_only')
element_link = _load('element_link')
publish_scope = _load('publish_scope')

EXCHANGE = {'exchangeId': 'ex-1', 'collectionId': 'col-1', 'hubId': 'hub-1', 'name': 'Core', 'extra': 'dropped'}


def _helper_exe():
    candidates = [
        os.environ.get('DX_HELPER_EXE'),
        os.path.join(REPO, 'dist', 'helper', 'FusionDxHelper.exe'),
        os.path.join(REPO, 'helper', 'src', 'FusionDxHelper.Api', 'bin', 'Debug', 'net10.0-windows7.0', 'win-x64',
                     'FusionDxHelper.exe'),
    ]
    return next((c for c in candidates if c and os.path.isfile(c)), None)


class DocMetaTests(unittest.TestCase):
    def test_reference_round_trip_keeps_spec_fields(self):
        built = doc_meta.build_reference(EXCHANGE, 'rev-1', 'mock', now='2026-11-16T00:00:00Z')
        read = doc_meta.loads(doc_meta.dumps(built))
        for key in ('schemaVersion', 'exchangeId', 'projectId', 'importedRevisionId', 'importMode', 'importedAtUtc'):
            self.assertIn(key, read)
        self.assertEqual(read['importMode'], 'read-only-reference')
        self.assertEqual(read['exchange']['collectionId'], 'col-1')
        self.assertNotIn('extra', read['exchange'])

    def test_no_token_like_fields_are_stored(self):
        text = doc_meta.dumps(doc_meta.build_reference(dict(EXCHANGE, accessToken='secret'), 'rev-1', 'sdk'))
        self.assertNotIn('secret', text)
        self.assertNotIn('oken', text)

    def test_needs_refresh_only_when_revision_differs(self):
        built = doc_meta.build_reference(EXCHANGE, 'rev-1', 'sdk')
        self.assertFalse(doc_meta.needs_refresh(built, 'rev-1'))
        self.assertTrue(doc_meta.needs_refresh(built, 'rev-2'))

    def test_rejects_untrustworthy_metadata(self):
        good = doc_meta.build_reference(EXCHANGE, 'rev-1', 'sdk')
        for bad in ('not json', '[]', doc_meta.dumps(dict(good, schemaVersion=2)),
                    doc_meta.dumps(dict(good, exchangeId='other')),
                    doc_meta.dumps(dict(good, exchange={'exchangeId': 'ex-1'}))):
            with self.assertRaises(doc_meta.MetadataError):
                doc_meta.loads(bad)

    def test_rejects_missing_ids_and_bad_placement(self):
        with self.assertRaises(doc_meta.MetadataError):
            doc_meta.build_reference({'exchangeId': 'x'}, 'rev', 'sdk')
        with self.assertRaises(doc_meta.MetadataError):
            doc_meta.build_reference(EXCHANGE, '', 'sdk')
        with self.assertRaises(doc_meta.MetadataError):
            doc_meta.build_reference(EXCHANGE, 'rev', 'sdk', placement='sideways')

    def test_docs_url(self):
        exchange = {'projectId': 'b.proj1', 'folderUrn': 'urn:adsk.wipprod:fs.folder:co.folder1',
                    'fileUrn': 'urn:adsk.wipprod:dm.lineage:item1'}
        self.assertEqual(
            doc_meta.docs_url(exchange),
            'https://acc.autodesk.com/docs/files/projects/proj1?folderUrn=urn%3Aadsk.wipprod%3Afs.folder%3Aco.folder1'
            '&entityId=urn%3Aadsk.wipprod%3Adm.lineage%3Aitem1&viewModel=detail&moduleId=folders')
        # No usable item id: the link opens the folder. No folder, or mock data: no link.
        self.assertEqual(
            doc_meta.docs_url(dict(exchange, fileUrn='something-else')),
            'https://acc.autodesk.com/docs/files/projects/proj1?folderUrn=urn%3Aadsk.wipprod%3Afs.folder%3Aco.folder1'
            '&moduleId=folders')
        self.assertIsNone(doc_meta.docs_url({'projectId': 'b.proj1'}))
        self.assertIsNone(doc_meta.docs_url(exchange, 'mock'))

    def test_alignment(self):
        self.assertEqual(doc_meta.consumed_alignment('r1', None)[0], 'unknown')
        self.assertEqual(doc_meta.consumed_alignment('r1', {'latest': 'r1'})[0], 'current')
        self.assertEqual(doc_meta.consumed_alignment('r1', {'latest': 'r2'})[0], 'behind')
        self.assertEqual(doc_meta.consumed_alignment('r1', {'error': 'offline'}), ('error', 'offline'))
        self.assertEqual(doc_meta.published_alignment('r1', {'latest': 'r1'})[0], 'current')
        self.assertEqual(doc_meta.published_alignment('r1', {'latest': 'r2'})[0], 'diverged')
        self.assertEqual(doc_meta.published_alignment('r1', None)[0], 'unknown')
        self.assertEqual(doc_meta.local_change_notes(5, 5, False), [])
        self.assertEqual(doc_meta.local_change_notes(None, 7, False), [])
        notes = doc_meta.local_change_notes(5, 7, True)
        self.assertIn('Saved 2 time(s)', notes[0])
        self.assertEqual(notes[1], 'This design has unsaved changes.')
        # The extra field survives a round trip through the stored record.
        record = dict(doc_meta.build_published(EXCHANGE, 'rev-1', 'sdk'), publishedFromVersion=5)
        self.assertEqual(doc_meta.loads(doc_meta.dumps(record))['publishedFromVersion'], 5)

    def test_changes_note(self):
        self.assertEqual(doc_meta.changes_note(None), '')
        self.assertEqual(doc_meta.changes_note({'revisionsBehind': 0}), '')
        note = doc_meta.changes_note({'revisionsBehind': 2, 'createdCount': 3, 'modifiedCount': 1, 'deletedCount': 0,
                                      'created': [{'name': 'Wall'}, {'sourceElementId': 'e2'}]})
        self.assertEqual(note, 'Since the loaded revision (2 revisions): 3 added, 1 modified, 0 deleted. '
                               'Added: Wall, e2, ….')
        self.assertIn('no element changes', doc_meta.changes_note({'revisionsBehind': 1}))

    def test_pack_elements_and_mesh_units(self):
        packed = doc_meta.pack_elements([
            {'displayName': 'Wall:15', 'category': 'Walls', 'properties': {'sourceCategory': 'Walls', 'PartNumber': 'Wall', 'Empty': ''}},
            {'sourceElementId': 'e2'}])
        self.assertEqual(packed['total'], 2)
        self.assertEqual(packed['listed'][0], {'name': 'Wall:15', 'category': 'Walls', 'properties': {'PartNumber': 'Wall'}})
        self.assertEqual(packed['listed'][1], {'name': 'e2', 'category': '', 'properties': {}})
        many = doc_meta.pack_elements([{'displayName': str(i)} for i in range(doc_meta.MAX_ELEMENTS_STORED + 5)])
        self.assertEqual((many['total'], len(many['listed'])), (doc_meta.MAX_ELEMENTS_STORED + 5, doc_meta.MAX_ELEMENTS_STORED))
        self.assertEqual(doc_meta.mesh_unit('Feet'), 'Foot')
        self.assertEqual(doc_meta.mesh_unit(' centimeters '), 'Centimeter')
        self.assertIsNone(doc_meta.mesh_unit('Furlongs'))
        self.assertIsNone(doc_meta.mesh_unit(None))

    def test_names(self):
        self.assertEqual(doc_meta.reference_name('Building Core'), 'DX Reference — Building Core')
        self.assertEqual(doc_meta.safe_file_stem('a/b:c*d'), 'a_b_c_d')
        self.assertEqual(doc_meta.safe_file_stem(''), 'design')


class PublishScopeTests(unittest.TestCase):
    OCCURRENCES = ['Frame:1', 'Frame:1+Bracket:1', 'Frame:1+Bracket:2', 'Motor:1', 'DX Reference:1',
                   'DX Reference:1+Wall:1']
    BODIES = [('', 'Base'), ('', 'Cover'), ('Frame:1', 'Rail'), ('Frame:1+Bracket:1', 'Plate'),
              ('Frame:1+Bracket:2', 'Plate'), ('Motor:1', 'Housing'), ('DX Reference:1+Wall:1', 'Wall')]
    EXCLUDED = ['DX Reference:1']

    def plan(self, occurrences=None, bodies=None):
        return publish_scope.plan(self.OCCURRENCES, self.BODIES, self.EXCLUDED, occurrences, bodies)

    def test_whole_design_leaves_out_only_references(self):
        steps = self.plan()
        self.assertEqual(steps['hide_occurrences'], ['DX Reference:1', 'DX Reference:1+Wall:1'])
        self.assertEqual(steps['hide_bodies'], [])
        self.assertEqual(len(steps['kept_bodies']), 6)

    def test_selected_component_keeps_everything_inside_it(self):
        steps = self.plan(occurrences=['Frame:1'], bodies=[])
        self.assertEqual(steps['kept_bodies'],
                         [('Frame:1', 'Rail'), ('Frame:1+Bracket:1', 'Plate'), ('Frame:1+Bracket:2', 'Plate')])
        self.assertEqual(steps['hide_occurrences'], ['Motor:1', 'DX Reference:1', 'DX Reference:1+Wall:1'])
        self.assertEqual(steps['hide_bodies'], [('', 'Base'), ('', 'Cover')])

    def test_nested_component_keeps_its_parents_visible_but_not_their_bodies(self):
        steps = self.plan(occurrences=['Frame:1+Bracket:2'], bodies=[])
        self.assertEqual(steps['kept_bodies'], [('Frame:1+Bracket:2', 'Plate')])
        self.assertNotIn('Frame:1', steps['hide_occurrences'])
        self.assertIn('Frame:1+Bracket:1', steps['hide_occurrences'])
        self.assertIn(('Frame:1', 'Rail'), steps['hide_bodies'])

    def test_selected_bodies_only(self):
        steps = self.plan(occurrences=[], bodies=[('', 'Cover'), ('Motor:1', 'Housing')])
        self.assertEqual(steps['kept_bodies'], [('', 'Cover'), ('Motor:1', 'Housing')])
        self.assertEqual(steps['hide_bodies'], [('', 'Base')])
        self.assertIn('Frame:1', steps['hide_occurrences'])
        self.assertNotIn('Motor:1', steps['hide_occurrences'])

    def test_references_are_never_kept_even_when_selected(self):
        steps = self.plan(occurrences=['DX Reference:1'], bodies=[('DX Reference:1+Wall:1', 'Wall')])
        self.assertEqual(steps['kept_bodies'], [])

    def units(self, occurrences=None, bodies=None):
        return [(u['kind'], u['path']) for u in publish_scope.plan_units(
            self.OCCURRENCES, self.BODIES, self.EXCLUDED, occurrences, bodies)]

    def test_units_for_the_whole_design(self):
        self.assertEqual(self.units(), [('bodies', ''), ('occurrence', 'Frame:1'), ('occurrence', 'Motor:1')])

    def test_units_for_a_selection(self):
        self.assertEqual(
            self.units(['Frame:1', 'Frame:1+Bracket:2', 'DX Reference:1'],
                       [('', 'Cover'), ('', 'Base'), ('Motor:1', 'Housing'), ('Frame:1', 'Rail')]),
            [('bodies', ''), ('bodies', 'Motor:1'), ('occurrence', 'Frame:1')])

    def test_unit_ids_are_stable_and_distinct(self):
        self.assertEqual(publish_scope.unit_id('root-id', '', True), 'fusion:root-id')
        self.assertEqual(publish_scope.unit_id('c1', 'Frame:1+Bracket:2'), 'fusion:c1:1.2')
        self.assertEqual(publish_scope.unit_id('c1', 'Frame (renamed):1+Bracket:2'), 'fusion:c1:1.2')
        self.assertEqual(publish_scope.unit_id('c2', 'Motor:1', True), 'fusion:c2:1:bodies')

    def test_step_guard(self):
        fixture = os.path.join(REPO, 'tests', 'fixtures', 'building-core-rev1.step')
        self.assertEqual(publish_scope.count_step_solids(fixture), 6)
        self.assertIsNone(publish_scope.check_export(6, 6))
        self.assertIsNone(publish_scope.check_export(2, 6))
        self.assertIn('Nothing was published', publish_scope.check_export(7, 6))
        self.assertEqual(publish_scope.count_step_shapes(fixture), 6)
        self.assertEqual(publish_scope.count_step_shapes(__file__), 0)

    def test_scope_storage_and_description(self):
        self.assertEqual(publish_scope.clean_scope(None), {'mode': 'all'})
        self.assertEqual(publish_scope.clean_scope({'mode': 'selection', 'items': []}), {'mode': 'all'})
        scope = publish_scope.clean_scope({'mode': 'selection', 'items': [
            {'token': 't1', 'kind': 'occurrence', 'name': 'Frame:1', 'extra': 1}, {'token': ''}, 'junk']})
        self.assertEqual(scope['items'], [{'token': 't1', 'kind': 'occurrence', 'name': 'Frame:1'}])
        self.assertEqual(publish_scope.describe(scope), '1 selected item')
        self.assertEqual(publish_scope.describe(scope, 2), '1 selected item (2 no longer exist)')
        self.assertEqual(publish_scope.describe({'mode': 'all'}), 'Whole design')
        record = dict(doc_meta.build_published(EXCHANGE, 'rev-1', 'sdk'), scope=scope)
        self.assertEqual(doc_meta.loads(doc_meta.dumps(record))['scope'], scope)


class FavoritesTests(unittest.TestCase):
    LOCATION = {'kind': 'folder', 'path': ['Hub', 'Project', 'Project Files', 'Exchanges'],
                'hub': {'hubId': 'h1', 'name': 'Hub', 'region': 'US'},
                'project': {'projectId': 'p1', 'name': 'Project'},
                'folder': {'folderId': 'f1', 'name': 'Exchanges', 'readOnly': False, 'hasChildren': True},
                'exchange': None, 'accessToken': 'secret'}

    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        self.path = favorites.file_path(os.path.join(self.dir.name, 'nested'))

    def test_add_persists_only_ids_and_names(self):
        self.assertEqual(favorites.load(self.path), [])
        saved = favorites.add(self.path, self.LOCATION)
        self.assertEqual(saved, favorites.load(self.path))
        self.assertEqual(saved[0]['folder'], {'folderId': 'f1', 'name': 'Exchanges', 'readOnly': False})
        self.assertEqual(saved[0]['hub'], {'hubId': 'h1', 'name': 'Hub'})
        self.assertEqual(saved[0]['path'][-1], 'Exchanges')
        with open(self.path, encoding='utf-8') as f:
            self.assertNotIn('secret', f.read())

    def test_no_duplicates_and_remove(self):
        favorites.add(self.path, self.LOCATION)
        other = dict(self.LOCATION, folder={'folderId': 'f2', 'name': 'Other'})
        favorites.add(self.path, other)
        self.assertEqual(len(favorites.add(self.path, self.LOCATION)), 2)
        left = favorites.remove(self.path, self.LOCATION)
        self.assertEqual([f['folder']['folderId'] for f in left], ['f2'])
        self.assertEqual(favorites.load(self.path), left)

    def test_rejects_things_that_are_not_folders_and_survives_a_bad_file(self):
        self.assertEqual(favorites.add(self.path, {'hub': {'hubId': 'h1'}, 'project': None, 'folder': None}), [])
        self.assertEqual(favorites.add(self.path, 'nonsense'), [])
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        with open(self.path, 'w', encoding='utf-8') as f:
            f.write('{not json')
        self.assertEqual(favorites.load(self.path), [])
        self.assertEqual(len(favorites.add(self.path, self.LOCATION)), 1)

    def test_mock_mode_uses_its_own_file(self):
        self.assertNotEqual(favorites.file_path('d'), favorites.file_path('d', mock=True))


class ConfigTests(unittest.TestCase):
    """Settings shipped in the add-in folder work with no per-user file; the user's file overrides them."""

    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        self.addin = os.path.join(self.dir.name, 'DataExchange')
        self.user = os.path.join(self.dir.name, 'local')
        os.makedirs(self.addin)
        os.makedirs(os.path.join(self.user, 'FusionDataExchange'))
        saved = os.environ.get('LOCALAPPDATA')
        os.environ['LOCALAPPDATA'] = self.user
        self.addCleanup(lambda: os.environ.__setitem__('LOCALAPPDATA', saved) if saved is not None
                        else os.environ.pop('LOCALAPPDATA', None))
        self.addCleanup(helper_client.set_addin_dir, None)

    def write(self, folder, text):
        with open(os.path.join(folder, 'config.json'), 'w', encoding='utf-8') as f:
            f.write(text)

    def test_shipped_config_alone_is_enough(self):
        self.write(self.addin, '{"clientId": "shipped", "callbackUrl": "http://localhost:47821/"}')
        helper_client.set_addin_dir(self.addin)
        self.assertEqual(helper_client.load_config()['clientId'], 'shipped')

    def test_user_file_overrides_key_by_key(self):
        self.write(self.addin, '{"clientId": "shipped", "callbackUrl": "http://localhost:47821/"}')
        self.write(os.path.join(self.user, 'FusionDataExchange'), '{"clientId": "mine", "mock": true}')
        helper_client.set_addin_dir(self.addin)
        self.assertEqual(helper_client.load_config(),
                         {'clientId': 'mine', 'callbackUrl': 'http://localhost:47821/', 'mock': True})

    def test_missing_or_broken_files_are_ignored(self):
        helper_client.set_addin_dir(self.addin)
        self.assertEqual(helper_client.load_config(), {})
        self.write(self.addin, '{broken')
        self.assertEqual(helper_client.load_config(), {})


class JobQueueTests(unittest.TestCase):
    """The queue with a fake main thread: posted callables are pumped by the test."""

    def setUp(self):
        self.posted = queue.Queue()
        self.busy = None       # the gate's answer: None means Fusion is free
        self.changes = 0
        self.queue = jobs.JobQueue(self.posted.put, self._gate, str, self._changed)

    def _gate(self, job):
        if isinstance(self.busy, Exception):
            raise self.busy
        if self.busy and job.force:
            return None
        job.can_continue = bool(self.busy)
        return self.busy

    def _changed(self):
        self.changes += 1

    def pump(self, count=1):
        for _ in range(count):
            self.posted.get(timeout=5)()

    def test_background_then_document_work_then_result(self):
        seen = []

        def job_body(job):
            value = yield jobs.Background('Downloading', lambda: 21 * 2)
            seen.append(value)
            yield jobs.WhenIdle('Importing')
            seen.append('imported')
            return 'Loaded %d' % value

        job = self.queue.submit('Load', 'Doc', None, job_body)
        self.assertEqual((job.state, job.message), (jobs.RUNNING, 'Downloading'))
        self.pump()
        self.assertEqual(seen, [42, 'imported'])
        self.assertEqual((job.state, job.result), (jobs.DONE, 'Loaded 42'))
        self.assertEqual(self.queue.active_count(), 0)
        self.assertTrue(self.changes >= 3)

    def test_waits_for_fusion_then_resumes_on_poke(self):
        cleaned = []

        def job_body(job):
            try:
                yield jobs.WhenIdle('Importing')
                return 'ok'
            finally:
                cleaned.append(True)

        self.busy = 'Waiting for the current Fusion command to finish'
        job = self.queue.submit('Load', 'Doc', None, job_body)
        self.assertEqual((job.state, job.message), (jobs.WAITING, self.busy))
        self.assertTrue(job.to_dict()['canContinue'])
        self.queue.poke()
        self.assertEqual(job.state, jobs.WAITING)
        self.busy = None
        self.queue.poke()
        self.assertEqual((job.state, job.result, cleaned), (jobs.DONE, 'ok', [True]))

    def test_continue_now_forces_a_waiting_job(self):
        def job_body(job):
            yield jobs.WhenIdle('Importing')
            return 'forced'

        self.busy = 'busy'
        job = self.queue.submit('Load', 'Doc', None, job_body)
        self.queue.continue_now(job.id)
        self.assertEqual((job.state, job.result, job.force), (jobs.DONE, 'forced', False))

    def test_background_error_is_thrown_into_the_job_and_cleanup_runs(self):
        cleaned = []

        def fail():
            raise ValueError('network down')

        def job_body(job):
            try:
                yield jobs.Background('Downloading', fail)
            finally:
                cleaned.append(True)

        job = self.queue.submit('Load', 'Doc', None, job_body)
        self.pump()
        self.assertEqual((job.state, job.result, cleaned), (jobs.FAILED, 'network down', [True]))

    def test_closed_document_fails_the_job_with_cleanup(self):
        cleaned = []

        def job_body(job):
            try:
                yield jobs.WhenIdle('Importing')
            finally:
                cleaned.append(True)

        self.busy = RuntimeError('The design was closed.')
        job = self.queue.submit('Load', 'Doc', None, job_body)
        self.assertEqual((job.state, job.result, cleaned), (jobs.FAILED, 'The design was closed.', [True]))

    def test_jobs_run_side_by_side_and_finished_ones_clear(self):
        def job_body(job):
            value = yield jobs.Background('Working', lambda: job.id)
            return str(value)

        first = self.queue.submit('A', 'Doc', None, job_body)
        second = self.queue.submit('B', 'Doc', None, job_body)
        self.assertEqual(self.queue.active_count(), 2)
        self.pump(2)
        self.assertEqual((first.result, second.result), (str(first.id), str(second.id)))
        self.assertEqual([j['title'] for j in self.queue.snapshot()], ['B', 'A'])
        self.queue.clear_finished()
        self.assertEqual(self.queue.snapshot(), [])

    def test_progress_shows_on_the_job_doing_network_work(self):
        import threading
        started, release = threading.Event(), threading.Event()

        def slow():
            started.set()
            release.wait(5)
            return 1

        def job_body(job):
            yield jobs.Background('Downloading', slow)
            yield jobs.WhenIdle('Importing')
            return 'ok'

        job = self.queue.submit('Load', 'Doc', None, job_body)
        self.assertTrue(started.wait(5))
        self.queue.set_progress(jobs.progress_text('DownloadGeometry', 41.6))
        self.assertEqual(job.to_dict()['message'], 'Downloading · Download geometry 42%')
        release.set()
        self.pump()
        self.assertEqual((job.state, job.progress), (jobs.DONE, ''))
        self.queue.set_progress('late')   # nothing is running: ignored
        self.assertEqual(job.to_dict()['message'], 'Done')
        self.assertEqual(jobs.humanize_step('finalize_exchange'), 'Finalize exchange')
        self.assertEqual(jobs.progress_text('', None), '')

    def test_stop_closes_unfinished_jobs(self):
        cleaned = []

        def job_body(job):
            try:
                yield jobs.WhenIdle('Importing')
            finally:
                cleaned.append(True)

        self.busy = 'busy'
        self.queue.submit('Load', 'Doc', None, job_body)
        self.queue.stop()
        self.assertEqual(cleaned, [True])
        self.queue.poke()


@unittest.skipUnless(_helper_exe(), 'helper not built')
class HelperClientMockTests(unittest.TestCase):
    """The real add-in client against the real helper process, mock data."""

    @classmethod
    def setUpClass(cls):
        cls.client = helper_client.HelperClient(
            _helper_exe(), mock=True, fixtures_dir=os.path.join(REPO, 'tests', 'fixtures'))
        cls.client.ensure_started()

    @classmethod
    def tearDownClass(cls):
        cls.client.stop()

    def test_1_health_and_auth_gate(self):
        self.assertEqual(self.client.health()['mode'], 'mock')
        self.assertTrue(helper_client.helper_is_current(self.client.health()))
        self.assertFalse(helper_client.helper_is_current({'helperVersion': '0.1.0'}))
        self.assertFalse(helper_client.helper_is_current({}))
        self.assertEqual(helper_client.version_tuple('1.10.3-beta'), (1, 10, 3))
        self.assertIsNone(self.client.account())
        with self.assertRaises(helper_client.HelperError) as caught:
            self.client.hubs()
        self.assertEqual(caught.exception.code, 'AUTH_REQUIRED')
        self.assertTrue(caught.exception.correlation_id)

    def test_2_wrong_secret_is_refused(self):
        real = self.client._secret
        self.client._secret = 'wrong'
        try:
            with self.assertRaises(helper_client.HelperError) as caught:
                self.client.hubs()
            self.assertEqual(caught.exception.code, 'UNAUTHORIZED')
        finally:
            self.client._secret = real

    def test_3_load_refresh_publish_flow(self):
        c = self.client
        self.assertEqual(c.login()['displayName'], 'Mock User')
        hub = c.hubs()[0]
        project = c.projects(hub['hubId'])[0]
        folder = c.folders(hub['hubId'], project['projectId'])[0]
        listed = c.exchanges(hub['hubId'], project['projectId'], folder['folderId'])[0]
        exchange = c.resolve(hub['hubId'], project['projectId'], listed['fileUrn'])

        self.assertEqual(c.thumbnail(hub['hubId'], project['projectId'], listed['fileUrn']),
                         {'available': False, 'path': None})

        export = c.export(exchange)
        manifest = export['manifest']
        step = os.path.join(export['outputDirectory'], manifest['exchangeGeometry']['blobRef'])
        self.assertTrue(os.path.getsize(step) > 0)
        stored = doc_meta.loads(doc_meta.dumps(
            doc_meta.build_reference(exchange, manifest['revisionId'], manifest['source'])))

        # No-op refresh, then a new revision appears.
        self.assertFalse(doc_meta.needs_refresh(stored, c.latest_revision(stored['exchange'])['revisionId']))
        self.assertTrue(c.mock_advance(exchange['exchangeId'])['advanced'])
        self.assertTrue(doc_meta.needs_refresh(stored, c.latest_revision(stored['exchange'])['revisionId']))
        self.assertEqual(c.status()['updates'], [])
        with self.assertRaises(helper_client.HelperError) as no_mesh:
            c.export_mesh(exchange)
        self.assertEqual(no_mesh.exception.code, 'UNSUPPORTED_GEOMETRY')
        changes = c.changes(stored['exchange'], stored['importedRevisionId'])
        self.assertEqual((changes['revisionsBehind'], changes['createdCount']), (1, 1))
        self.assertIn('1 added', doc_meta.changes_note(changes))

        # Publish the downloaded STEP as a new exchange, then update it.
        published = c.publish(step, 'fusion:root', 'Bracket', new={
            'hubId': hub['hubId'], 'projectId': project['projectId'], 'folderId': folder['folderId'],
            'name': 'From Fusion'})
        self.assertTrue(published['created'])
        again = c.publish(step, 'fusion:root', 'Bracket', existing=published['exchange'],
                          properties={'Part number': 'P-1'}, replace_owned=True,
                          more_elements=[{'sourceId': 'fusion:c1:1', 'name': 'Motor:1', 'stepPath': step,
                                          'properties': {'Material': 'Steel'}}])
        self.assertEqual((again['elementCount'], again['propertyCount']), (2, 2))
        self.assertNotEqual(published['revision']['revisionId'], again['revision']['revisionId'])

        c.cleanup(export['outputDirectory'])
        self.assertFalse(os.path.isdir(export['outputDirectory']))

        # Delete the published exchange: it is gone for good.
        c.delete_exchange(hub['hubId'], project['projectId'], published['exchange']['fileUrn'])
        with self.assertRaises(helper_client.HelperError) as caught:
            c.latest_revision(published['exchange'])
        self.assertEqual(caught.exception.code, 'NOT_FOUND')

    def test_4_missing_exchange_is_not_found(self):
        with self.assertRaises(helper_client.HelperError) as caught:
            self.client.latest_revision({'exchangeId': 'nope', 'collectionId': 'c', 'hubId': 'h'})
        self.assertEqual(caught.exception.code, 'NOT_FOUND')


    def test_data_only_elements_in_mock_mode_are_labelled(self):
        data = self.client.data_only_elements('mock-hub', 'mock-project', 'urn:mock:file:anything')
        self.assertEqual(data['source'], 'mock')
        self.assertTrue(all(e['name'].startswith('MOCK') for e in data['listed']))
        self.assertEqual(data['total'], len(data['listed']))


class FormaLinkTests(unittest.TestCase):
    LINK = ('https://acc.autodesk.com/docs/files/projects/0A1B2C3D-1111-2222-3333-444455556666'
            '?folderUrn=urn%3Aadsk.wipprod%3Afs.folder%3Aco.folder1'
            '&entityId=urn%3Aadsk.wipprod%3Adm.lineage%3Aitem1&viewModel=detail&moduleId=folders')

    def test_reads_project_folder_and_item(self):
        self.assertEqual(doc_meta.parse_forma_link('  ' + self.LINK + ' '), {
            'projectId': 'b.0a1b2c3d-1111-2222-3333-444455556666',
            'folderUrn': 'urn:adsk.wipprod:fs.folder:co.folder1',
            'fileUrn': 'urn:adsk.wipprod:dm.lineage:item1'})

    def test_round_trips_with_the_link_the_add_in_builds(self):
        found = doc_meta.parse_forma_link(self.LINK)
        self.assertEqual(doc_meta.parse_forma_link(doc_meta.docs_url(found)), found)

    def test_links_use_the_host_of_the_hub_region(self):
        found = doc_meta.parse_forma_link(self.LINK)
        self.assertTrue(doc_meta.docs_url(dict(found, region='EMEA')).startswith('https://acc.autodesk.eu/docs/files/projects/'))
        self.assertTrue(doc_meta.docs_url(dict(found, region='aus')).startswith('https://acc.aus.autodesk.com/'))
        self.assertTrue(doc_meta.docs_url(dict(found, region='Mars')).startswith('https://acc.autodesk.com/'))
        emea = doc_meta.docs_url(dict(found, region='EMEA'))
        self.assertEqual(doc_meta.parse_forma_link(emea), found)
        self.assertTrue(doc_meta.is_forma_link(emea))
        self.assertFalse(doc_meta.is_forma_link('https://acc.autodesk.eu.evil.example/docs'))
        self.assertFalse(doc_meta.is_forma_link(None))
        self.assertEqual(doc_meta.slim_exchange(dict(EXCHANGE, region='EMEA'))['region'], 'EMEA')

    def test_folder_links_and_other_text_are_told_apart(self):
        folder = self.LINK.split('&entityId')[0]
        self.assertIsNone(doc_meta.parse_forma_link(folder)['fileUrn'])
        for text in ('', 'hello', 'http://acc.autodesk.com/docs/files/projects/0a1b2c3d-1111-2222-3333-444455556666',
                     'https://evil.example/docs/files/projects/0a1b2c3d-1111-2222-3333-444455556666',
                     'https://acc.autodesk.com/docs/files/projects/not-a-guid'):
            self.assertIsNone(doc_meta.parse_forma_link(text), text)


class LastFolderTests(unittest.TestCase):
    def test_last_folder_survives_favorite_changes(self):
        location = dict(FavoritesTests.LOCATION)
        with tempfile.TemporaryDirectory() as folder:
            path = favorites.file_path(folder)
            self.assertIsNone(favorites.load_last(path))
            exchange = dict(location, kind='exchange', path=list(location['path']) + ['Some exchange'])
            saved = favorites.save_last(path, exchange)
            self.assertEqual(saved['path'], list(location['path']))
            favorites.add(path, location)
            favorites.remove(path, location)
            self.assertEqual(favorites.load_last(path), saved)
            self.assertEqual(favorites.load(path), [])
            self.assertIsNone(favorites.save_last(path, {'kind': 'hub'}))
            self.assertEqual(favorites.load_last(path), saved)


class ElementLinkTests(unittest.TestCase):
    def test_names_compare_without_the_decorations_each_side_adds(self):
        for text in ('Wall (Right):15', 'Wall (Right)_852F4FD0050AA6441B68E6B9F8592230', 'Wall (Right) v2',
                     'Wall (Right) (1)', 'wall (right):1', '  Wall (Right)  '):
            self.assertEqual(element_link.name_key(text), 'wall (right)', text)
        self.assertEqual(element_link.name_key(None), '')
        self.assertEqual(element_link.body_key('00-Component3_1'), 'component3')
        self.assertEqual(element_link.name_key('00-Component3_1'), '00-component3')
        self.assertEqual(element_link.name_key('Nut, Hex Regular_2'), element_link.name_key('Nut, Hex Regular:13'))

    def test_an_element_finds_every_piece_of_geometry_named_like_it(self):
        components = ['Wall_64E5BBF721EAE4F38066FDADE3D0825C', 'Impact Door_30C70E55C7DC49A340A0A961057D0BE1',
                      'Wall_64E5BBF721EAE4F38066FDADE3D0825C (1)', 'Wall (Left)_21B7ADF41A968CC14B5CB5FDA0078555']
        self.assertEqual(element_link.matching('Wall:14', components), [0, 2])
        self.assertEqual(element_link.matching('Roof:1', components), [])
        self.assertEqual(element_link.matching('', components), [])
        self.assertEqual(element_link.matching('Component3', ['00-Component3_1', 'Box']), [])
        self.assertEqual(element_link.matching('Component3', ['00-Component3_1', 'Box'], bodies=True), [0])

    def test_geometry_finds_its_elements_from_the_most_specific_name_outwards(self):
        elements = [{'name': 'Wall:14'}, {'name': 'Wall:15'}, {'name': 'Impact Door:2'}, {'name': ''}]
        self.assertEqual(element_link.elements_named(
            [('Solid5', True), ('Wall_64E5BBF721EAE4F38066FDADE3D0825C:1', False)], elements), ['Wall:14', 'Wall:15'])
        self.assertEqual(element_link.elements_named([('Solid5', True), ('Roof:1', False)], elements), [])
        self.assertEqual(element_link.elements_named([('00-Impact Door_1', True)], elements), ['Impact Door:2'])
        self.assertEqual(element_link.elements_named([], None), [])


class DataOnlyTests(unittest.TestCase):
    def test_recognises_the_service_refusal_and_the_helper_code(self):
        self.assertTrue(data_only.is_error('DATA_ONLY_EXCHANGE', ''))
        self.assertTrue(data_only.is_error(
            'INTERNAL_ERROR', 'Could not get element data model: This API does not support AECDM based exchange.'))
        self.assertFalse(data_only.is_error('INTERNAL_ERROR', 'The request is invalid.'))
        self.assertFalse(data_only.is_error('NOT_FOUND', None))

    def test_record_keeps_where_it_is_and_a_capped_element_list(self):
        data = {'name': 'Levels', 'total': 500, 'source': 'aecdm', 'listed': [
            {'name': 'Level 1', 'category': 'Levels', 'properties': {'Elevation': '0 mm', 'Empty': ''}}, 'junk']}
        record = doc_meta.build_data_only(dict(EXCHANGE, region='EMEA'), data, 'hub-1', 'b.p', 'urn:x', now='2026-11-23T00:00:00Z')
        self.assertEqual(record['exchangeId'], 'ex-1')
        self.assertEqual(record['exchange']['region'], 'EMEA')
        self.assertNotIn('extra', record['exchange'])
        self.assertEqual(record['elements'], {'total': 500, 'listed': [
            {'name': 'Level 1', 'category': 'Levels', 'properties': {'Elevation': '0 mm'}}]})
        stored = {}
        for index in range(doc_meta.MAX_DATA_ONLY + 2):
            other = dict(record, exchangeId='ex-%d' % index, readAtUtc='2026-11-%02dT00:00:00Z' % (index + 1))
            stored = doc_meta.merge_data_only(stored, other)
        self.assertEqual(len(stored), doc_meta.MAX_DATA_ONLY)
        self.assertNotIn('ex-0', stored)
        self.assertIn('ex-%d' % (doc_meta.MAX_DATA_ONLY + 1), stored)

    def test_remembers_exchanges_per_file(self):
        with tempfile.TemporaryDirectory() as folder:
            path = data_only.file_path(os.path.join(folder, 'nested'))
            self.assertEqual(data_only.load(path), {})
            data_only.add(path, 'urn:a', 'Levels and grids')
            data_only.add(path, 'urn:b', None)
            data_only.add(path, '', 'ignored')
            self.assertEqual(data_only.load(path), {'urn:a': 'Levels and grids', 'urn:b': ''})
            self.assertTrue(data_only.file_path(folder, mock=True).endswith('data-only.mock.json'))


if __name__ == '__main__':
    unittest.main()
