"""Single-process temporary store. Locks serialize PDF/OCR jobs and mutations."""
from contextlib import contextmanager
from pathlib import Path
from threading import RLock
import shutil
import tempfile
import time
from ..models import Session


class Store:
    def __init__(self, ttl=1800, root=None, maximum=20):
        self.root = Path(tempfile.mkdtemp(prefix='privacy-', dir=root))
        self.root.chmod(0o700)
        self.ttl, self.maximum = ttl, maximum
        self.items = {}
        self.lock = RLock()

    def cleanup(self):
        with self.lock:
            for sid, (_, touched) in list(self.items.items()):
                if time.monotonic() - touched > self.ttl:
                    self.delete(sid)

    def create(self):
        with self.lock:
            self.cleanup()
            if len(self.items) >= self.maximum:
                raise ValueError('Session limit reached; delete an unused session')
            session = Session()
            (self.root / session.id).mkdir(mode=0o700)
            self.items[session.id] = (session, time.monotonic())
            return session

    @contextmanager
    def access(self, sid):
        with self.lock:
            self.cleanup()
            if sid not in self.items:
                raise KeyError('Session not found or expired')
            session, _ = self.items[sid]
            self.items[sid] = (session, time.monotonic())
            yield session, self.root / sid
            # Long OCR operations count as activity, not idle time.
            if sid in self.items:
                self.items[sid] = (session, time.monotonic())

    def delete(self, sid):
        with self.lock:
            self.items.pop(sid, None)
            shutil.rmtree(self.root / sid, ignore_errors=True)

    def close(self):
        with self.lock:
            shutil.rmtree(self.root, ignore_errors=True)
            self.items.clear()


def invalidate(session, path):
    session.status = 'review'
    session.includes_mapping = False
    session.warnings = []
    (path / 'result.zip').unlink(missing_ok=True)
    (path / 'mapping.json').unlink(missing_ok=True)
