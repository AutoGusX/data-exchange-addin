"""Background job queue for Load, Refresh and Publish. No Fusion imports, so it runs under plain tests.

A job is a generator that always runs on the main thread. It hands work back by yielding:

    value = yield Background('Downloading', lambda: client.export(...))   # runs on a worker thread
    yield WhenIdle('Importing into Fusion')                               # resumes when Fusion is free

Network work never blocks Fusion. Document work runs between yields, on the main thread, and only
after the gate says the job's document is active and no other command is running. The value a job
returns is its result text; an exception fails the job.
"""
import itertools
import re
import threading
import time

RUNNING, WAITING, DONE, FAILED = 'running', 'waiting', 'done', 'failed'


def humanize_step(step_id):
    """'DownloadGeometry' or 'finalize_exchange' as words: 'Download geometry', 'Finalize exchange'."""
    words = re.sub(r'(?<=[a-z0-9])(?=[A-Z])|[_\-.]+', ' ', str(step_id or '')).split()
    return ' '.join(words).capitalize()


def progress_text(step_id, percent):
    step = humanize_step(step_id)
    try:
        amount = '%d%%' % max(0, min(100, round(float(percent))))
    except (TypeError, ValueError):
        amount = ''
    return ' '.join(part for part in (step, amount) if part)


class Background:
    def __init__(self, label, work):
        self.label = label
        self.work = work


class WhenIdle:
    def __init__(self, label):
        self.label = label


class Job:
    def __init__(self, job_id, title, detail, context):
        self.id = job_id
        self.title = title
        self.detail = detail      # e.g. the document name
        self.context = context    # opaque to the queue (the Fusion document)
        self.state = RUNNING
        self.message = 'Queued'
        self.progress = ''        # live detail for the running stage, e.g. 'Download geometry 40%'
        self.result = ''
        self.link = None          # optional web page for the result (shown as a button)
        self.started = time.time()
        self.finished = None
        self.can_continue = False  # set by the gate: waiting only on a busy command
        self.force = False        # the user chose "Continue now": skip the busy-command check once
        self._generator = None
        self._idle_label = None

    @property
    def is_finished(self):
        return self.state in (DONE, FAILED)

    def to_dict(self):
        end = self.finished if self.finished is not None else time.time()
        message = self.message + (' · ' + self.progress if self.progress and self.state == RUNNING else '')
        return {'id': self.id, 'title': self.title, 'detail': self.detail, 'state': self.state,
                'message': message, 'result': self.result, 'link': self.link,
                'canContinue': self.state == WAITING and self.can_continue, 'seconds': round(end - self.started, 1)}


class JobQueue:
    """All methods except the worker thread body must be called on the main thread.

    post_to_main(callable): schedule a callable on the main thread (thread-safe).
    gate(job): None when the job's document work may run now, else a short reason to wait;
               raising fails the job (for example the document was closed).
    describe(error): user-facing text for an exception.
    on_change(): called on the main thread whenever the job list changes.
    """

    def __init__(self, post_to_main, gate, describe=str, on_change=None, lock=None):
        self._post = post_to_main
        self._gate = gate
        self._describe = describe
        self._on_change = on_change or (lambda: None)
        self._lock = lock or threading.Lock()   # network work runs one request at a time
        self._ids = itertools.count(1)
        self.jobs = []
        self.stopped = False
        self._working = None    # the job whose network work holds the lock right now

    # ---- public ------------------------------------------------------------------------------

    def submit(self, title, detail, context, factory):
        """factory(job) returns the job generator. The job starts straight away."""
        job = Job(next(self._ids), title, detail, context)
        self.jobs.append(job)
        job._generator = factory(job)
        self._advance(job)
        return job

    def poke(self):
        """Something changed (document activated, command ended): retry jobs waiting for Fusion."""
        for job in [j for j in self.jobs if j.state == WAITING]:
            self._try_idle(job)

    def continue_now(self, job_id):
        for job in self.jobs:
            if job.id == job_id and job.state == WAITING:
                job.force = True
                self._try_idle(job)

    def clear_finished(self):
        self.jobs = [j for j in self.jobs if not j.is_finished]
        self._on_change()

    def set_progress(self, text):
        """Show live detail on the job whose network work is running. Main thread."""
        job = self._working
        if job is not None and job.state == RUNNING and job.progress != text:
            job.progress = text
            self._on_change()

    def active_count(self):
        return sum(1 for j in self.jobs if not j.is_finished)

    def snapshot(self):
        return [j.to_dict() for j in reversed(self.jobs)]

    def stop(self):
        """Add-in shutdown: finish nothing more. Generators are closed so their cleanup runs."""
        self.stopped = True
        for job in self.jobs:
            if not job.is_finished and job._generator is not None:
                try:
                    job._generator.close()
                except Exception:
                    pass
        self.jobs = []

    # ---- driving a job -----------------------------------------------------------------------

    def _advance(self, job, value=None, error=None):
        if self.stopped or job.is_finished:
            return
        job.progress = ''
        try:
            request = job._generator.throw(error) if error is not None else job._generator.send(value)
        except StopIteration as finished:
            self._finish(job, DONE, finished.value)
            return
        except Exception as e:
            self._finish(job, FAILED, self._describe(e))
            return

        if isinstance(request, Background):
            job.state, job.message = RUNNING, request.label
            self._on_change()
            self._start_worker(job, request.work)
        elif isinstance(request, WhenIdle):
            job._idle_label = request.label
            self._try_idle(job)
        else:
            self._advance(job, error=TypeError('A job yielded something that is not a queue request.'))

    def _try_idle(self, job):
        try:
            reason = self._gate(job)
        except Exception as e:
            job.state = WAITING  # so the failure path below sees an unfinished job
            self._fail_from_outside(job, e)
            return
        if reason:
            job.state, job.message = WAITING, reason
            self._on_change()
            return
        job.force = False
        job.state, job.message = RUNNING, job._idle_label
        self._on_change()
        self._advance(job)

    def _fail_from_outside(self, job, error):
        # Throw into the generator so its finally blocks (temp file cleanup) still run.
        self._advance(job, error=error)

    def _start_worker(self, job, work):
        def target():
            try:
                with self._lock:
                    self._working = job
                    try:
                        outcome = (work(), None)
                    finally:
                        self._working = None
            except BaseException as e:
                outcome = (None, e)
            self._post(lambda: self._advance(job, *outcome))

        threading.Thread(target=target, daemon=True).start()

    def _finish(self, job, state, text):
        job.state = state
        job.result = text or ''
        job.message = 'Done' if state == DONE else 'Failed'
        job.finished = time.time()
        self._on_change()
